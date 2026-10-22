
#Validación de las variables críticas del .env.

import os
import sys
from pathlib import Path

from .exceptions import ConfigurationError

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import config
CRITICAS = [
    "DRIVE_SHEETS_ID",
    "DRIVE_RESPONSES_FOLDER",
    "GMAIL_LABEL_ID",
    "GMAIL_REPLY_TO",
    "SLACK_REVIEW_CHANNEL_ID",
]


def validar_criticos():
    """Lanza ConfigurationError si falta una variable esencial del .env."""
    if not config.GROQ_API_KEY and not config.GEMINI_API_KEY:
        raise ConfigurationError(
            "Faltan credenciales de IA: configure GROQ_API_KEY o GEMINI_API_KEY en el .env."
        )

    faltantes = [clave for clave in CRITICAS if not os.getenv(clave)]
    if faltantes:
        raise ConfigurationError(
            message=f"Variables críticas faltantes en el .env: {', '.join(faltantes)}",
            details="Revise que el .env tenga todos los valores requeridos.",
        )
