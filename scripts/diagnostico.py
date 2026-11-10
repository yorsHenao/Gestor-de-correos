"""Diagnóstico de SOLO LECTURA del Proyecto E.

    python scripts/diagnostico.py

Revisa internet, .env, token de Google, Gmail, Drive, hojas, plantillas, Slack, IA,
Chromium y la sesión de Lupe. No escribe, no envía y no borra nada (tampoco el token:
no usa autenticar()). Sale con código 1 si algo está en [FALTA].
"""

import os
import socket
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for ruta in (str(RAIZ), str(RAIZ / "scripts")):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

REINSTALAR = "vuelve a correr INSTALAR.bat"


class Informe:
    def __init__(self):
        self.fallos = 0
        self.avisos = 0

    def ok(self, texto):
        print(f"[OK]    {texto}")

    def falta(self, texto, como=REINSTALAR):
        self.fallos += 1
        print(f"[FALTA] {texto}")
        if como:
            print(f"        → {como}")

    def aviso(self, texto, como=""):
        self.avisos += 1
        print(f"[AVISO] {texto}")
        if como:
            print(f"        → {como}")


def _codigo(error):
    for atributo in ("status_code", "code"):
        valor = getattr(error, atributo, None)
        if isinstance(valor, int):
            return valor
    resp = getattr(error, "resp", None)
    return getattr(resp, "status", None)


def _sin_conexion(inf, que, error):
    """Un fallo de red o DNS no se arregla reinstalando: aviso y repetir."""
    from utils.exceptions import es_falla_de_red

    if not es_falla_de_red(error):
        return False
    inf.aviso(f"{que}: sin conexión momentánea (red o DNS)",
              "Repite el diagnóstico; si se repite, revisa la red o la VPN")
    return True


def revisar_internet(inf, host="www.googleapis.com"):
    try:
        socket.create_connection((host, 443), timeout=5).close()
        inf.ok("Internet")
        return True
    except OSError as error:
        inf.falta(f"Sin conexión a internet ({error})", "Revisa la red o la VPN y vuelve a correr el diagnóstico")
        return False


def revisar_env(inf, config):
    if not (RAIZ / ".env").exists():
        inf.falta("No existe el archivo .env")
        return
    from utils.config_manager import validar_criticos

    try:
        validar_criticos()
        inf.ok(".env con las variables obligatorias")
    except Exception as error:
        inf.falta(f".env: {getattr(error, 'message', None) or error}")
    if not config.SLACK_BOT_TOKEN:
        inf.falta("Falta SLACK_BOT_TOKEN en el .env", "Pide el token del bot a Jeisembert y " + REINSTALAR)


def cargar_token(inf, config):
    """Credenciales en memoria. Si hay que renovarlas, se renuevan sin guardar ni borrar el archivo."""
    from autenticar import SCOPES
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    ruta = Path(config.GOOGLE_TOKEN_FILE)
    if not Path(config.GOOGLE_CREDENTIALS_FILE).exists():
        inf.falta("No está data-json/credentials.json")
    if not ruta.exists():
        inf.falta("No hay token de Google (data-json/token.json)")
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(ruta))
    except ValueError:
        inf.falta("token.json está dañado")
        return None
    faltan = sorted(set(SCOPES) - set(creds.scopes or []))
    if faltan:
        inf.falta("Al token le faltan permisos: " + ", ".join(s.rsplit("/", 1)[-1] for s in faltan),
                  "Borra data-json/token.json y " + REINSTALAR)
        return None
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as error:
            if _sin_conexion(inf, "Token de Google", error):
                return None
            inf.falta(f"El token de Google no se pudo renovar ({type(error).__name__})",
                      "Si hay internet, el token fue revocado: " + REINSTALAR)
            return None
    inf.ok("Token de Google y permisos")
    return creds


def revisar_gmail(inf, gmail, config):
    from instalador import recursos

    try:
        lista = gmail.users().labels().list(userId="me").execute().get("labels", [])
    except Exception as error:
        if not _sin_conexion(inf, "Gmail", error):
            inf.falta(f"Gmail no responde ({_codigo(error) or error})")
        return
    ids = {e["id"]: e["name"] for e in lista}
    malas = [v for v in recursos.etiquetas() if getattr(config, v, "") not in ids]
    if malas:
        inf.falta("Etiquetas de Gmail sin configurar o inexistentes: " + ", ".join(malas))
    else:
        inf.ok("Gmail y etiquetas")


