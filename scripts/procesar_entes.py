import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
for ruta in (str(SCRIPTS), str(ROOT)):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

from googleapiclient.discovery import build

import storage
from agentes.agente_entes import AgenteEntes
from app import registro
from app.alertas import alertar
from autenticar import autenticar
from config import (
    ENTES_MAX_CORREOS,
    ENTES_MAX_FALLOS_RONDA,
    ENTES_MAX_INTENTOS,
    GMAIL_LABEL_ENTES,
    GMAIL_LABEL_REVISION_MANUAL,
)
from entes import formulario, hoja
from entes.correo import correo_agente, leer_mensaje, listar_ids_entes
from entes.imagenes import completar_con_imagenes
from entes.lupe import consultar_cuenta
from entes.pedir_info import pedir_informacion
from flow_control import DetenerFlujo, comprobar_detener
from gmail_cuerpo import bytes_parte
from lupe_navegador import SesionLupe, salir_por_lupe
from utils.exceptions import (
    ErrorInfraestructura,
    LupeOcupado,
    LupeSesionRequerida,
    es_infraestructura,
)
from utils.logger import (
    log_caso,
    log_separador,
    obtener_log,
    resumir_error,
    resumen,
)

log = obtener_log("entes")

_NO_CAPTURAR = (DetenerFlujo, LupeSesionRequerida, LupeOcupado)

COMPLETADO = "completados"
REVISION = "revision_manual"
ERROR = "errores"
PEDIR_INFO = "pedir_info"
SIN_CORREO = "sin_correo"
OMITIDO = "omitido"


def procesar_entes():
    servicio = build("gmail", "v1", credentials=autenticar())
    ids = listar_ids_entes(servicio, ENTES_MAX_CORREOS)
    log.info("Cola entes: %d correo(s) sin leer", len(ids))
    contadores = {
        "procesados": 0,
        COMPLETADO: 0,
        REVISION: 0,
        ERROR: 0,
        PEDIR_INFO: 0,
        SIN_CORREO: 0,
    }
    if not ids:
        return resumen(log, **contadores)

    contexto = {
        "servicio": servicio,
        "agente": AgenteEntes(),
        "correo_agente": correo_agente(servicio),
    }
    fallos_ronda = 0
    try:
        indice = hoja.indice()
    except Exception as error:
        log.warning("No se pudo leer el Sheet de Entes; se sigue con los correos: %s", error)
        indice = {}
    with SesionLupe(paso="entes") as lupe, tempfile.TemporaryDirectory() as carpeta:
        contexto["lupe"] = lupe
        contexto["indice"] = indice
        for mensaje_id in ids:
            comprobar_detener("Parada solicitada entre casos de Entes.")
            log_separador(log)
            contadores["procesados"] += 1
            carpeta_caso = Path(carpeta) / mensaje_id
            carpeta_caso.mkdir(parents=True, exist_ok=True)
            try:
                resultado = _procesar_mensaje(
                    contexto, mensaje_id, carpeta_caso, indice.get(mensaje_id)
                )
            except ErrorInfraestructura as error:
                contadores[ERROR] += 1
                detalle = error.details or resumir_error(error.__cause__ or error)
                log.warning(
                    "Entes en pausa por infraestructura; los correos siguen sin leer: %s",
                    detalle,
                )
                alertar(
                    "entes_infra",
                    "Entes pausado por infraestructura",
                    f"{detalle}. Los correos se reintentarán en la siguiente ronda.",
                )
                break
            if resultado in contadores:
                contadores[resultado] += 1
            if resultado in (ERROR, REVISION):
                fallos_ronda += 1
            if fallos_ronda > ENTES_MAX_FALLOS_RONDA:
                pendientes = len(ids) - contadores["procesados"]
                log.warning(
                    "Entes en pausa por esta ronda: %d fallos; %d correo(s) quedan para la siguiente",
                    fallos_ronda,
                    pendientes,
                )
                alertar(
                    "entes_pausa",
                    "Entes pausado en esta ronda",
                    f"{fallos_ronda} fallos en la ronda. Quedan {pendientes} correo(s) sin procesar.",
                )
                break
    return resumen(log, **contadores)


