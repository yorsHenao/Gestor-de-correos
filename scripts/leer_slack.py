import sys
import re
import traceback
from datetime import datetime
from pathlib import Path
import httplib2
from googleapiclient.errors import HttpError
from slack_sdk import WebClient
from dotenv import load_dotenv
from agentes.agente_slack import AgenteSlack
from consultar_lupe import verificar_y_capturar_habilitado
from enviar_correo import enviar_correo, enviar_cuenta_habilitada
from generar_borrador import generar_borrador
from flow_control import DetenerFlujo, comprobar_detener
from lupe_navegador import SesionLupe, salir_por_lupe
from utils.exceptions import LupeOcupado, LupeSesionRequerida, es_infraestructura
import storage
import requests
from config import CONTROL_CALIDAD, SLACK_BOT_TOKEN, SLACK_REVIEW_CHANNEL_ID
from utils.logger import log_caso, obtener_log, resumen, resumir_error

log = obtener_log("slack")

BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

cliente_slack = WebClient(token=SLACK_BOT_TOKEN)
agente_slack = AgenteSlack()

DECISIONES_HABILITADO = ("liberar", "segunda_oportunidad")
MAX_INTENTOS_RESPUESTA = 5
_ESTADOS_HTTP_TEMPORALES = frozenset({408, 429, 500, 502, 503, 504})
_CAMPO_PDF = {
    "terceros": "pdf_drive_id_terceros",
    "rt_rl": "pdf_drive_id_rt_rl",
    "bloqueado": "pdf_drive_id",
}


class RespuestaPendiente(Exception):
    pass


def _formatear_hilo_ops(mensajes):
    """Texto cronológico del hilo (sin el mensaje inicial del bot)."""
    lineas = []
    for i, mensaje in enumerate(mensajes[1:], start=1):
        texto = (mensaje.get("text") or "").strip()
        if texto:
            lineas.append(f"[Mensaje {i}] {texto}")
    return "\n".join(lineas)


def _descargar_primera_imagen(mensajes, clog=log):
    vio_adjunto_no_imagen = False
    for msg in mensajes[1:]:
        archivos = msg.get("files", [])
        for archivo in archivos:
            mimetype = archivo.get("mimetype") or ""
            if mimetype.startswith("image/"):
                url = archivo.get("url_private")
                if not url:
                    continue
                try:
                    respuesta = requests.get(
                        url,
                        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                        timeout=30,
                    )
                except requests.RequestException as error:
                    clog.warning("Slack: no se pudo descargar la imagen: %s", error)
                    continue
                if respuesta.status_code == 200:
                    clog.info("Slack: imagen de prueba descargada")
                    return respuesta.content
                clog.warning("Slack: no se pudo descargar la imagen (status %s)", respuesta.status_code)
            elif mimetype:
                vio_adjunto_no_imagen = True
    if vio_adjunto_no_imagen:
        clog.info("Slack: adjunto que no es imagen (p. ej. video); la respuesta va sin anexo visual")
    return None


def _esperando_ops(informacion) -> str | None:
    """thread_id Slack sin decision de OPS; None si el caso no aplica a esta etapa."""
    if informacion.get("completado", False):
        return None
    slack = informacion.get("slack") or {}
    if (slack.get("estado") or "").strip() == "caso_manual":
        return None
    thread_id = (slack.get("thread_id") or "").strip()
    if not thread_id or slack.get("decision"):
        return None
    return thread_id


def _respuesta_pendiente(informacion) -> str | None:
    """Decisión de OPS ya guardada cuya respuesta no terminó; None si no hay nada que retomar."""
    if informacion.get("completado", False) or informacion.get("cuenta_no_encontrada"):
        return None
    slack = informacion.get("slack") or {}
    if (slack.get("estado") or "").strip() == "caso_manual":
        return None
    if not (slack.get("thread_id") or "").strip():
        return None
    decision = (slack.get("decision") or "").strip()
    if decision in DECISIONES_HABILITADO:
        return decision
    if decision == "mantener_bloqueo" and str(slack.get("causal_elegida") or "").strip():
        return decision
    return None


