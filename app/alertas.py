import base64
import logging
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for ruta in (str(RAIZ), str(RAIZ / "scripts")):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

import config
log = logging.getLogger("proyecto_e")

MARCA = "PROYECTO_E_ALERTA\t"

TIPOS = {
    "lupe_login": "Lupe pide iniciar sesión",
    "lupe_ocupado": "Lupe ocupado",
    "paso_error": "Un paso terminó con error",
    "entes_revision": "Caso de Entes a revisión manual",
    "entes_pausa": "Entes pausado en esta ronda",
    "entes_infra": "Entes pausado por infraestructura",
    "servicio": "Servicio Proyecto E",
}

_MAX_AVISOS = 50
_avisos = []
_enviados = set()


def alertar(tipo, titulo, detalle="", ciclo_id=None):
    """Manda la alerta por los canales activos. Nunca lanza excepción."""
    del ciclo_id
    clave = (tipo, titulo)
    if clave in _enviados:
        return False
    _enviados.add(clave)
    anotar(tipo, titulo, detalle)
    texto = (detalle or "").replace("\n", " ")
    print(f"{MARCA}{tipo}\t{titulo}\t{texto}", flush=True)

    for canal in config.ALERTAS_CANALES:
        envio = _CANALES.get(canal)
        if envio is None:
            log.warning("Canal de alerta desconocido: %s", canal, extra={"paso": "alertas"})
            continue
        try:
            envio(titulo, detalle)
        except Exception as error:
            log.warning("Alerta por %s no enviada: %s", canal, error, extra={"paso": "alertas"})
    return True


def nuevo_ciclo():
    """Cada ciclo vuelve a avisar: la misma alerta solo se repite dentro de un ciclo."""
    _enviados.clear()


def anotar(tipo, titulo, detalle=""):
    _avisos.append({
        "tipo": tipo,
        "titulo": titulo,
        "detalle": detalle or "",
        "fecha": datetime.now().strftime("%d/%m %H:%M"),
    })
    del _avisos[:-_MAX_AVISOS]


def anotar_desde_linea(linea):
    if not linea.startswith(MARCA):
        return False
    partes = linea[len(MARCA):].rstrip("\n").split("\t", 2)
    if len(partes) >= 2:
        tipo, titulo = partes[0], partes[1]
        detalle = partes[2] if len(partes) > 2 else ""
        if not any(aviso["tipo"] == tipo and aviso["titulo"] == titulo for aviso in _avisos):
            anotar(tipo, titulo, detalle)
    return True


def pendientes(limite=5):
    return list(reversed(_avisos[-limite:]))


def _toast(titulo, detalle):
    from win11toast import notify

    notify(
        f"Proyecto E: {titulo}",
        (detalle or "")[:250],
        on_click=config.DASHBOARD_URL,
    )


def _gmail(titulo, detalle):
    destino = (config.ALERTAS_EMAIL or "").strip()
    if not destino:
        raise RuntimeError("ALERTAS_EMAIL no está en el .env")
    from autenticar import autenticar
    from googleapiclient.discovery import build

    cuerpo = f"{detalle or titulo}\n\nDashboard: {config.DASHBOARD_URL}"
    mensaje = MIMEText(cuerpo, "plain", "utf-8")
    mensaje["to"] = destino
    mensaje["subject"] = f"[Proyecto E] {titulo}"
    raw = base64.urlsafe_b64encode(mensaje.as_bytes()).decode()
    servicio = build("gmail", "v1", credentials=autenticar())
    servicio.users().messages().send(userId="me", body={"raw": raw}).execute()


def _slack(titulo, detalle):
    canal = (config.ALERTAS_SLACK_CANAL or "").strip()
    if not canal or not config.SLACK_BOT_TOKEN:
        raise RuntimeError("Faltan ALERTAS_SLACK_CANAL o SLACK_BOT_TOKEN")
    from slack_sdk import WebClient

    WebClient(token=config.SLACK_BOT_TOKEN).chat_postMessage(
        channel=canal, text=f":rotating_light: *Proyecto E: {titulo}*\n{detalle}"
    )


_CANALES = {"toast": _toast, "gmail": _gmail, "slack": _slack}


if __name__ == "__main__":
    alertar("servicio", "Prueba de alertas", "Si ves esto, el canal funciona.")
