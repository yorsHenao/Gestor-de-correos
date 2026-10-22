"""Lectura de la cola Gmail de Entes: cuerpo, fecha de la petición, PDF e imágenes.

El cuerpo y los bytes de adjuntos salen de gmail_cuerpo; el OCR de PDF, de ocr.py.
"""

import re
from datetime import date, timedelta, timezone
from email.utils import parsedate_to_datetime, parseaddr

from config import GMAIL_LABEL_ENTES
from gmail_cuerpo import bytes_parte, extraer_contenido
from ocr import extraer_texto_pdf
from utils.exceptions import es_infraestructura
from utils.logger import resumir_error

ZONA_COLOMBIA = timezone(timedelta(hours=-5))

_MESES_CORREO = {
    "ene": 1, "enero": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}


def listar_ids_entes(servicio, max_resultados):
    """Ids de los mensajes no leídos con la etiqueta entes, del más reciente hacia atrás."""
    if not GMAIL_LABEL_ENTES:
        raise RuntimeError("GMAIL_LABEL_ENTES no está en el .env")
    ids = []
    page_token = None
    while len(ids) < max_resultados:
        lote = (
            servicio.users()
            .messages()
            .list(
                userId="me",
                labelIds=[GMAIL_LABEL_ENTES, "UNREAD"],
                maxResults=min(20, max_resultados - len(ids)),
                pageToken=page_token,
            )
            .execute()
        )
        ids.extend(item["id"] for item in lote.get("messages", []))
        page_token = lote.get("nextPageToken")
        if not page_token:
            break
    return ids[:max_resultados]


def leer_mensaje(servicio, message_id, clog):
    msg = (
        servicio.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    payload = msg.get("payload") or {}
    headers = payload.get("headers", [])
    remitente = _header(headers, "From")
    nombre, correo = parseaddr(remitente)
    cuerpo, _ = extraer_contenido(msg, servicio, incluir_adjuntos=False)
    texto_pdf = _texto_pdfs(servicio, msg["id"], payload, clog)
    pdfs = _listar_partes(payload, _es_pdf)
    adjuntos = _adjuntos(payload)
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "message_id": _header(headers, "Message-ID"),
        "asunto": _header(headers, "Subject") or "Sin asunto",
        "remitente": remitente or "Desconocido",
        "remitente_nombre": nombre or correo or remitente,
        "remitente_correo": correo or remitente,
        "fecha_llegada_correo": _fecha_peticion(cuerpo, _header(headers, "Date")),
        "cuerpo": cuerpo,
        "html": _html(servicio, msg["id"], payload),
        "texto_pdf": texto_pdf,
        "adjuntos": adjuntos,
        "imagenes": _listar_partes(payload, lambda mime, _n: mime.startswith("image/")),
        "pdfs": pdfs,
    }


def correo_agente(servicio):
    perfil = servicio.users().getProfile(userId="me").execute()
    return (perfil.get("emailAddress") or "").strip()


def _header(headers, nombre):
    for header in headers:
        if header.get("name", "").lower() == nombre.lower():
            return header.get("value", "")
    return ""


def _fecha_iso(fecha_raw):
    if not fecha_raw:
        return ""
    limpia = re.sub(r"(?i)\s+(?:at|a las)\s+", " ", str(fecha_raw)).strip()
    try:
        momento = parsedate_to_datetime(limpia).astimezone(ZONA_COLOMBIA)
    except (TypeError, ValueError, IndexError):
        return _fecha_escrita(limpia)
    return momento.date().isoformat()


def _fecha_peticion(cuerpo, fecha_principal):
    """Fecha del bloque reenviado. La del sobre solo se usa si no hay reenvío."""
    bloques = re.split(
        r"(?im)^\s*(?:[-\s]*forwarded message[-\s]*|mensaje reenviado)\s*$",
        cuerpo or "",
    )
    if len(bloques) > 1:
        hallado = re.search(r"(?im)^\s*(?:date|fecha)\s*:\s*(.+)$", bloques[-1])
        if hallado:
            fecha = _fecha_iso(hallado.group(1))
            if fecha:
                return fecha
    return _fecha_iso(fecha_principal)


def _fecha_escrita(texto):
    plano = texto.lower()
    hallado = re.search(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóúñ]+)\.?\s+(?:de\s+)?(\d{4})", plano)
    if not hallado:
        return ""
    mes = _MESES_CORREO.get(hallado.group(2))
    if not mes:
        return ""
    try:
        return date(int(hallado.group(3)), mes, int(hallado.group(1))).isoformat()
    except ValueError:
        return ""


def _html(servicio, message_id, payload):
    """HTML del mensaje, para imprimirlo como se imprime el correo."""
    partes = []

    def recorrer(parte):
        if parte.get("mimeType", "") == "text/html":
            data = bytes_parte(servicio, message_id, parte)
            if data:
                partes.append(data.decode("utf-8", errors="replace"))
            return
        for hija in parte.get("parts") or []:
            recorrer(hija)

    recorrer(payload)
    return "\n".join(parte.strip() for parte in partes if parte.strip())


def _es_pdf(mime, nombre):
    return mime == "application/pdf" or nombre.lower().endswith(".pdf")


def _listar_partes(payload, condicion):
    """Metadatos de las partes que cumplen la condición. Los bytes se piden solo si hacen falta."""
    encontradas = []

    def recorrer(parte):
        mime = parte.get("mimeType", "")
        nombre = (parte.get("filename") or "").strip()
        if condicion(mime, nombre):
            body = parte.get("body") or {}
            encontradas.append(
                {
                    "nombre": nombre,
                    "mime": mime or "application/pdf",
                    "tamano": body.get("size", 0),
                    "attachment_id": body.get("attachmentId") or "",
                    "data": body.get("data") or "",
                }
            )
            return
        for hija in parte.get("parts") or []:
            recorrer(hija)

    recorrer(payload)
    return encontradas


def _adjuntos(payload):
    encontrados = []

    def recorrer(parte):
        nombre = (parte.get("filename") or "").strip()
        if nombre:
            body = parte.get("body") or {}
            encontrados.append(
                {
                    "nombre": nombre,
                    "mime": parte.get("mimeType", ""),
                    "tamano": body.get("size", 0),
                }
            )
        for hija in parte.get("parts") or []:
            recorrer(hija)

    recorrer(payload)
    return encontrados


def _texto_pdfs(servicio, message_id, payload, clog):
    """Extrae texto si es posible; un fallo nunca elimina el PDF de los anexos."""
    textos = []

    def recorrer(parte):
        mime = parte.get("mimeType", "")
        if mime.startswith("image/"):
            return
        if mime == "application/pdf":
            nombre = (parte.get("filename") or "").strip() or "adjunto.pdf"
            data = bytes_parte(servicio, message_id, parte)
            if data:
                try:
                    textos.append(extraer_texto_pdf(data))
                except Exception as error:
                    if es_infraestructura(error):
                        clog.warning(
                            "PDF sin leer (%s); se adjunta igual: %s",
                            resumir_error(error),
                            nombre,
                        )
                    else:
                        clog.warning(
                            "PDF bloqueado o dañado; se adjunta igual: %s", nombre
                        )
        for hija in parte.get("parts") or []:
            recorrer(hija)

    recorrer(payload)
    return "\n".join(t.strip() for t in textos if t and t.strip()).strip()