def _formato_respuesta(informacion):
    tipo_representacion = (informacion.get("correo") or {}).get("tipo_representacion", "").lower()
    if tipo_representacion == "rt_rl" or informacion.get("tipo_caso") == 3:
        return "rt_rl"
    if tipo_representacion == "tercero" or informacion.get("tipo_caso") == 2:
        return "terceros"
    return "bloqueado"


def _es_falla_temporal(error):
    if isinstance(error, HttpError):
        return getattr(error.resp, "status", None) in _ESTADOS_HTTP_TEMPORALES
    if isinstance(error, (FileNotFoundError, PermissionError)):
        return False
    if es_infraestructura(error):
        return True
    return isinstance(error, (
        httplib2.ServerNotFoundError,
        requests.ConnectionError,
        requests.Timeout,
        ConnectionError,
        TimeoutError,
        OSError,
        RespuestaPendiente,
    ))


def _foto_bloqueado(archivo, informacion, clog):
    foto = storage.leer_foto_prueba(archivo)
    if foto or not ((informacion.get("slack") or {}).get("foto_prueba") or {}).get("aplica"):
        return foto
    thread_id = informacion["slack"]["thread_id"]
    respuestas = cliente_slack.conversations_replies(
        channel=SLACK_REVIEW_CHANNEL_ID,
        ts=thread_id,
    )
    foto = _descargar_primera_imagen(respuestas.data["messages"], clog)
    if not foto:
        raise RespuestaPendiente("no se pudo recuperar la imagen de prueba del hilo")
    storage.guardar_foto_prueba(archivo, foto)
    return foto


def _completar_respuesta(archivo, decision, foto_bytes, lupe, clog, retomado=False):
    """Genera lo que falte y entrega la respuesta. True si quedó completado, False si sigue pendiente."""
    informacion = storage.leer_caso(archivo)
    if informacion.get("completado"):
        clog.info("El caso ya estaba completado; no se repite la respuesta")
        return True
    if decision in DECISIONES_HABILITADO:
        if foto_bytes is None:
            habilitado, foto_bytes = verificar_y_capturar_habilitado(archivo, page=lupe.pagina())
            if habilitado is None:
                raise RespuestaPendiente("Lupe no confirmó el estado de la cuenta")
            if not habilitado:
                informacion = storage.leer_caso(archivo)
                informacion["slack"]["estado"] = "caso_manual"
                informacion["recuperacion"] = _registro_recuperacion(
                    informacion, "OPS decidió habilitar pero Lupe muestra la cuenta bloqueada"
                )
                storage.guardar_caso(archivo, informacion)
                clog.warning(
                    "Caso manual: la decisión guardada es %s pero la cuenta sigue bloqueada en Lupe",
                    decision,
                )
                return None
        if not foto_bytes:
            raise RespuestaPendiente("Lupe no entregó la captura de evidencia de la cuenta habilitada")
        enviado = enviar_cuenta_habilitada(archivo, foto_bytes, crear_borrador=CONTROL_CALIDAD)
        formato = "habilitado"
    else:
        formato = _formato_respuesta(informacion)
        if informacion.get(_CAMPO_PDF[formato]):
            clog.info("Respuesta pendiente: el PDF ya existe; no se genera otro documento")
        else:
            if retomado:
                foto_bytes = _foto_bloqueado(archivo, informacion, clog)
            generar_borrador(archivo, formato=formato, foto_bytes=foto_bytes)
        enviado = enviar_correo(archivo, crear_borrador=CONTROL_CALIDAD)

    if not enviado:
        raise RespuestaPendiente("no se pudo crear o enviar el correo")

    informacion = storage.leer_caso(archivo)
    informacion["completado"] = True
    informacion.pop("recuperacion", None)
    storage.guardar_caso(archivo, informacion)
    storage.borrar_foto_prueba(archivo)
    clog.info(
        "Resultado: completado (%s, %s)",
        formato,
        "borrador" if CONTROL_CALIDAD else "enviado",
    )
    return True


def _registro_recuperacion(informacion, motivo):
    previo = informacion.get("recuperacion") or {}
    return {
        "intentos": int(previo.get("intentos") or 0) + 1,
        "ultimo_error": motivo,
        "fecha": datetime.now().isoformat(timespec="seconds"),
    }


