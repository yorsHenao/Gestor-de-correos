import sys
from pathlib import Path
from slack_sdk import WebClient
from dotenv import load_dotenv
from agentes.agente_slack import AgenteSlack
from consultar_lupe import verificar_y_capturar_habilitado
from enviar_correo import enviar_correo, enviar_cuenta_habilitada
from generar_borrador import generar_borrador
from flow_control import DetenerFlujo, comprobar_detener
from lupe_navegador import SesionLupe, salir_por_lupe
from utils.exceptions import LupeOcupado, LupeSesionRequerida
import storage
import requests
from config import CONTROL_CALIDAD, SLACK_BOT_TOKEN, SLACK_REVIEW_CHANNEL_ID
from utils.logger import log_caso, obtener_log, resumen

log = obtener_log("slack")

BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

cliente_slack = WebClient(token=SLACK_BOT_TOKEN)
agente_slack = AgenteSlack()


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
                respuesta = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                )
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

        resultado_ia = agente_slack.interpretar_respuesta(respuesta_ops)
        decision = resultado_ia["decision"]
        clog.info("OPS: %s → decisión %s", _truncar(respuesta_ops, 150), decision)

        if decision in ("liberar", "segunda_oportunidad"):
            formato_borrador = "habilitado"
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
            formato_borrador = "bloqueado"
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

        storage.guardar_caso(archivo, informacion)

        if formato_borrador == "bloqueado":
            tipo_representacion = informacion["correo"].get("tipo_representacion", "").lower()
            if tipo_representacion == "rt_rl" or informacion.get("tipo_caso") == 3:
                formato_respuesta = "rt_rl"
            elif tipo_representacion == "tercero" or informacion.get("tipo_caso") == 2:
                formato_respuesta = "terceros"
            else:
                formato_respuesta = "bloqueado"
            generar_borrador(
                archivo,
                formato=formato_respuesta,
                foto_bytes=foto_bytes,
            )

        informacion = storage.leer_caso(archivo)

        if formato_borrador == "habilitado":
            enviado = enviar_cuenta_habilitada(
                archivo,
                foto_bytes,
                crear_borrador=CONTROL_CALIDAD,
            )
        else:
            enviado = enviar_correo(
                archivo,
                crear_borrador=CONTROL_CALIDAD,
            )

        if enviado:
            informacion = storage.leer_caso(archivo)
            informacion["completado"] = True
            storage.guardar_caso(archivo, informacion)
            contadores["completados"] += 1
            clog.info(
                "Resultado: completado (%s, %s)",
                formato_borrador,
                "borrador" if CONTROL_CALIDAD else "enviado",
            )
        else:
            contadores["errores"] += 1
            clog.error("No se pudo crear o enviar el correo; el caso sigue pendiente")
            informacion = storage.leer_caso(archivo)
            informacion["slack"]["decision"] = ""
            storage.guardar_caso(archivo, informacion)

    resumen(log, **contadores)


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
