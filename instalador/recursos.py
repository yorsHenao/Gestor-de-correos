"""Qué crea el instalador en la cuenta de cada persona (nombres, encabezados y plantillas)."""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLAS_DIR = RAIZ / "plantillas"
ARCHIVO_ETIQUETAS = Path(__file__).resolve().parent / "etiquetas.json"

MIME_CARPETA = "application/vnd.google-apps.folder"
MIME_HOJA = "application/vnd.google-apps.spreadsheet"
MIME_DOC = "application/vnd.google-apps.document"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Marca que llevan en Drive los archivos creados por el instalador (para no duplicarlos).
MARCA = "proyecto_e_rol"

CARPETA_RAIZ = "legal-ops"

# rol, nombre, variable del .env (None = solo organiza)
SUBCARPETAS = [
    ("plantillas", "01_Plantillas_Docs", None),
    ("hojas", "02_Hojas_de_Control", None),
    ("respuestas", "03_Respuestas_Docs", "DRIVE_RESPONSES_FOLDER"),
    ("pdf", "04_Respuestas_PDF", "DRIVE_PDF_FOLDER"),
    ("capturas", "05_Capturas_Temporales", "DRIVE_SCREENSHOTS_FOLDER"),
]

# Encabezados de la fila 1. El código solo usa posiciones de columna (storage.py: A:G,
# clave en G; entes/hoja.py: A:G, lee F:H, H es de QA). Textos tomados de las hojas en uso.
HOJAS = [
    ("DRIVE_SHEETS_ID", "Control de Casos RT", [
        "nombre", "correo del peticionario", "id_rt", "estado", "fecha",
        "hilo de Slack", "id del mensaje",
    ]),
    ("DRIVE_SHEETS_ENTES_ID", "Control de Casos Entes", [
        "peticionario", "correo", "id usuario", "fecha peticion", "causal",
        "estado", "Id - Gmail", "flujo completo",
    ]),
]

_RT = [
    "{{ fecha }}", "{{ lupe.nombre | title }}", "{{ lupe.correo | title }}",
    "{{ correo.peticion  }}", "{{causales.descripcion }}", "{{ causales.numerales }}",
]
_FOTO = ["{% if foto_prueba %}", "{{ foto_prueba }}", "{% endif %}"]
_RL = ["{{ nombre.rl | title }}", "{{ correo.rl | title }}"]

# variable, título del Google Doc, marcadores que deben aparecer literal
PLANTILLAS = [
    ("DOC_TEMPLATE_BLOCKED", "Formato_Respuesta_Derecho_de_Peticion_Rt_bloqueado", _RT + _FOTO),
    ("DOC_TEMPLATE_RT_RL", "Formato_Respuesta_Derecho_de_Peticion_Rt_RL_bloqueado", _RT + _RL + _FOTO),
    ("DOC_TEMPLATE_RT_WITHOUT_ANEXOS", "Formato_Respuesta_Derecho_de_Peticion_rt_sin_anexos", list(_RT)),
    ("DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS", "Formato_Respuesta_Derecho_de_Peticion_rt_rl_sin_anexos", _RT + _RL),
    ("DOC_TEMPLATE_THIRD_PARTY", "Formato_Respuesta_Derecho_de_Peticion_Terceros", [
        "{{ fecha }}", "{{ nombre | title }}", "{{ correo | title }}", "{{ peticion }}", "{{ id }}",
    ]),
]


def etiquetas():
    """{variable: nombre exacto} desde instalador/etiquetas.json."""
    datos = json.loads(ARCHIVO_ETIQUETAS.read_text(encoding="utf-8"))
    return {k: v for k, v in datos.items() if k.startswith("GMAIL_LABEL_")}
