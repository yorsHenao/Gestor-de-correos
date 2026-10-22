import sys
from pathlib import Path
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
import storage
from config import (
    SLACK_BOT_TOKEN,
    SLACK_REVIEW_CHANNEL_ID,
    SLACK_REVIEW_USER_DISCIPLINE,
    SLACK_REVIEW_USER_FRAUD,
)
from utils.logger import log_caso, obtener_log

log = obtener_log()

BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

cliente_slack = WebClient(token=SLACK_BOT_TOKEN)

def enviar_slack(ruta_caso):
    informacion = storage.leer_caso(ruta_caso)

    id_rt = informacion["lupe"]["id"]
    regla = informacion["lupe"]["regla_bloqueo"]

    # Los casos con Fraud se asignan a fraude; el resto, a disciplina.
    es_fraude = "fraud" in regla.lower()
    usuario_id = (
        SLACK_REVIEW_USER_FRAUD if es_fraude else SLACK_REVIEW_USER_DISCIPLINE
    )
    usuario = f"<@{usuario_id}>"

    # armar mensaje
    mensaje = f"""
    *Nuevo caso RT*
    *ID:* {id_rt}
    *Regla de bloqueo:* {regla}
    Hola {usuario} Rt, solicita desbloqueo, nos confirman por favor motivo del bloqueo, y si se mantiene el bloqueo o se libera. Gracias.
    """

    # enviar mensaje
    try:
        respuesta = cliente_slack.chat_postMessage(
            channel=SLACK_REVIEW_CHANNEL_ID,
            text=mensaje
        )
        log_caso(log, ruta_caso).info(
            "Slack: mensaje a %s enviado (RT %s)", "fraude" if es_fraude else "disciplina", id_rt
        )

        # Guardar el thread_id internamente pero no mostrarlo en la salida
        informacion["slack"]["thread_id"] = respuesta.data["ts"]

        storage.guardar_caso(ruta_caso, informacion)

    except SlackApiError as e:
        log_caso(log, ruta_caso).error("Slack: no se pudo enviar el mensaje: %s", e.response["error"])