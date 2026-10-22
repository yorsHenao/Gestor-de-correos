import io
import re

import pdfplumber
import pypdfium2 as pdfium
from rapidocr_onnxruntime import RapidOCR

from ia_cliente import llamar_gemini_vision
from utils.logger import obtener_log

log = obtener_log()


_OCR_LOCAL = RapidOCR()


def _texto_util(texto):
    if not texto or len(texto.strip()) < 8:
        return False
    caracteres_validos = len(re.findall(r"[\w@.,:;#%+\-]", texto, re.UNICODE))
    return caracteres_validos / max(len(texto), 1) >= 0.35 and "�" not in texto


def _texto_nativo(pdf_bytes):
    paginas = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pagina in pdf.pages:
            paginas.append(pagina.extract_text() or "")
    return "\n".join(paginas).strip()


def _texto_ocr_local(pdf_bytes):
    documento = pdfium.PdfDocument(pdf_bytes)
    paginas = []
    for pagina in documento:
        imagen = pagina.render(scale=2).to_pil()
        resultado, _ = _OCR_LOCAL(imagen)
        if resultado:
            paginas.append("\n".join(elemento[1] for elemento in resultado))
    return "\n".join(paginas).strip()


def extraer_texto_pdf(pdf_bytes):
    texto = _texto_nativo(pdf_bytes)
    if _texto_util(texto):
        return texto

    texto = _texto_ocr_local(pdf_bytes)
    if _texto_util(texto):
        log.debug("OCR: texto extraído localmente con RapidOCR")
        return texto

    log.info("OCR: sin texto local; se usa Gemini como último respaldo")
    return llamar_gemini_vision(
        pdf_bytes,
        "Transcribe todo el texto visible en este documento, tal cual aparece, sin resumir ni omitir nada.",
    )