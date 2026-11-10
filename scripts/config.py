#!/usr/bin/env python3
"""
Módulo de configuración para scripts del Proyecto E.

Este módulo mantiene compatibilidad con la carga original de variables
desde el archivo .env usando os.getenv(), para no romper la importación
de otros scripts (clasificar_correos_gmail.py, procesar_correos_rt.py, etc.).

También expone una función utilitaria para obtener configuración
centralizada cuando se ejecuta desde main.py.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# --- Rutas base del proyecto ---
BASE_DIR = Path(__file__).parent.parent

# --- Carga del .env ---
load_dotenv(BASE_DIR / ".env")


def _numero_env(variable, defecto, tipo=int):
    """Número del .env; si la línea está vacía o mal escrita, usa el valor por defecto."""
    texto = (os.getenv(variable) or "").strip()
    if not texto:
        return tipo(defecto)
    try:
        return tipo(texto)
    except ValueError:
        print(f"Aviso: {variable}={texto!r} no es un número válido; se usa {defecto}", file=sys.stderr)
        return tipo(defecto)


def _ruta_env(variable, defecto):
    texto = (os.getenv(variable) or "").strip()
    ruta = Path(texto) if texto else BASE_DIR / defecto
    if not ruta.is_absolute():
        ruta = BASE_DIR / ruta
    return ruta


# --- Variables de Autenticación Google ---
GOOGLE_CREDENTIALS_FILE = _ruta_env("GOOGLE_CREDENTIALS_FILE", Path("data-json") / "credentials.json")
GOOGLE_TOKEN_FILE = _ruta_env("GOOGLE_TOKEN_FILE", Path("data-json") / "token.json")
LUPE_SESSION_FILE = Path(__file__).parent / "sesion.json"

# --- Lupe: perfil persistente de Chromium y sesión ---
# El perfil guarda cookies de Google/Lupe; va fuera del repo.
_DATOS_LOCALES = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "share")
LUPE_PROFILE_DIR = Path(
    os.getenv("LUPE_PROFILE_DIR") or _DATOS_LOCALES / "ProyectoE" / "lupe_profile"
)
# interactivo: si Google pide clave, espera a una persona. automatico: sale con código 20.
LUPE_MODO = os.getenv("LUPE_MODO", "interactivo").strip().lower()
LUPE_REINGRESO_SEG = _numero_env("LUPE_REINGRESO_SEG", "30")
LUPE_LOGIN_MAX_SEG = _numero_env("LUPE_LOGIN_MAX_SEG", "300")
# Vacío = Chromium de Playwright; "chrome" = Google Chrome instalado.
LUPE_CHANNEL = os.getenv("LUPE_CHANNEL", "").strip()
LUPE_LOCK_ESPERA_SEG = _numero_env("LUPE_LOCK_ESPERA_SEG", "1800")
LUPE_SESION_ESTADO = BASE_DIR / "logs" / "lupe_sesion_estado.json"

# --- Cuenta que recibe las respuestas legales ---
GMAIL_REPLY_TO = os.getenv("GMAIL_REPLY_TO", "").strip()

# Canales y usuario de Slack usados por el flujo de revision.
SLACK_REVIEW_CHANNEL_ID = os.getenv("SLACK_REVIEW_CHANNEL_ID", "").strip()
SLACK_REVIEW_CHANNEL_NAME = os.getenv("SLACK_REVIEW_CHANNEL_NAME", "").strip()

SLACK_REVIEW_USER_FRAUD = os.getenv("SLACK_REVIEW_USER_FRAUD", "")
SLACK_REVIEW_USER_DISCIPLINE = os.getenv("SLACK_REVIEW_USER_DISCIPLINE", "")

# Etiquetas de Gmail usadas para clasificar correos.
GMAIL_LABEL_ID = os.getenv("GMAIL_LABEL_ID", "")
GMAIL_LABEL_REVISION_MANUAL = os.getenv("GMAIL_LABEL_REVISION_MANUAL", "")
GMAIL_LABEL_ENTES = os.getenv("GMAIL_LABEL_ENTES", "")
GMAIL_LABEL_OTROS = os.getenv("GMAIL_LABEL_OTROS", "")
GMAIL_LABEL_FACTURACION = os.getenv("GMAIL_LABEL_FACTURACION", "")
GMAIL_LABEL_RESTAURANTES = os.getenv("GMAIL_LABEL_RESTAURANTES", "")
GMAIL_LABEL_ELIMINAR_DEUDA_RTS = os.getenv("GMAIL_LABEL_ELIMINAR_DEUDA_RTS", "")

# Carpetas y archivos de Drive/Sheets usados para los casos.
DRIVE_SHEETS_ID = os.getenv("DRIVE_SHEETS_ID", "")
DRIVE_RESPONSES_FOLDER = os.getenv("DRIVE_RESPONSES_FOLDER", "")
DRIVE_PDF_FOLDER = os.getenv("DRIVE_PDF_FOLDER", "")
DRIVE_SCREENSHOTS_FOLDER = os.getenv("DRIVE_SCREENSHOTS_FOLDER", "")

# Plantillas de Google Docs por tipo de respuesta.
DOC_TEMPLATE_BLOCKED = os.getenv("DOC_TEMPLATE_BLOCKED", "")
DOC_TEMPLATE_THIRD_PARTY = os.getenv("DOC_TEMPLATE_THIRD_PARTY", "")
DOC_TEMPLATE_RT_RL = os.getenv("DOC_TEMPLATE_RT_RL", "")
DOC_TEMPLATE_RT_WITHOUT_ANEXOS = os.getenv("DOC_TEMPLATE_RT_WITHOUT_ANEXOS", "")
DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS = os.getenv("DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS", "")

# Credenciales y proveedor de servicios externos.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_KEY2 = os.getenv("GROQ_API_KEY2")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IA_PROVIDER = os.getenv("IA_PROVIDER", "groq").lower()
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
GEMINI_TIMEOUT_SEG = _numero_env("GEMINI_TIMEOUT_SEG", "120")
URL_LUPE = os.getenv("URL_LUPE", "").strip() or "https://lupe.rappi.com/"
URL_FORMULARIO = os.getenv("URL_FORMULARIO", "").strip() or "https://master.d3i85um4nwfu2v.amplifyapp.com/"
CORREOS_REVISION_MANUAL = tuple(
    correo.strip().lower()
    for correo in os.getenv("CORREOS_REVISION_MANUAL", "").split(",")
    if correo.strip()
)
DOMINIOS_REVISION_MANUAL = tuple(dict.fromkeys(
    dominio.strip().lower().lstrip("@.")
    for dominio in (
        "fiscalia.gov.co,policia.gov.co,ramajudicial.gov.co,"
        + os.getenv("DOMINIOS_REVISION_MANUAL", "")
    ).split(",")
    if dominio.strip().lstrip("@.")
))
VENTANA_PETICION_REPETIDA_DIAS = _numero_env("VENTANA_PETICION_REPETIDA_DIAS", "60")
CONTROL_CALIDAD = os.getenv("CONTROL_CALIDAD", "false").lower() == "true"
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# Entes: hoja de registro, formulario y reintentos entre rondas.
DRIVE_SHEETS_ENTES_ID = os.getenv("DRIVE_SHEETS_ENTES_ID", "")
ENTES_MAX_CORREOS = _numero_env("ENTES_MAX_CORREOS", "26")
ENTES_TIMEOUT_FORM = _numero_env("ENTES_TIMEOUT_FORM", "120")
ENTES_MAX_INTENTOS = _numero_env("ENTES_MAX_INTENTOS", "3")
ENTES_MAX_FALLOS_RONDA = _numero_env("ENTES_MAX_FALLOS_RONDA", "2")
ENTES_MAX_IMAGENES = max(0, _numero_env("ENTES_MAX_IMAGENES", "3"))

# Servicio automático: ciclo, dashboard y alertas.
CICLO_HORAS = _numero_env("CICLO_HORAS", "4", float)
DASHBOARD_PUERTO = _numero_env("DASHBOARD_PUERTO", "8000")
DASHBOARD_URL = f"http://127.0.0.1:{DASHBOARD_PUERTO}"
ALERTAS_CANALES = [
    canal.strip().lower()
    for canal in os.getenv("ALERTAS_CANALES", "toast,gmail").split(",")
    if canal.strip()
]
ALERTAS_EMAIL = os.getenv("ALERTAS_EMAIL", "")
ALERTAS_SLACK_CANAL = os.getenv("ALERTAS_SLACK_CANAL", "")