def _motivo_respuesta(error):
    texto = resumir_error(error)
    texto = re.sub(r"https?://\S+", "[URL omitida]", texto, flags=re.I)
    texto = re.sub(r"\bBearer\s+\S+", "Bearer [oculto]", texto, flags=re.I)
    return re.sub(
        r"\b(token|access_token|refresh_token|api_key|password|secret|authorization)\s*[:=]\s*\S+",
        r"\1=[oculto]", texto, flags=re.I,
    )


def _registrar_falla(archivo, error, clog, contadores):
    motivo = _motivo_respuesta(error)
    try:
        informacion = storage.leer_caso(archivo)
        if informacion.get("completado"):
            clog.warning("El caso quedó completado pero falló una actualización posterior: %s", motivo)
            contadores["completados"] += 1
            return False
        recuperacion = _registro_recuperacion(informacion, motivo)
        informacion["recuperacion"] = recuperacion
        temporal = _es_falla_temporal(error) and recuperacion["intentos"] < MAX_INTENTOS_RESPUESTA
        if not temporal:
            informacion.setdefault("slack", {})["estado"] = "caso_manual"
        storage.guardar_caso(archivo, informacion)
    except Exception as error_guardado:
        clog.error(
            "Respuesta pendiente: no se pudo registrar la falla (%s); se reintentará con la decisión guardada",
            _motivo_respuesta(error_guardado),
        )
        contadores["errores"] += 1
        return True
    if temporal:
        contadores["errores"] += 1
        clog.error(
            "Respuesta pendiente (intento %d de %d): %s. La decisión de OPS queda guardada y se "
            "retomará en el próximo ciclo sin consultar de nuevo a OPS ni publicar en Slack",
            recuperacion["intentos"], MAX_INTENTOS_RESPUESTA, motivo,
        )
        return True
    contadores["revision_manual"] += 1
    clog.error(
        "Caso manual: la respuesta no se pudo completar (%s). Revisar el documento y el correo "
        "antes de responder; no se reintentará automáticamente",
        motivo,
    )
    return False


def revisar_slack():
    """Revisa los hilos pendientes; Lupe se abre una sola vez y solo si algún caso lo necesita."""
    with SesionLupe(paso="slack") as lupe:
        _revisar_pendientes(lupe)