def revisar_drive(inf, drive, config):
    from instalador import recursos

    def archivo(file_id):
        meta = drive.files().get(fileId=file_id, fields="id,name,mimeType,trashed").execute()
        if meta.get("trashed"):
            raise LookupError("está en la papelera")
        return meta

    bien = True
    for _, _, variable in recursos.SUBCARPETAS:
        if not variable:
            continue
        valor = getattr(config, variable, "")
        if not valor:
            if variable == "DRIVE_RESPONSES_FOLDER":
                inf.falta(f"{variable} vacío")
                bien = False
            continue
        try:
            if archivo(valor)["mimeType"] != recursos.MIME_CARPETA:
                raise LookupError("no es una carpeta")
        except Exception as error:
            if not _sin_conexion(inf, f"Carpeta {variable}", error):
                inf.falta(f"Carpeta {variable} no accesible ({_codigo(error) or error})")
            bien = False
    if bien:
        inf.ok("Carpetas de Drive")


def revisar_hojas(inf, sheets, config):
    from instalador import recursos

    for variable, nombre, encabezados in recursos.HOJAS:
        hoja = getattr(config, variable, "")
        if not hoja:
            inf.falta(f"{variable} vacío ({nombre})")
            continue
        ultima = chr(ord("A") + len(encabezados) - 1)
        try:
            fila = sheets.spreadsheets().values().get(
                spreadsheetId=hoja, range=f"A1:{ultima}1").execute().get("values", [])
        except Exception as error:
            if not _sin_conexion(inf, f"Hoja {nombre}", error):
                inf.falta(f"Hoja {nombre} no accesible ({_codigo(error) or error})")
            continue
        actual = [str(v).strip() for v in (fila[0] if fila else [])]
        if actual == encabezados:
            inf.ok(f"Hoja {nombre} y encabezados")
        elif not any(actual):
            inf.falta(f"Hoja {nombre} sin encabezados en la fila 1")
        else:
            inf.aviso(f"Hoja {nombre}: la fila 1 tiene otros títulos (el programa usa solo la posición de las columnas)")


def _texto_doc(documento):
    partes = []

    def recorrer(contenido):
        for bloque in contenido or []:
            for el in (bloque.get("paragraph") or {}).get("elements", []):
                partes.append((el.get("textRun") or {}).get("content", ""))
            for fila in (bloque.get("table") or {}).get("tableRows", []):
                for celda in fila.get("tableCells", []):
                    recorrer(celda.get("content"))

    recorrer((documento.get("body") or {}).get("content"))
    for seccion in ("headers", "footers"):
        for parte in (documento.get(seccion) or {}).values():
            recorrer(parte.get("content"))
    return "".join(partes)


def revisar_plantillas(inf, docs, config):
    from instalador import recursos

    bien = True
    for variable, titulo, marcadores in recursos.PLANTILLAS:
        doc_id = getattr(config, variable, "")
        if not doc_id:
            inf.falta(f"{variable} vacío ({titulo})")
            bien = False
            continue
        try:
            texto = _texto_doc(docs.documents().get(documentId=doc_id).execute())
        except Exception as error:
            if not _sin_conexion(inf, f"Plantilla {variable}", error):
                inf.falta(f"Plantilla {variable} no accesible o no es Google Docs ({_codigo(error) or error})")
            bien = False
            continue
        faltan = [m for m in marcadores if m not in texto]
        if faltan:
            inf.falta(f"Plantilla {variable}: faltan marcadores {', '.join(faltan)}",
                      "Corrige el Google Doc o bórralo de legal-ops y " + REINSTALAR)
            bien = False
    if bien:
        inf.ok("Plantillas de Google Docs y marcadores")


def revisar_slack(inf, config, cliente=None):
    if not config.SLACK_BOT_TOKEN:
        return
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    cliente = cliente or WebClient(token=config.SLACK_BOT_TOKEN, timeout=15)
    try:
        cliente.auth_test()
    except SlackApiError as error:
        inf.falta(f"Slack rechaza el token del bot ({error.response.get('error')})",
                  "Pide de nuevo el token a Jeisembert y " + REINSTALAR)
        return
    except Exception as error:
        inf.falta(f"Slack no responde ({type(error).__name__})", "Revisa la conexión")
        return
    try:
        cliente.conversations_history(channel=config.SLACK_REVIEW_CHANNEL_ID, limit=1)
        inf.ok("Slack: bot y canal de revisión")
    except SlackApiError as error:
        inf.falta(f"Slack: el bot no puede leer el canal de revisión ({error.response.get('error')})",
                  "Pide que agreguen el bot al canal " + (config.SLACK_REVIEW_CHANNEL_NAME or ""))