def _procesar_mensaje(contexto, mensaje_id, carpeta, fila):
    clog = log_caso(log, mensaje_id)
    hueco = registro.leer_hueco(mensaje_id)
    if hueco:
        return _cerrar_hueco(mensaje_id, hueco, clog)
    if fila:
        accion = hoja.accion_fila(fila.get("estado"), fila.get("completado"))
        if accion == "cerrar":
            storage.marcar_leido(mensaje_id)
            clog.info("El Sheet ya cerró el caso; correo marcado leído")
            return COMPLETADO
        if accion == "quieto":
            clog.info("Estado «%s» en el Sheet; no se vuelve a procesar", fila.get("estado"))
            return OMITIDO

    servicio = contexto["servicio"]
    try:
        mensaje = leer_mensaje(servicio, mensaje_id, clog)
        datos = contexto["agente"].extraer(**_para_agente(mensaje))
    except _NO_CAPTURAR:
        raise
    except Exception as error:
        _elevar_si_infra(error)
        clog.exception("No se pudo leer o extraer el correo; sigue sin leer")
        return _fallo(contexto, mensaje_id, {"mensaje_id": mensaje_id}, f"Extracción: {error}", clog)

    correo = str(datos.get("correo_peticionario") or "").strip()
    clog = log_caso(log, correo or mensaje_id)
    if not correo:
        registro_caso = _registro(datos, _lupe_vacio("sin_correo"), mensaje)
        if hoja.cerrar_caso(registro_caso, hoja.ESTADO_SIN_CORREO, clog):
            clog.warning("Sin correo del peticionario: queda leído para gestión manual")
            return SIN_CORREO
        return ERROR

    if datos.get("busqueda") == "orden" and not _esencial(datos):
        completar_con_imagenes(servicio, mensaje, datos, clog)

    lupe = _resolver_busqueda(contexto["lupe"], datos, clog)
    registro_caso = _registro(datos, lupe, mensaje)
    if lupe.get("rama") == "error":
        return _fallo(contexto, mensaje_id, registro_caso, f"Lupe: {lupe.get('detalle')}", clog)

    campos = formulario.armar_formulario(
        datos, lupe, mensaje.get("pdfs") or [], contexto["correo_agente"]
    )
    registro_caso["id_usuario"] = lupe.get("id_usuario") or campos.get("id_usuario") or "0"
    if not campos.get("llenar"):
        peticion = {**mensaje, **datos, "formulario": {"registro": registro_caso}}
        resultado_pedido = pedir_informacion(servicio, peticion, clog)
        if resultado_pedido:
            modo_pedido, cerrado = resultado_pedido
            if not cerrado:
                registro.guardar_hueco(mensaje_id, registro_caso, f"pedir_info_{modo_pedido}")
            return PEDIR_INFO
        return _fallo(contexto, mensaje_id, registro_caso, "No se pudo pedir la información", clog)

    playwright = contexto["lupe"].playwright()
    try:
        elegidos, omitidos = formulario.preparar_anexos(
            playwright,
            mensaje,
            datos,
            carpeta,
            lambda parte: bytes_parte(servicio, mensaje_id, parte),
        )
    except Exception as error:
        _elevar_si_infra(error)
        clog.exception("No se pudieron preparar los anexos")
        return _fallo(contexto, mensaje_id, registro_caso, f"Anexos: {error}", clog)
    campos["archivos"] = elegidos
    campos["archivos_omitidos"] = omitidos
    if omitidos:
        clog.warning("Anexos omitidos: %s", ", ".join(o["nombre"] for o in omitidos))

    clog.info(
        "Formulario: rama %s, orden %s, usuario %s, causal %s, %d anexo(s)",
        lupe.get("rama"),
        campos["id_orden"],
        campos["id_usuario"],
        registro_caso["causal"],
        len(elegidos),
    )
    envio = _enviar_con_reintento(playwright, campos, clog)
    if envio["estado"] == formulario.ENVIO_OK:
        registro.guardar_hueco(mensaje_id, registro_caso, "enviado")
        return _cerrar_enviado(mensaje_id, registro_caso, clog)
    if envio["estado"] == formulario.ENVIO_INCIERTO:
        return _a_revision(
            contexto,
            mensaje_id,
            registro_caso,
            f"Envío incierto, no se reintenta para no duplicar: {envio['detalle']}",
            clog,
        )
    return _fallo(contexto, mensaje_id, registro_caso, envio["detalle"], clog)