def _revisar_pendientes(lupe):
    archivos = storage.listar_pendientes_slack()
    log.info("Slack: %d caso(s) «En Slack - pendiente» en el registro", len(archivos))
    contadores = {
        "procesados": 0,
        "completados": 0,
        "revision_manual": 0,
        "errores": 0,
        "esperando_ops": 0,
    }
    vistos = set()
    fallas_temporales = 0

    for archivo in archivos:
        clog = log_caso(log, archivo)
        try:
            informacion = storage.leer_caso(archivo)
        except FileNotFoundError:
            clog.warning("Está pendiente en la base pero el expediente no se pudo leer")
            continue
        thread_id = _esperando_ops(informacion)
        if not thread_id:
            continue

        comprobar_detener("Parada antes del siguiente caso Slack.")
        contadores["procesados"] += 1
        vistos.add(archivo)

        try:
            respuestas = cliente_slack.conversations_replies(
                channel=SLACK_REVIEW_CHANNEL_ID,
                ts=thread_id,
            )
        except Exception as e:
            contadores["errores"] += 1
            clog.error("Error leyendo el hilo de Slack: %s", e)
            continue

        mensajes = respuestas.data["messages"]

        if len(mensajes) == 1:
            contadores["esperando_ops"] += 1
            clog.info("Sigue pendiente: OPS aún no responde")
            continue

        respuesta_ops = _formatear_hilo_ops(mensajes)
        if not respuesta_ops.strip():
            contadores["esperando_ops"] += 1
            clog.info("Sigue pendiente: el hilo no tiene texto de OPS")
            continue

        if storage.falta_correo_peticionario(
            (informacion.get("correo") or {}).get("correo_peticionario")
        ):
            contadores["esperando_ops"] += 1
            clog.info("Sigue pendiente: falta el correo del peticionario")
            continue

        try:
            resultado_ia = agente_slack.interpretar_respuesta(respuesta_ops)
        except Exception as e:
            contadores["errores"] += 1
            clog.error("IA no pudo interpretar el hilo de OPS; sigue pendiente: %s", resumir_error(e))
            continue
        decision = resultado_ia["decision"]
        clog.info("OPS: %s → decisión %s", _truncar(respuesta_ops, 150), decision)

        if decision in DECISIONES_HABILITADO:
            habilitado, foto_bytes = verificar_y_capturar_habilitado(
                archivo, page=lupe.pagina()
            )
            if habilitado is None:
                contadores["errores"] += 1
                continue
            if not habilitado:
                contadores["esperando_ops"] += 1
                clog.info("Sigue pendiente: la cuenta aún está bloqueada en Lupe")
                continue
            informacion["slack"]["decision"] = decision
        else:
            foto_bytes = _descargar_primera_imagen(mensajes, clog)
            prueba = informacion.setdefault("slack", {}).setdefault(
                "foto_prueba", {"aplica": False}
            )
            prueba["aplica"] = bool(foto_bytes)
            informacion["slack"]["decision"] = decision
            mapeo = storage.leer_mapeo_causales()
            regla = informacion["lupe"]["regla_bloqueo"]
            if regla in mapeo["mapeo"]:
                informacion["slack"]["causal_elegida"] = mapeo["mapeo"][regla]
            else:
                informacion["slack"]["estado"] = "caso_manual"
                storage.guardar_caso(archivo, informacion)
                contadores["revision_manual"] += 1
                clog.warning("Caso manual: no hay causal mapeada para la regla «%s»", regla)
                continue
            if foto_bytes:
                storage.guardar_foto_prueba(archivo, foto_bytes)

        storage.guardar_caso(archivo, informacion)

        resultado = _intentar_respuesta(archivo, decision, foto_bytes, lupe, clog, contadores, False)
        fallas_temporales += resultado == "temporal"

    for archivo in storage.listar_respuestas_pendientes():
        if archivo in vistos:
            continue
        clog = log_caso(log, archivo)
        try:
            informacion = storage.leer_caso(archivo)
        except FileNotFoundError:
            continue
        decision = _respuesta_pendiente(informacion)
        if not decision:
            continue
        comprobar_detener("Parada antes del siguiente caso Slack.")
        contadores["procesados"] += 1
        vistos.add(archivo)
        clog.info(
            "Respuesta pendiente: se retoma la decisión guardada «%s» sin consultar de nuevo a OPS",
            decision,
        )
        resultado = _intentar_respuesta(archivo, decision, None, lupe, clog, contadores, True)
        fallas_temporales += resultado == "temporal"

    contadores["errores"] += contadores["revision_manual"]
    resumen(log, **contadores, reintentos_pendientes=fallas_temporales)
    if fallas_temporales:
        log.warning(
            "Resultado parcial: %d completados, %d respuestas con recuperación pendiente, %d manuales. "
            "Las decisiones quedan guardadas para la próxima ejecución de Slack; "
            "si el servicio está en pausa o no está activo, no se reintentan automáticamente.",
            contadores["completados"], fallas_temporales, contadores["revision_manual"],
        )


def _intentar_respuesta(archivo, decision, foto_bytes, lupe, clog, contadores, retomado):
    try:
        completado = _completar_respuesta(archivo, decision, foto_bytes, lupe, clog, retomado)
    except (DetenerFlujo, LupeSesionRequerida, LupeOcupado):
        raise
    except Exception as error:
        traza = "\n".join(
            f"  {Path(marco.filename).name}:{marco.lineno} en {marco.name}"
            for marco in traceback.extract_tb(error.__traceback__)
        )
        clog.error("Falla de respuesta: %s\nTraza:\n%s", _motivo_respuesta(error), traza)
        return "temporal" if _registrar_falla(archivo, error, clog, contadores) else "manual"
    if completado:
        contadores["completados"] += 1
        return "completado"
    contadores["revision_manual"] += 1
    return "manual"


def _truncar(texto, max_len):
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= max_len else texto[: max_len - 1].rstrip() + "…"


if __name__ == "__main__":
    try:
        revisar_slack()
    except DetenerFlujo:
        pass
    except (LupeSesionRequerida, LupeOcupado) as error:
        salir_por_lupe(error)