def _motivo_ia(error):
    from utils.exceptions import es_falla_de_red

    if es_falla_de_red(error):
        return "aviso", "sin conexión momentánea (red o DNS); repite el diagnóstico"
    codigo = _codigo(error)
    texto = str(error).lower()
    if codigo in (401, 403) or "api_key_invalid" in texto or "invalid api key" in texto:
        return "falta", "la clave no es válida (401)"
    if codigo == 429 or "rate" in texto and "limit" in texto:
        return "aviso", "clave válida pero sin cuota por ahora (429)"
    if codigo == 404 or "model" in texto and ("not found" in texto or "does not exist" in texto):
        return "falta", "el modelo configurado no existe (404)"
    if codigo == 504 or "deadline_exceeded" in texto or "timed out" in texto:
        return "aviso", "clave válida pero la IA no respondió a tiempo (504)"
    return "falta", f"error {codigo or type(error).__name__}"


def revisar_ia(inf, config, groq_cliente=None, gemini_cliente=None):
    resultados = []
    clave_groq = config.GROQ_API_KEY2 or config.GROQ_API_KEY
    if clave_groq:
        try:
            if groq_cliente is None:
                from groq import Groq
                groq_cliente = Groq(api_key=clave_groq, timeout=20, max_retries=0)
            groq_cliente.chat.completions.create(
                model=config.GROQ_MODEL, messages=[{"role": "user", "content": "di ok"}], max_tokens=16)
            resultados.append(("Groq", "ok", ""))
        except Exception as error:
            resultados.append((f"Groq ({config.GROQ_MODEL})",) + _motivo_ia(error))
    if config.GEMINI_API_KEY:
        try:
            from google.genai import types
            if gemini_cliente is None:
                from google import genai
                gemini_cliente = genai.Client(
                    api_key=config.GEMINI_API_KEY,
                    http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_SEG * 1000),
                )
            gemini_cliente.models.generate_content(
                model=config.GEMINI_MODEL, contents="di ok",
                config=types.GenerateContentConfig(max_output_tokens=16))
            resultados.append(("Gemini", "ok", ""))
        except Exception as error:
            resultados.append((f"Gemini ({config.GEMINI_MODEL})",) + _motivo_ia(error))
    if not resultados:
        inf.falta("No hay claves de IA (GROQ_API_KEY o GEMINI_API_KEY)")
        return
    alguna = any(estado != "falta" for _, estado, _ in resultados)
    for nombre, estado, motivo in resultados:
        if estado == "ok":
            inf.ok(f"IA {nombre}")
        elif estado == "aviso" or alguna:
            inf.aviso(f"IA {nombre}: {motivo}", "El programa usará el otro proveedor de respaldo" if alguna else "")
        else:
            inf.falta(f"IA {nombre}: {motivo}", "Revisa la clave o el modelo en el .env y " + REINSTALAR)


def revisar_navegador(inf, config):
    if config.LUPE_CHANNEL == "chrome":
        inf.ok("Navegador: se usa Google Chrome instalado (LUPE_CHANNEL=chrome)")
    else:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                ruta = p.chromium.executable_path
            if ruta and Path(ruta).exists():
                inf.ok("Chromium de Playwright")
            else:
                inf.falta("Chromium de Playwright no instalado", "Corre: .venv\\Scripts\\python -m playwright install chromium")
        except Exception as error:
            inf.falta(f"Playwright no disponible ({type(error).__name__})", REINSTALAR)
    try:
        import lupe_navegador

        estado = lupe_navegador.estado_sesion()
    except Exception:
        estado = "desconocido"
    if estado == "vigente":
        inf.ok("Sesión de Lupe")
    else:
        inf.aviso(f"Sesión de Lupe: {estado}",
                  "Usa «Iniciar sesión en Lupe» en el Dashboard o corre «python main.py login»")


def diagnosticar(construir=None):
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env", override=True)
    import config

    inf = Informe()
    hay_red = revisar_internet(inf)
    revisar_env(inf, config)
    creds = cargar_token(inf, config) if hay_red else None
    if creds is not None:
        if construir is None:
            from googleapiclient.discovery import build as construir
        revisar_gmail(inf, construir("gmail", "v1", credentials=creds, cache_discovery=False), config)
        revisar_drive(inf, construir("drive", "v3", credentials=creds, cache_discovery=False), config)
        revisar_hojas(inf, construir("sheets", "v4", credentials=creds, cache_discovery=False), config)
        revisar_plantillas(inf, construir("docs", "v1", credentials=creds, cache_discovery=False), config)
    elif hay_red:
        inf.falta("Gmail, Drive, hojas y plantillas sin revisar", "Primero hace falta el token de Google")
    if hay_red:
        revisar_slack(inf, config)
        revisar_ia(inf, config)
    revisar_navegador(inf, config)
    print()
    if inf.fallos:
        print(f"Resultado: {inf.fallos} punto(s) en [FALTA]. Corrígelos y vuelve a correr INSTALAR.bat.")
    else:
        print("Resultado: todo listo" + (f" ({inf.avisos} aviso(s))." if inf.avisos else "."))
    return inf


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(1 if diagnosticar().fallos else 0)