def _enviar_con_reintento(playwright, campos, clog):
    """Un reintento inmediato solo si el fallo fue antes de enviar o el formulario lo rechazó."""
    envio = formulario.enviar_formulario(playwright, campos)
    if envio["estado"] == formulario.ENVIO_FALLO:
        _elevar_si_infra(envio["detalle"])
        clog.warning("Formulario falló (%s); reintento inmediato", envio["detalle"])
        envio = formulario.enviar_formulario(playwright, campos)
        if envio["estado"] == formulario.ENVIO_FALLO:
            _elevar_si_infra(envio["detalle"])
    return envio


def _cerrar_hueco(mensaje_id, hueco, clog):
    registro_caso = {
        "peticionario": hueco.get("peticionario") or "",
        "correo": hueco.get("correo") or "",
        "id_usuario": hueco.get("id_usuario") or "0",
        "fecha_peticion": hueco.get("fecha_peticion") or "",
        "causal": hueco.get("causal") or "Otro",
        "mensaje_id": mensaje_id,
    }
    motivo = hueco.get("motivo") or ""
    if motivo == "pedir_info_borrador":
        cerrado = hoja.anotar_estado(registro_caso, hoja.ESTADO_MAS_INFO, clog)
    elif motivo == "pedir_info_enviado":
        cerrado = hoja.cerrar_caso(registro_caso, hoja.ESTADO_MAS_INFO_ENVIADO, clog)
    else:
        cerrado = False
        resultado = _cerrar_enviado(mensaje_id, registro_caso, clog)
        if resultado == COMPLETADO:
            return COMPLETADO
        return ERROR
    if cerrado:
        registro.borrar_hueco(mensaje_id)
        clog.info("Pedido de información ya preparado; no se crea otro correo")
    return PEDIR_INFO


def _cerrar_enviado(mensaje_id, registro_caso, clog):
    if hoja.cerrar_caso(registro_caso, hoja.ESTADO_ENVIADO_ENTES, clog):
        registro.borrar_hueco(mensaje_id)
        clog.info("Formulario enviado a Entes; estado actualizado y correo leído")
        return COMPLETADO
    clog.warning("Formulario enviado, pero falta cerrar el caso; se intentará en la próxima ronda")
    return ERROR


def _fallo(contexto, mensaje_id, registro_caso, detalle, clog):
    fila = (contexto.get("indice") or {}).get(mensaje_id) or {}
    intentos = hoja.numero_reintento(fila.get("estado")) + 1
    if intentos >= ENTES_MAX_INTENTOS:
        return _a_revision(
            contexto, mensaje_id, registro_caso, f"{intentos} intentos fallidos. Último: {detalle}", clog
        )
    if registro_caso.get("mensaje_id"):
        hoja.anotar_estado(registro_caso, hoja.estado_reintento(intentos), clog)
        indice = contexto.setdefault("indice", {})
        fila = dict(indice.get(mensaje_id) or {})
        fila["estado"] = hoja.estado_reintento(intentos)
        indice[mensaje_id] = fila
    clog.warning(
        "Fallo (intento %d de %d), sigue sin leer para la próxima ronda: %s",
        intentos,
        ENTES_MAX_INTENTOS,
        detalle,
    )
    return ERROR


