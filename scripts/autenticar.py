import os
import time
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest
from httplib2 import ServerNotFoundError
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

REINTENTOS_GOOGLE = 4
_ESPERAS_REFRESH = (2, 4, 8)


class _PeticionConReintentos(HttpRequest):
    """Lecturas: reintenta DNS, conexión, 5xx y 429. Escrituras: solo si la petición no salió."""

    def execute(self, http=None, num_retries=None):
        if num_retries is not None:
            return super().execute(http=http, num_retries=num_retries)
        if self.method == "GET":
            return super().execute(http=http, num_retries=REINTENTOS_GOOGLE)
        # Un 5xx o un corte a mitad de camino pudo haber escrito ya: reintentar duplicaría.
        for intento in range(REINTENTOS_GOOGLE + 1):
            try:
                return super().execute(http=http)
            except (ServerNotFoundError, ConnectionRefusedError) as error:
                if intento == REINTENTOS_GOOGLE:
                    raise
                espera = 2 ** intento
                log.warning(
                    "Google %s %s sin conexión (%s); reintento en %d s",
                    self.method, self.methodId or "", type(error).__name__, espera,
                )
                self._sleep(espera)


def servicio_google(nombre, version, creds=None):
    """Cliente de una API de Google con reintentos ante fallos sueltos de red."""
    return build(
        nombre,
        version,
        credentials=creds or autenticar(),
        requestBuilder=_PeticionConReintentos,
    )


def _renovar(creds):
    for intento, espera in enumerate(_ESPERAS_REFRESH + (None,)):
        try:
            creds.refresh(Request())
            return
        except TransportError as error:
            if espera is None:
                raise
            log.warning(
                "Google: no se pudo renovar el token (%s); reintento %d en %d s",
                type(error).__name__, intento + 1, espera,
            )
            time.sleep(espera)


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
                _renovar(creds)
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
