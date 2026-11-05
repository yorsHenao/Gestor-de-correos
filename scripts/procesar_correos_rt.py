import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import storage
BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")
from email.utils import parseaddr
from googleapiclient.discovery import build
from app.alertas import alertar
from autenticar import autenticar
from agentes.agente_extractor_rt import AgenteExtractorRT
from consultar_lupe import consultar_lupe
from clasificar_correos_gmail import (
    CONFIANZA_MINIMA,
    LABEL_MANUAL,
    LABEL_RT,
    _es_consulta_mismo_caso,
    _es_cuenta_desactivada_o_eliminada,
    _es_recepcion_hub,
    _es_reclamo_aliado_operativo,
    _obtener_peticionario,
    _rt_sin_cola_operativa,
)
from agentes.agente_clasificador import clasificar_correo
from entes.correo import _fecha_peticion
from gmail_cuerpo import extraer_contenido, mensaje_tiene_pdf_o_word
from flow_control import DetenerFlujo, comprobar_detener
from lupe_navegador import SesionLupe, salir_por_lupe
from utils.exceptions import LupeOcupado, LupeSesionRequerida, es_infraestructura
from utils.logger import log_caso, obtener_log, resumen, resumir_error
import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)

log = obtener_log("rt")

_NO_RT = {
    "eliminar_deuda",
    "entes",
    "facturacion",
    "otro",
    "vuelo",
    "reclamo_aliado",
}
MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_en_espanol(iso):
    if not iso:
        return "Sin fecha"
    anio, mes, dia = (int(parte) for parte in iso.split("-"))
    return f"{dia} de {MESES_ES[mes - 1]} de {anio}"


def _truncar(texto, max_len=100):
    texto = " ".join(str(texto or "").split())
    if len(texto) <= max_len:
        return texto
    return texto[: max_len - 1].rstrip() + "…"


def _es_rt_operable(asunto, cuerpo, adjuntos=""):
    """False solo ante un falso positivo claro. Si la IA falla o duda, sigue como RT."""
    texto = f"{asunto}\n{cuerpo}\n{adjuntos}"
    if _es_consulta_mismo_caso(texto):
        return False, "coordinación mismo caso — revisión manual"
    if _es_cuenta_desactivada_o_eliminada(texto):
        return False, "cuenta desactivada o eliminada — revisión manual"
    if _es_reclamo_aliado_operativo(texto):
        return False, "aliado operativo — reclamo_aliado"
    try:
        resultado = clasificar_correo(asunto, cuerpo, adjuntos=adjuntos)
    except Exception:
        return True, ""
    categorias = resultado.get("categorias") or []
    confianza = resultado.get("confianza", 0)
    if not categorias:
        if _es_reclamo_aliado_operativo(texto):
            return False, "aliado operativo — reclamo_aliado"
        if _rt_sin_cola_operativa(texto):
            return False, "acceso o incentivos — revisión manual"
        return True, ""
    if categorias == ["entes"] and _es_reclamo_aliado_operativo(texto):
        return False, "aliado operativo — reclamo_aliado"
    if (
        len(categorias) == 1
        and categorias[0] in _NO_RT
        and confianza >= CONFIANZA_MINIMA
    ):
        return False, f"{categorias[0]} — revisión manual"
    return True, ""


def _buscar_duplicado(id_rt, vistos):
    """Caso del mismo RT ya gestionado: (nombre, estado, regla). None si hay que procesarlo."""
    if not id_rt:
        return None
    if id_rt in vistos:
        return vistos[id_rt]
    try:
        return storage.buscar_duplicado_rt(id_rt)
    except Exception:
        log.warning("No se pudo revisar duplicados del RT %s; sigue el flujo normal", id_rt)
        return None


def _recordar_caso(nombre_archivo, vistos):
    """Recuerda el caso recién gestionado por si otra petición del mismo RT llega en la ronda."""
    try:
        referencia = storage.referencia_duplicado_rt(nombre_archivo)
    except Exception:
        return
    if referencia:
        id_rt, estado, regla = referencia
        vistos[id_rt] = (nombre_archivo, estado, regla)


def _anotar_duplicado(nombre_referencia, datos_correo):
    """Deja el correo repetido en el expediente del caso que ya cubre a ese RT."""
    informacion = storage.leer_caso(nombre_referencia)
    duplicados = informacion["correo"].setdefault("duplicados", [])
    if any(d.get("message_id") == datos_correo["message_id"] for d in duplicados):
        return
    duplicados.append({
        "message_id": datos_correo["message_id"],
        "thread_id_gmail": datos_correo["thread_id_gmail"],
        "asunto": datos_correo["asunto"],
        "fecha_recibido": datos_correo["fecha_recibido"],
    })
    storage.guardar_caso(nombre_referencia, informacion)


