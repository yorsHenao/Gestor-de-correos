import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError
from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE
from utils.logger import obtener_log

log = obtener_log()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]

def autenticar():
    creds = None
    if os.path.exists(GOOGLE_TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
        except ValueError:
            log.warning("Google: token.json dañado; hay que autorizar de nuevo")
            os.remove(GOOGLE_TOKEN_FILE)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.debug("Google: renovando token")
            try:
                creds.refresh(Request())
            except RefreshError:
                log.warning("Google: el token fue revocado o expiró; hay que autorizar de nuevo")
                creds = None
                if os.path.exists(GOOGLE_TOKEN_FILE):
                    os.remove(GOOGLE_TOKEN_FILE)

        if not creds or not creds.valid:
            # Sin una persona delante, el login del navegador se quedaría esperando para siempre.
            if os.getenv("PROYECTO_E_AUTOMATICO"):
                raise RuntimeError(
                    "Google pide autorizar de nuevo: ejecuta «python main.py paso clasificar» "
                    "en este PC y acepta los permisos en el navegador."
                )
            log.warning("Google: iniciando sesión en el navegador")
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(GOOGLE_TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds
