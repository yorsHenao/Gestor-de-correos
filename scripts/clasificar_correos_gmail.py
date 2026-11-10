import sys
import re
from unicodedata import normalize
from email.utils import parseaddr
from pathlib import Path
from dotenv import load_dotenv
from googleapiclient.discovery import build
from autenticar import autenticar
try:
    from scripts.agentes.agente_clasificador import clasificar_correo
except ModuleNotFoundError:
    from agentes.agente_clasificador import clasificar_correo
from gmail_cuerpo import extraer_contenido
from flow_control import DetenerFlujo, comprobar_detener, salir_por_red
from config import (
    CORREOS_REVISION_MANUAL,
    DOMINIOS_REVISION_MANUAL,
    GMAIL_LABEL_ELIMINAR_DEUDA_RTS,
    GMAIL_LABEL_ENTES,
    GMAIL_LABEL_FACTURACION,
    GMAIL_LABEL_ID,
    GMAIL_LABEL_OTROS,
    GMAIL_LABEL_RESTAURANTES,
    GMAIL_LABEL_REVISION_MANUAL,
    IA_PROVIDER,
)
import logging
from app.alertas import alertar
from utils.exceptions import es_infraestructura
from utils.logger import log_caso, obtener_log, resumen, resumir_error
logging.getLogger("pdfminer").setLevel(logging.ERROR)

log = obtener_log("clasificar")

BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

LABEL_RT          = GMAIL_LABEL_ID
LABEL_ENTES       = GMAIL_LABEL_ENTES
LABEL_MANUAL      = GMAIL_LABEL_REVISION_MANUAL
LABEL_OTROS       = GMAIL_LABEL_OTROS
LABEL_FACTURACION = GMAIL_LABEL_FACTURACION
LABEL_RESTAURANTES = GMAIL_LABEL_RESTAURANTES
LABEL_ELIMINAR_DEUDA_RTS = GMAIL_LABEL_ELIMINAR_DEUDA_RTS
CONFIANZA_MINIMA = 0.75
CORREO_HUB = "recepcion.hub@rappi.com"


_ETIQUETA_NOMBRES = {
    LABEL_RT: "RT bloqueado",
    LABEL_ELIMINAR_DEUDA_RTS: "Eliminar_deudas_rts",
    LABEL_ENTES: "Entes",
    LABEL_MANUAL: "Revisión manual",
    LABEL_OTROS: "Otros",
    LABEL_FACTURACION: "Facturación",
    LABEL_RESTAURANTES: "Restaurantes",
}


def _truncar(texto, max_len=100):
    texto = " ".join(str(texto or "").split())
    if len(texto) <= max_len:
        return texto
    return texto[: max_len - 1].rstrip() + "…"


def _nombre_etiqueta(etiqueta_id):
    return _ETIQUETA_NOMBRES.get(etiqueta_id, "Etiqueta Gmail")


def _imprimir_clasificacion(
    correo_peticionario,
    resumen_ia,
    categorias,
    etiqueta_id,
    confianza,
    razon,
):
    """Una línea por correo; WARNING si queda en revisión manual."""
    cat_txt = ", ".join(categorias) if categorias else "—"
    if categorias or float(confianza or 0) > 0:
        ia_txt = f"{cat_txt} ({float(confianza) * 100:.0f}%)"
    else:
        ia_txt = "—"
    texto = f"{_nombre_etiqueta(etiqueta_id)} | IA {ia_txt} | {razon}"
    if resumen_ia:
        texto += f" | {_truncar(resumen_ia, 100)}"
    nivel = logging.WARNING if etiqueta_id == LABEL_MANUAL else logging.INFO
    log_caso(log, correo_peticionario).log(nivel, texto)


def _es_recepcion_hub(*correos):
    destinos = {CORREO_HUB, *CORREOS_REVISION_MANUAL}
    for valor in correos:
        _, email = parseaddr(str(valor or ""))
        candidato = (email or valor or "").strip().lower()
        if candidato in destinos:
            return True
    return False


def _marcar_revision_manual(servicio, mensaje_id):
    servicio.users().messages().modify(
        userId="me",
        id=mensaje_id,
        body={
            "addLabelIds": [LABEL_MANUAL],
            "removeLabelIds": ["INBOX"],
        },
    ).execute()


def _obtener_peticionario(remitente, cuerpo):
    """Obtiene el remitente del mensaje reenviado o usa el externo."""
    bloques_reenviados = re.split(
        r"(?im)^\s*(?:[-\s]*forwarded message[-\s]*|mensaje reenviado)\s*$",
        cuerpo,
    )
    bloque = bloques_reenviados[-1] if len(bloques_reenviados) > 1 else cuerpo
    encontrados = re.findall(r"(?im)^\s*(?:from|de):\s*(.+?)\s*$", bloque)

    nombre, correo = parseaddr(encontrados[-1] if encontrados else remitente)
    if not correo:
        correo = remitente
    if not nombre:
        nombre = correo
    return nombre, correo


_REENVIO = re.compile(
    r"(?im)^\s*(?:[-\s]*forwarded message[-\s]*|mensaje reenviado)\s*$"
)

_CORTE_CABECERA = re.compile(
    r"(?im)"
    r"-{2,}\s*(?:forwarded message|mensaje reenviado|original message|mensaje original)\s*-{2,}"
    r"|^\s*(?:forwarded message|mensaje reenviado)\s*$"
    r"|(?:inicio del mensaje reenviado|begin forwarded message)\s*:"
    r"|\b(?:de|from)\s*:[^\n]{0,250}?\b(?:enviado(?:\s+el)?|sent|fecha|date)\s*:"
    r"|^\s*(?:de|from)\s*:[^\n]*\n\s*(?:enviado(?:\s+el)?|sent|fecha|date)\s*:"
    r"|\b(?:escribio|wrote)\s*:"
    r"|^\s*>"
)
_PREFIJO_REENVIO_ASUNTO = re.compile(r"^\s*(?:(?:fwd?|rv|re|tr|reenviar|reenviado)\s*:\s*)+")
_CASO = (
    r"(?:casit[oa]s?|casos?(?:\s+rt)?|solicitud(?:es)?|peticion(?:es)?|tickets?"
    r"|radicados?|requerimientos?|reclamos?|temas?|gestion(?:es)?)"
)
_FRASES_CUALQUIER_REMITENTE = tuple(re.compile(p) for p in (
    rf"\bmism[oa]s?\s+{_CASO}\b",
    rf"\b{_CASO}\s+(?:\w+\s+){{0,2}}?(?:repetid[oa]s?|duplicad[oa]s?)\b",
    r"\b(?:es|esta|seria|sera|parece)\s+(?:un\s+|una\s+)?(?:repetid[oa]|duplicad[oa])\b",
    r"\bdoble\s+gestion\b",
    r"\b(?:confirmar|confirmame|validar|verificar)\b.{0,80}?\bmism[oa]\s+(?:correo|peticion)\b",
))
_FRASES_INTERNAS = tuple(re.compile(p) for p in (
    r"\b(?:es|seria|sera|corresponde\s+a|corresponde\s+al|si\s+es)\s+(?:el\s+|la\s+|lo\s+)?mism[oa]\b",
    r"\bes\s+igual\s+al?\s+anterior\b",
    r"\bes\s+(?:el|la)\s+que\s+ya\s+teniamos\b",
    r"\b(?:repetid[oa]s?|duplicad[oa]s?)\b",
    r"\bya\s+(?:(?:se|lo|la|le|fue|esta|en)\s+){0,3}(?:gestion\w*|trabaj\w*|respond\w*|respondi\w*)",
    r"\bya\s+lo\s+(?:tienes|tenemos|tengo)\b",
    rf"\b(?:tienes|tenemos|existe|hay)\s+(?:(?:ya|un|una|el|la|este|esta|algun|alguna|abierto)\s+){{0,3}}{_CASO}\b",
    r"\b(?:me\s+indicas|me\s+indica|me\s+confirmas|me\s+confirma|me\s+validas"
    r"|me\s+ayudas\s+a\s+validar|me\s+ayudas\s+a\s+revisar|puedes\s+revisar|podrias\s+revisar)\s+si\b"
    r".{0,150}?\b(?:mism[oa]s?|gestionad[oa]s?|repetid[oa]s?|duplicad[oa]s?)",
    r"\bignor(?:a|alo|ala|ar|en)\b",
    r"\bno\s+(?:lo|la|los|las)\s+gestion\w*",
    rf"\b(?:un|una|otro|otra)\s+{_CASO}\s+(?:diferente|distint[oa]|nuev[oa])\b",
    rf"\b{_CASO}\s+(?:diferente|distint[oa])\b",
    rf"\bes\s+otr[oa]\s+{_CASO}\b",
    r"\bno\s+es\s+(?:el|la)\s+mism[oa]\b",
    r"\bpuedes\s+(?:avanzar|gestionar\w*|proceder|continuar)\b",
    r"\bgestional[oa]s?\b",
    r"\bdale\b",
    r"\bproced[ae]\b",
))


def _normalizar_texto(texto):
    t = normalize("NFD", str(texto or "").lower())
    t = re.sub(r"[\u0300-\u036f]", "", t)
    return re.sub(r"[^\w@.\n>:-]+", " ", t)


def _texto_cabecera(texto, max_len=1500):
    """Tramo antes del reenvío (o inicio del correo) para coordinación entre revisores."""
    t = _normalizar_texto(texto)
    corte = _CORTE_CABECERA.search(t)
    cabecera = t[: corte.start()] if corte else t
    return " ".join(cabecera.split())[:max_len]


def _correo_de(valor):
    _, email = parseaddr(str(valor or ""))
    return (email or str(valor or "")).strip().lower()


def _es_remitente_interno(remitente):
    return _correo_de(remitente).endswith("@rappi.com")


def _es_consulta_mismo_caso(texto, remitente="", asunto=None):
    """Colega pide confirmar si el reenvío es el mismo caso en gestión."""
    if asunto is None:
        cuerpo = str(texto or "")
        primera, _, resto = cuerpo.partition("\n")
    else:
        primera, resto = str(asunto or ""), str(texto or "")
    cabecera = _texto_cabecera(f"{primera}\n{resto}")
    if cabecera and any(p.search(cabecera) for p in _FRASES_CUALQUIER_REMITENTE):
        return True
    if not _es_remitente_interno(remitente):
        return False
    asunto_propio = "" if _PREFIJO_REENVIO_ASUNTO.match(_normalizar_texto(primera)) else primera
    propio = _texto_cabecera(f"{asunto_propio}\n{resto}")
    return bool(propio) and any(p.search(propio) for p in _FRASES_INTERNAS)


_LINEA_REMITENTE = re.compile(
    r"(?i)\b(?:de|from)\s*:\s*[^\n<>@]{0,120}?<?\s*([\w.+-]+@[\w-]+(?:\.[\w-]+)+)"
)


def _dominio_revision_manual(correo):
    email = _correo_de(correo)
    if "@" not in email:
        return ""
    dominio = email.rsplit("@", 1)[1].strip(".")
    for base in DOMINIOS_REVISION_MANUAL:
        if dominio == base or dominio.endswith("." + base):
            return base
    return ""


def _es_autoridad(remitente, cuerpo=""):
    if _dominio_revision_manual(remitente):
        return True
    if not _es_remitente_interno(remitente):
        return False
    return any(
        _dominio_revision_manual(hallado)
        for hallado in _LINEA_REMITENTE.findall(str(cuerpo or ""))
    )


def razon_revision_fija(asunto, cuerpo, remitente=""):
    if _es_autoridad(remitente, cuerpo):
        return "entidad judicial o autoridad — revisión manual"
    if _es_consulta_mismo_caso(cuerpo, remitente, asunto=asunto):
        return "coordinación mismo caso — revisión manual"
    return ""


def _es_reclamo_aliado_operativo(texto):
    """Tienda/aliado con problema operativo de repartidores en el local."""
    t = " ".join(str(texto or "").lower().split())
    if not t:
        return False
    aliado = any(
        p in t
        for p in (
            "tienda",
            "comercio",
            "aliado",
            "restaurante",
            "local",
            "punto de venta",
            "nuestra tienda",
            "mi negocio",
            "nuestro local",
        )
    )
    operativo = any(
        p in t
        for p in (
            "marcar llegada",
            "marcan llegada",
            "no pueden marcar",
            "no puede marcar llegada",
            "liberación",
            "liberado",
            "liberan",
            "se liberan",
            "liberación automática",
        )
    ) or (
        "repartidor" in t
        and any(p in t for p in ("llegada", "liber", "actualiz"))
    )
    return aliado and operativo


def _menciona_sancion_rappi(texto):
    """Suspensión, bloqueo o desbloqueo: la petición puede ir a OPS aunque diga desactivada."""
    t = " ".join(str(texto or "").lower().split())
    t = re.sub(r"[\u0300-\u036f]", "", normalize("NFD", t))
    return any(
        p in t
        for p in ("suspendid", "inhabilitad", "deshabilitad", "bloque", "desbloque")
    ) or (
        re.search(r"\bcuentas?\b", t) is not None
        and re.search(r"\b(?:suspension|inhabilitacion)(?:es)?\b", t) is not None
    )


def _es_cuenta_desactivada_o_eliminada(texto):
    """Reactivar una cuenta apagada, sin sanción de Rappi, no es desbloqueo RT.

    Si el escrito menciona suspensión, bloqueo o desbloqueo, sigue el flujo RT
    aunque también diga desactivada, eliminada o dada de baja.
    """
    t = " ".join(str(texto or "").lower().split())
    if not t:
        return False
    if _menciona_sancion_rappi(t):
        return False
    desactivada = "desactivad" in t
    eliminada = (
        "cuenta eliminad" in t
        or "eliminaron mi cuenta" in t
        or "eliminaron la cuenta" in t
    )
    baja = "dieron de baja" in t or "dar de baja" in t
    if desactivada or eliminada or baja:
        return True
    return ("activar" in t or "reactiv" in t) and "cuenta" in t


def _rt_sin_cola_operativa(texto):
    """Acceso a la cuenta, cuenta apagada o incentivos: van a revisión manual."""
    if _es_cuenta_desactivada_o_eliminada(texto):
        return True
    if _es_reclamo_aliado_operativo(texto):
        return False
    t = " ".join(str(texto or "").lower().split())
    if not t:
        return False
    incentivos = (
        "incentivo" in t
        or "bono" in t
        or "recompensa" in t
        or "semana no pagada" in t
        or "semanas no pagadas" in t
        or "no me pagaron" in t
        or "no me han pagado" in t
    )
    olvido = any(
        p in t for p in ("olvide", "olvidé", "olvido", "olvidó", "perdi", "perdí", "perdió")
    )
    dato_acceso = any(p in t for p in ("clave", "contraseña", "contrasena"))
    correo_soy_rappi = "correo" in t and "soy rappi" in t
    no_entra = any(p in t for p in ("no puedo", "no me deja", "no me permite"))
    accion_cuenta = any(p in t for p in ("entrar", "ingresar", "crear", "iniciar"))
    verificar = "verificar" in t and ("registr" in t or "datos" in t)
    return (
        incentivos
        or (olvido and (dato_acceso or correo_soy_rappi))
        or (no_entra and accion_cuenta)
        or verificar
    )


_TEL_INTERNACIONAL = re.compile(
    r"(?:(?<![\w+.,$])\+|(?<![\w.,$])00)\s?\d[\d\s().-]{6,}\d"
)


def _tiene_telefono_extranjero(texto):
    """Teléfono con indicativo de otro país (+51, +1, 0052...). Colombia (+57) no cuenta."""
    for hallado in _TEL_INTERNACIONAL.finditer(str(texto or "")):
        crudo = hallado.group(0).strip()
        digitos = re.sub(r"\D", "", crudo)
        if crudo.startswith("00"):
            digitos = digitos[2:]
        if len(digitos) < 8:
            continue
        if not digitos.startswith("57"):
            return True
    return False