def _enviar_a_revision_manual(servicio, gmail_id, razon, correo):
    body = {"removeLabelIds": [LABEL_RT]}
    if LABEL_MANUAL:
        body["addLabelIds"] = [LABEL_MANUAL]
    servicio.users().messages().modify(
        userId="me",
        id=gmail_id,
        body=body,
    ).execute()
    log_caso(log, correo).warning(
        "No es RT operable (%s): a revisión manual, sigue sin leer", razon
    )


def procesar_correos_rt():
    """Procesa la cola RT con un solo navegador de Lupe. Devuelve los casos con error."""
    with SesionLupe(paso="rt") as lupe:
        return _procesar_cola(lupe)


def _procesar_cola(lupe):
    try:
        storage.sincronizar_cierres_qa()
    except Exception as error:
        log.warning("No se pudo leer el cierre de QA en el Sheet: %s", error)
    extractor = AgenteExtractorRT()
    errores = 0
    creds = autenticar()
    servicio = build(
        "gmail", "v1", credentials=creds
    )

    resultado = (
        servicio.users()
        .messages()
        .list(
            userId="me",
            labelIds=[os.getenv("GMAIL_LABEL_ID"), "UNREAD"],
        )
        .execute()
    )

    mensajes = resultado.get(
        "messages", []
    )

    log.info("Cola RT: %d correo(s) sin leer", len(mensajes))
    contadores = {
        "procesados": 0,
        "completados": 0,
        "revision_manual": 0,
        "errores": 0,
        "duplicados": 0,
        "pausa_ia": 0,
    }
    duplicados_vistos = {}
    if not mensajes:
        resumen(log, **contadores)
        return 0

    for mensaje in mensajes:
        comprobar_detener("Parada antes del siguiente correo RT.")
        contadores["procesados"] += 1
        try:
            msg = (
                servicio.users()
                .messages()
                .get(
                    userId="me",
                    id=mensaje["id"],
                    format="full",
                )
                .execute()
            )
        except Exception as error:
            errores += 1
            log.error("No se pudo leer el correo %s; queda sin leer para la siguiente ronda: %s", mensaje["id"], error)
            continue

        headers = msg["payload"]["headers"]
        message_id = next((h["value"] for h in headers if h["name"] == "Message-ID"), "")
        thread_id_gmail = msg["threadId"]
        asunto = next(
            (h["value"] for h in headers if h["name"] == "Subject"), "Sin asunto"
        )
        remitente = next(
            (h["value"] for h in headers if h["name"] == "From"), "Desconocido"
        )
        remitente_nombre, remitente_email = parseaddr(remitente)
        if not remitente_email:
            remitente_email = remitente
        if not remitente_nombre:
            remitente_nombre = remitente_email
        if _es_recepcion_hub(remitente_email):
            _enviar_a_revision_manual(
                servicio,
                mensaje["id"],
                "recepción hub — revisión manual",
                remitente_email,
            )
            continue
        fecha_raw = next((h["value"] for h in headers if h["name"] == "Date"), None)

        try:
            cuerpo, adjuntos = extraer_contenido(
                msg, servicio, incluir_adjuntos=mensaje_tiene_pdf_o_word(msg)
            )
        except Exception as error:
            errores += 1
            log_caso(log, remitente_email).error("No se pudo leer el contenido del correo; queda sin leer: %s", error)
            continue
        fecha = _fecha_en_espanol(_fecha_peticion(cuerpo, fecha_raw))

        _, correo_peticionario = _obtener_peticionario(
            remitente, f"{cuerpo}\n{adjuntos}"
        )
        if _es_recepcion_hub(correo_peticionario):
            _enviar_a_revision_manual(
                servicio,
                mensaje["id"],
                "recepción hub — revisión manual",
                correo_peticionario,
            )
            continue
        es_rt, razon = _es_rt_operable(asunto, cuerpo, adjuntos)
        if not es_rt:
            _enviar_a_revision_manual(
                servicio, mensaje["id"], razon, correo_peticionario
            )
            continue

        texto_extraer = f"{cuerpo} {adjuntos}".strip()

        try:
            resultado_ia = extractor.extraer(asunto, texto_extraer)
        except Exception as error:
            if es_infraestructura(error):
                contadores["procesados"] -= 1
                contadores["pausa_ia"] = len(mensajes) - contadores["procesados"]
                detalle = resumir_error(error)
                log_caso(log, correo_peticionario or remitente_email).warning(
                    "Paso RT en pausa por la IA; %d correo(s) siguen sin leer: %s",
                    contadores["pausa_ia"],
                    detalle,
                )
                alertar(
                    "rt_infra",
                    "Paso RT pausado por la IA",
                    f"{detalle}. Los correos se reintentarán en la siguiente ronda.",
                )
                break
            errores += 1
            log_caso(log, correo_peticionario or remitente_email).error(
                "IA no disponible para extraer el caso; el correo sigue sin leer para la siguiente ronda: %s", error
            )
            continue

        datos = {
            "completado": False,
            "correo": {
                "asunto": asunto,
                "correo_rt": resultado_ia.get("correo_rt", ""),
                "correo_peticionario": resultado_ia.get("correo_peticionario") or "",
                "nombre_peticionario": resultado_ia.get("nombre_peticionario") or remitente_nombre,
                "nombre_rt": resultado_ia.get("nombre_rt", ""),
                "remitente_email": remitente_email,
                "fecha_recibido": fecha,
                "peticion": _truncar(resultado_ia.get("peticion") or "", 200),
                "telefono": resultado_ia.get("telefono", ""),
                "id_rt": resultado_ia.get("id_rt", ""),
                "numero_documento": resultado_ia.get("numero_documento", ""),
                "tipo_representacion": resultado_ia.get("tipo_representacion", ""),
                "message_id": message_id,
                "thread_id_gmail": thread_id_gmail,
            },
            "lupe": {"nombre": "", "id": "", "regla_bloqueo": ""},
            "slack": {
                "decision": "",
                "causal_elegida": "",
                "foto_prueba": {"aplica": False},
                "thread_id": "",
                "estado": "",
            },
        }

        # Otra petición del mismo RT con la regla de bloqueo ya conocida: ni Lupe ni respuesta de nuevo.
        id_rt = str(datos["correo"]["id_rt"] or "").strip()
        duplicado = _buscar_duplicado(id_rt, duplicados_vistos)
        if duplicado:
            nombre_referencia, estado_referencia, regla_referencia = duplicado
            duplicados_vistos[id_rt] = duplicado
            _anotar_duplicado(nombre_referencia, datos["correo"])
            storage.marcar_leido(mensaje["id"], servicio)
            log_caso(log, nombre_referencia).info(
                "Duplicado del RT %s (%s, regla %s): no se repite la gestión; correo marcado leído",
                id_rt,
                estado_referencia,
                regla_referencia,
            )
            contadores["duplicados"] += 1
            continue

        mensaje_id = mensaje["id"]
        correo_para_nombre = resultado_ia.get("correo_peticionario") or resultado_ia.get("correo_rt") or remitente_email
        datos["correo"]["gmail_id"] = mensaje_id

        page = lupe.pagina()
        storage.guardar_caso(mensaje_id, datos)

        if datos["correo"].get("id_rt"):
            search_by = "ID RT"
            search_value = datos["correo"]["id_rt"]
        elif datos["correo"].get("numero_documento"):
            search_by = "Cédula"
            search_value = datos["correo"]["numero_documento"]
        elif datos["correo"].get("correo_peticionario"):
            search_by = "Correo"
            search_value = datos["correo"]["correo_peticionario"]
        elif datos["correo"].get("telefono"):
            search_by = "Teléfono"
            search_value = datos["correo"]["telefono"]
        else:
            search_by = None
            search_value = ""

        clog = log_caso(log, correo_para_nombre or mensaje_id)
        clog.info("Caso creado; búsqueda en Lupe por %s", search_by or "ningún dato")

        try:
            resultado_lupe = consultar_lupe(mensaje_id, page=page)
        except (DetenerFlujo, LupeSesionRequerida, LupeOcupado):
            raise
        except Exception:
            errores += 1
            clog.exception("Falló la consulta en Lupe; el correo sigue sin leer para la siguiente ronda")
            continue

        if resultado_lupe == "revision_manual":
            _enviar_a_revision_manual(
                servicio,
                mensaje["id"],
                "cuenta anonimizada en Lupe",
                correo_peticionario,
            )
            continue

        servicio.users().messages().modify(
            userId="me",
            id=mensaje["id"],
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        contadores["completados"] += 1
        _recordar_caso(mensaje_id, duplicados_vistos)

    contadores["errores"] = errores
    contadores["revision_manual"] = (
        contadores["procesados"]
        - contadores["completados"]
        - contadores["duplicados"]
        - errores
    )
    resumen(log, **contadores)
    return errores


if __name__ == "__main__":
    try:
        if procesar_correos_rt():
            sys.exit(1)
    except DetenerFlujo:
        pass
    except (LupeSesionRequerida, LupeOcupado) as error:
        salir_por_lupe(error)