def _a_revision(contexto, mensaje_id, registro_caso, motivo, clog):
    try:
        storage.cambiar_etiquetas(
            mensaje_id,
            agregar=[GMAIL_LABEL_REVISION_MANUAL],
            quitar=[GMAIL_LABEL_ENTES],
            servicio=contexto["servicio"],
        )
    except Exception as error:
        clog.error("No se pudo poner la etiqueta Revisión manual: %s", error)
    if registro_caso.get("correo"):
        hoja.anotar_estado(registro_caso, hoja.ESTADO_REVISION, clog)
    registro.borrar_hueco(mensaje_id)
    clog.warning("A revisión manual (sigue sin leer): %s", motivo)
    alertar(
        "entes_revision",
        "Caso de Entes a revisión manual",
        f"{registro_caso.get('correo') or mensaje_id}: {motivo}",
    )
    return REVISION


def _para_agente(mensaje):
    return {
        "asunto": mensaje["asunto"],
        "cuerpo": mensaje["cuerpo"],
        "remitente": mensaje["remitente"],
        "fecha_correo": mensaje["fecha_llegada_correo"],
        "adjuntos": mensaje["adjuntos"],
        "texto_pdf": mensaje.get("texto_pdf", ""),
    }


def _registro(datos, lupe, mensaje):
    return {
        "peticionario": datos.get("nombre_peticionario") or "",
        "correo": datos.get("correo_peticionario") or "",
        "id_usuario": (lupe or {}).get("id_usuario") or "0",
        "fecha_peticion": datos.get("fecha_llegada_correo") or "",
        "causal": datos.get("causal") or "Otro",
        "mensaje_id": mensaje["id"],
    }


def _esencial(datos):
    if str(datos.get("id_orden_en_peticion") or "").strip():
        return True
    return bool(
        str(datos.get("fecha_orden") or "").strip()
        and str(datos.get("nombre_comercio") or "").strip()
    )


def _resolver_busqueda(lupe, datos, clog):
    busqueda = datos.get("busqueda") or "cuenta"
    if busqueda == "ninguna":
        clog.info("Sin búsqueda en Lupe: dice no tener cuenta")
        return _lupe_vacio("no_usuario")
    if datos.get("telefono_extranjero"):
        clog.info("Teléfono extranjero: se pide información")
        return _lupe_vacio("pedir_info")
    def consultar():
        resultado = consultar_cuenta(
            lupe.pagina(),
            correo=datos.get("correo_peticionario") or "",
            telefono=datos.get("telefono") or "",
            id_orden=datos.get("id_orden_en_peticion") or "",
            nombre_comercio=datos.get("nombre_comercio") or "",
            fecha_orden=datos.get("fecha_orden") or "",
            nombre_peticionario=datos.get("nombre_peticionario") or "",
            abrir_ordenes=busqueda == "orden",
            caso_log=clog,
        )
        if busqueda == "orden" and resultado.get("rama") == "sin_cuenta":
            resultado["rama"] = "pedir_info"
        return resultado

    try:
        return consultar()
    except _NO_CAPTURAR:
        raise
    except Exception as error:
        if _es_navegador_cerrado(error):
            clog.warning("Lupe: el navegador se cerró; reabierto y reintentando")
            try:
                lupe.reabrir()
                return consultar()
            except _NO_CAPTURAR:
                raise
            except Exception as segundo_error:
                _elevar_si_infra(segundo_error)
                clog.error("Error buscando en Lupe", exc_info=True)
                return {**_lupe_vacio("error"), "detalle": resumir_error(segundo_error)}
        _elevar_si_infra(error)
        clog.exception("Error buscando en Lupe")
        return {**_lupe_vacio("error"), "detalle": str(error)}


def _es_navegador_cerrado(error):
    texto = str(error).lower()
    return type(error).__name__ == "TargetClosedError" or (
        "target page, context or browser has been closed" in texto
    )


def _elevar_si_infra(error):
    if es_infraestructura(error):
        raise ErrorInfraestructura(details=resumir_error(error)) from (
            error if isinstance(error, BaseException) else None
        )


def _lupe_vacio(rama):
    return {"id_orden": "0", "id_usuario": "0", "valor_orden": "0", "rama": rama}


if __name__ == "__main__":
    try:
        procesar_entes()
    except (LupeSesionRequerida, LupeOcupado) as error:
        salir_por_lupe(error)
    except DetenerFlujo as error:
        log.warning("Paso detenido: %s", error)
        sys.exit(0)