def _decidir_etiqueta(resultado, texto="", remitente=""):
    categorias = resultado.get("categorias", [])
    confianza  = resultado.get("confianza", 0)

    if _es_consulta_mismo_caso(texto, remitente):
        return LABEL_MANUAL, "coordinación mismo caso — revisión manual"

    if _es_cuenta_desactivada_o_eliminada(texto):
        return LABEL_MANUAL, "cuenta desactivada o eliminada — revisión manual"

    if _es_reclamo_aliado_operativo(texto) and (
        not categorias or categorias == ["entes"]
    ):
        return LABEL_RESTAURANTES, "aliado operativo — reclamo_aliado"

    if len(categorias) == 0:
        if _rt_sin_cola_operativa(texto):
            return LABEL_MANUAL, "acceso o incentivos — revisión manual"
        return LABEL_MANUAL, "sin categoría — revisión manual"
    if len(categorias) > 1:
        if set(categorias) == {"rt_bloqueado", "eliminar_deuda"}:
            return LABEL_MANUAL, "desbloqueo y eliminar deuda — revisión manual"
        return LABEL_MANUAL, "varias categorías — revisión manual"

    categoria = categorias[0]

    if categoria == "rt_bloqueado":
        if confianza >= CONFIANZA_MINIMA:
            return LABEL_RT, "cuenta bloqueada"
        return LABEL_MANUAL, "rt_bloqueado con confianza baja"
    elif categoria == "entes":
        if _es_reclamo_aliado_operativo(texto):
            return LABEL_RESTAURANTES, "aliado operativo — reclamo_aliado"
        if _tiene_telefono_extranjero(texto):
            return LABEL_MANUAL, "teléfono de otro país → revisión manual"
        return LABEL_ENTES, "reclamo cliente final"
    elif categoria == "eliminar_deuda":
        if confianza >= CONFIANZA_MINIMA and LABEL_ELIMINAR_DEUDA_RTS:
            return LABEL_ELIMINAR_DEUDA_RTS, "deuda RT (confianza suficiente)"
        if confianza >= CONFIANZA_MINIMA and not LABEL_ELIMINAR_DEUDA_RTS:
            return LABEL_MANUAL, "falta GMAIL_LABEL_ELIMINAR_DEUDA_RTS en .env"
        return LABEL_MANUAL, "eliminar_deuda con confianza baja"
    elif categoria == "facturacion":
        return LABEL_FACTURACION, "facturación"
    elif categoria == "otro":
        return LABEL_OTROS, "categoría: otro"
    elif categoria == "reclamo_aliado":
        return LABEL_RESTAURANTES, "categoría: reclamo_aliado (restaurante/aliado)"
    else:
        return LABEL_MANUAL, f"categoría no mapeada: {categoria}"


