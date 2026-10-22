"""Extracción de texto de mensajes Gmail (cuerpo, HTML, PDF, DOCX)."""

import base64
import io
import logging
from html.parser import HTMLParser

from docx import Document

logging.getLogger("pdfminer").setLevel(logging.ERROR)


class _TextoHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.partes = []

    def handle_data(self, data):
        if data.strip():
            self.partes.append(data.strip())

    def texto(self):
        return " ".join(self.partes)


def _extraer_texto_html(data):
    parser = _TextoHTML()
    parser.feed(data)
    return parser.texto()


def extraer_contenido(msg, servicio, incluir_adjuntos=True):
    """Devuelve el cuerpo y, aparte, el texto de PDF o Word."""
    texto_cuerpo, texto_adjuntos = _leer_partes(
        msg, servicio, incluir_adjuntos=incluir_adjuntos
    )
    return texto_cuerpo.strip(), texto_adjuntos.strip()


def bytes_parte(servicio, message_id, parte):
    """Bytes de una parte del mensaje: en línea (body.data) o descargando el adjunto."""
    body = parte.get("body") or {}
    data = parte.get("data") or body.get("data") or ""
    if data:
        return base64.urlsafe_b64decode(data)
    adjunto_id = parte.get("attachment_id") or body.get("attachmentId")
    if not adjunto_id:
        return b""
    adjunto = servicio.users().messages().attachments().get(
        userId="me", messageId=message_id, id=adjunto_id
    ).execute()
    return base64.urlsafe_b64decode(adjunto.get("data", ""))


def extraer_cuerpo(msg, servicio):
    texto_cuerpo, texto_adjuntos = extraer_contenido(msg, servicio)
    return f"{texto_cuerpo} {texto_adjuntos}".strip()


def mensaje_tiene_pdf_o_word(msg):
    """True si hay PDF o DOCX; no considera imágenes."""
    encontrados = False

    def _recorrer(partes):
        nonlocal encontrados
        for parte in partes:
            mime = parte.get("mimeType", "")
            if mime in (
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ):
                encontrados = True
                return
            if mime.startswith("multipart"):
                _recorrer(parte.get("parts", []))
                if encontrados:
                    return

    payload = msg.get("payload") or {}
    if payload.get("mimeType") in (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return True
    _recorrer(payload.get("parts", []))
    return encontrados


def _leer_partes(msg, servicio, incluir_adjuntos=True):
    texto_cuerpo = ""
    texto_adjuntos = ""

    def _procesar_partes(partes):
        nonlocal texto_cuerpo, texto_adjuntos
        for parte in partes:
            mime = parte.get("mimeType", "")

            if mime == "text/plain":
                data = parte["body"].get("data", "")
                if data:
                    texto_cuerpo += "\n" + base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )

            elif mime == "text/html":
                data = parte["body"].get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    texto_cuerpo += "\n" + _extraer_texto_html(html)

            elif mime.startswith("multipart"):
                _procesar_partes(parte.get("parts", []))

            elif incluir_adjuntos and mime == "application/pdf":
                adjunto_id = parte["body"].get("attachmentId")
                if adjunto_id:
                    from ocr import extraer_texto_pdf

                    adjunto = servicio.users().messages().attachments().get(
                        userId="me", messageId=msg["id"], id=adjunto_id
                    ).execute()
                    pdf_data = base64.urlsafe_b64decode(adjunto["data"])
                    texto_adjuntos += extraer_texto_pdf(pdf_data)

            elif incluir_adjuntos and mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                adjunto_id = parte["body"].get("attachmentId")
                if adjunto_id:
                    adjunto = servicio.users().messages().attachments().get(
                        userId="me", messageId=msg["id"], id=adjunto_id
                    ).execute()
                    docx_data = base64.urlsafe_b64decode(adjunto["data"])
                    doc_word = Document(io.BytesIO(docx_data))
                    for parrafo in doc_word.paragraphs:
                        if parrafo.text.strip():
                            texto_adjuntos += parrafo.text + "\n"

    try:
        partes = msg["payload"].get("parts", [])
        if not partes:
            data = msg["payload"]["body"].get("data", "")
            if data:
                texto_cuerpo = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                if msg["payload"].get("mimeType") == "text/html":
                    texto_cuerpo = _extraer_texto_html(texto_cuerpo)
        else:
            _procesar_partes(partes)
    except Exception as e:
        logging.getLogger("proyecto_e").error(
            "Error extrayendo el contenido del correo: %s", e, extra={"caso": msg.get("id", "-")}
        )

    return texto_cuerpo, texto_adjuntos