def clasificar_correos():
    creds    = autenticar()
    servicio = build("gmail", "v1", credentials=creds)

    resultado = servicio.users().messages().list(
        userId="me",
        labelIds=["INBOX", "UNREAD"],
        maxResults=60
    ).execute()

    mensajes = resultado.get("messages", [])
    log.info("INBOX: %d correo(s) sin leer | IA: %s", len(mensajes), IA_PROVIDER)
    contadores = {"procesados": 0, "completados": 0, "revision_manual": 0, "errores": 0}
    if not mensajes:
        resumen(log, **contadores)
        return

    for mensaje in mensajes:
        contadores["procesados"] += 1
        comprobar_detener("Parada antes del siguiente correo a clasificar.")
        try:
            msg = servicio.users().messages().get(
                userId="me",
                id=mensaje["id"],
                format="full"
            ).execute()

            headers = msg["payload"]["headers"]
            asunto = next((h["value"] for h in headers if h["name"] == "Subject"), "Sin asunto")
            remitente = next((h["value"] for h in headers if h["name"] == "From"), "Desconocido")
            _, correo_from = parseaddr(remitente)
            if _es_recepcion_hub(correo_from or remitente):
                _imprimir_clasificacion(
                    correo_from or remitente,
                    "",
                    [],
                    LABEL_MANUAL,
                    0,
                    "recepción hub — revisión manual",
                )
                _marcar_revision_manual(servicio, mensaje["id"])
                continue

            cuerpo, adjuntos = extraer_contenido(msg, servicio)

            _, correo_peticionario = _obtener_peticionario(
                remitente, f"{cuerpo}\n{adjuntos}"
            )
            if _es_recepcion_hub(correo_peticionario):
                _imprimir_clasificacion(
                    correo_peticionario,
                    "",
                    [],
                    LABEL_MANUAL,
                    0,
                    "recepción hub — revisión manual",
                )
                _marcar_revision_manual(servicio, mensaje["id"])
                continue

            thread_id = msg["threadId"]
            resultado_hilo = servicio.users().threads().get(
                userId="me", id=thread_id, format="metadata"
            ).execute()

            etiquetas_existentes_en_hilo = set()
            for m in resultado_hilo.get("messages", []):
                etiquetas_existentes_en_hilo.update(m.get("labelIds", []))

            categorias_nuestras = {
                LABEL_RT, LABEL_ELIMINAR_DEUDA_RTS, LABEL_ENTES, LABEL_MANUAL,
                LABEL_OTROS, LABEL_FACTURACION,
                LABEL_RESTAURANTES,
            }
            categorias_nuestras.discard("")
            ya_gestionado = bool(etiquetas_existentes_en_hilo & categorias_nuestras)

            if ya_gestionado:
                _imprimir_clasificacion(
                    correo_peticionario,
                    "",
                    [],
                    LABEL_MANUAL,
                    0,
                    "hilo ya clasificado — revisión manual",
                )

                _marcar_revision_manual(servicio, mensaje["id"])
                continue

            razon_fija = razon_revision_fija(asunto, cuerpo, remitente)
            if razon_fija:
                _imprimir_clasificacion(
                    correo_peticionario,
                    "",
                    [],
                    LABEL_MANUAL,
                    0,
                    razon_fija,
                )
                _marcar_revision_manual(servicio, mensaje["id"])
                continue

            etiqueta = None
            try:
                resultado_ia = clasificar_correo(asunto, cuerpo, adjuntos=adjuntos)
                categorias = resultado_ia.get('categorias', [])
                etiqueta, razon = _decidir_etiqueta(
                    resultado_ia, f"{asunto}\n{cuerpo}\n{adjuntos}", remitente
                )
            except Exception as error:
                if es_infraestructura(error):
                    raise
                log_caso(log, correo_peticionario).warning("IA no disponible: %s", error)
                resultado_ia = {"categorias": [], "confianza": 0, "resumen": ""}
                categorias = []
                etiqueta = LABEL_MANUAL
                razon = "IA no disponible, revisión manual"

            _imprimir_clasificacion(
                correo_peticionario,
                resultado_ia.get("resumen", ""),
                categorias,
                etiqueta,
                resultado_ia.get("confianza", 0),
                razon,
            )

            servicio.users().messages().modify(
                userId="me",
                id=mensaje["id"],
                body={
                    "addLabelIds": [etiqueta],
                    "removeLabelIds": ["INBOX"]
                }
            ).execute()
            if etiqueta != LABEL_MANUAL:
                contadores["completados"] += 1
        except Exception as error:
            if es_infraestructura(error):
                contadores["procesados"] -= 1
                contadores["pausa_infra"] = len(mensajes) - contadores["procesados"]
                detalle = resumir_error(error)
                log.warning(
                    "Clasificar en pausa por infraestructura; %d correo(s) siguen en la bandeja: %s",
                    contadores["pausa_infra"],
                    detalle,
                )
                alertar(
                    "clasificar_infra",
                    "Clasificar pausado por infraestructura",
                    f"{detalle}. Los correos se reintentarán en la siguiente ronda.",
                )
                break
            contadores["errores"] += 1
            log.error("No se pudo clasificar el correo %s; queda en la bandeja para la siguiente ronda: %s", mensaje["id"], error)

    contadores["revision_manual"] = (
        contadores["procesados"] - contadores["completados"] - contadores["errores"]
    )
    resumen(log, **contadores)


if __name__ == "__main__":
    try:
        clasificar_correos()
    except DetenerFlujo:
        pass
    except Exception as error:
        if es_infraestructura(error):
            salir_por_red(error)
        raise
