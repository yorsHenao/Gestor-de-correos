"""Pasos de Google: credentials.json, autorización, etiquetas de Gmail y Drive propio.

Reglas: nunca borra, nunca comparte (no hay llamadas a permissions ni delete) y no duplica:
antes de crear busca por el ID del .env, luego por la marca appProperties y luego por nombre.
"""

import json
import shutil
from pathlib import Path

from googleapiclient.errors import HttpError

from instalador import recursos


# ── credentials.json ─────────────────────────────────────────────────────

def credencial_valida(ruta):
    """(True, "") si es un JSON OAuth de Google; si no, (False, motivo)."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False, "no es un archivo JSON legible"
    if not isinstance(datos, dict):
        return False, "no es un JSON de credenciales de Google"
    if datos.get("type") == "service_account":
        return False, "es una cuenta de servicio; se necesita un cliente OAuth (Aplicación de escritorio)"
    bloque = datos.get("installed") or datos.get("web")
    if not isinstance(bloque, dict):
        return False, "no tiene la sección 'installed' (cliente OAuth de escritorio)"
    if not bloque.get("client_id") or not bloque.get("client_secret"):
        return False, "le faltan client_id o client_secret"
    return True, ""


def elegir_archivo():
    """Diálogo de Windows para elegir el JSON. Devuelve la ruta o None si se cancela."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        texto = input("  Escribe o pega la ruta completa del archivo JSON (Enter para cancelar): ")
        return texto.strip().strip('"') or None

    ventana = tkinter.Tk()
    ventana.withdraw()
    ventana.attributes("-topmost", True)
    try:
        ruta = filedialog.askopenfilename(
            title="Elige el archivo de credenciales de Google (JSON descargado de Google Cloud)",
            filetypes=[("Credenciales JSON", "*.json"), ("Todos los archivos", "*.*")],
        )
    finally:
        ventana.destroy()
    return ruta or None


def asegurar_credenciales(destino, elegir=None, avisar=print, intentos=5):
    elegir = elegir or elegir_archivo
    destino = Path(destino)
    if destino.exists():
        ok, motivo = credencial_valida(destino)
        if ok:
            return True
        avisar(f"  El credentials.json actual no sirve ({motivo}). Elige otro.")
    for _ in range(intentos):
        ruta = elegir()
        if not ruta:
            avisar("  No se eligió ningún archivo.")
            return False
        ok, motivo = credencial_valida(ruta)
        if ok:
            destino.parent.mkdir(parents=True, exist_ok=True)
            if Path(ruta).resolve() != destino.resolve():
                shutil.copyfile(ruta, destino)
            return True
        avisar(f"  Ese archivo no sirve: {motivo}. Elige el JSON del cliente OAuth.")
    return False


# ── Etiquetas de Gmail ───────────────────────────────────────────────────

def _mapa_etiquetas(gmail):
    lista = gmail.users().labels().list(userId="me").execute().get("labels", [])
    return {e["name"].casefold(): e["id"] for e in lista}, {e["id"] for e in lista}


def asegurar_etiquetas(gmail, valores_env, nombres=None):
    """{variable: id} para cada etiqueta. Reusa el ID del .env si existe, si no busca por
    nombre exacto (Gmail no distingue mayúsculas) y solo crea la que falte."""
    nombres = nombres or recursos.etiquetas()
    por_nombre, ids = _mapa_etiquetas(gmail)
    resultado, creadas = {}, []
    for variable, nombre in nombres.items():
        actual = (valores_env.get(variable) or "").strip()
        if actual in ids:
            resultado[variable] = actual
            continue
        clave = nombre.casefold()
        if clave not in por_nombre:
            try:
                nueva = gmail.users().labels().create(userId="me", body={
                    "name": nombre,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                }).execute()
                por_nombre[clave] = nueva["id"]
                creadas.append(nombre)
            except HttpError as error:
                if getattr(error, "status_code", None) != 409 and "409" not in str(error):
                    raise
                por_nombre, ids = _mapa_etiquetas(gmail)
                if clave not in por_nombre:
                    raise
        resultado[variable] = por_nombre[clave]
    return resultado, creadas


# ── Drive ────────────────────────────────────────────────────────────────

def _escapar(texto):
    return texto.replace("\\", "\\\\").replace("'", "\\'")


def _vigente(drive, file_id, mime=None):
    if not file_id:
        return False
    try:
        meta = drive.files().get(fileId=file_id, fields="id,mimeType,trashed").execute()
    except HttpError:
        return False
    return not meta.get("trashed") and (mime is None or meta.get("mimeType") == mime)


def _buscar(drive, consulta):
    archivos = drive.files().list(
        q=consulta + " and trashed=false and 'me' in owners",
        fields="files(id,name)", spaces="drive", pageSize=10,
    ).execute().get("files", [])
    return archivos[0]["id"] if archivos else None


def _buscar_existente(drive, rol, nombre, mime, padre):
    hallado = _buscar(drive, f"appProperties has {{ key='{recursos.MARCA}' and value='{rol}' }}")
    if hallado:
        return hallado
    return _buscar(drive, f"name='{_escapar(nombre)}' and mimeType='{mime}' and '{padre}' in parents")


def _asegurar(drive, rol, nombre, mime, padre, id_env=None, crear=None):
    """(id, creado). Orden: ID del .env vigente → marca → nombre en la carpeta → crear."""
    if _vigente(drive, id_env, mime):
        return id_env, False
    hallado = _buscar_existente(drive, rol, nombre, mime, padre)
    if hallado:
        return hallado, False
    cuerpo = {"name": nombre, "mimeType": mime, "parents": [padre],
              "appProperties": {recursos.MARCA: rol}}
    if crear:
        return crear(cuerpo), True
    return drive.files().create(body=cuerpo, fields="id").execute()["id"], True


def _subir_plantilla(drive, ruta_docx):
    from googleapiclient.http import MediaFileUpload

    def crear(cuerpo):
        media = MediaFileUpload(str(ruta_docx), mimetype=recursos.MIME_DOCX, resumable=False)
        return drive.files().create(body=cuerpo, media_body=media, fields="id").execute()["id"]
    return crear


def _encabezados(sheets, hoja_id, encabezados, avisar):
    ultima = chr(ord("A") + len(encabezados) - 1)
    fila = sheets.spreadsheets().values().get(
        spreadsheetId=hoja_id, range=f"A1:{ultima}1"
    ).execute().get("values", [])
    if not fila or not any(str(v).strip() for v in fila[0]):
        sheets.spreadsheets().values().update(
            spreadsheetId=hoja_id, range=f"A1:{ultima}1",
            valueInputOption="RAW", body={"values": [encabezados]},
        ).execute()
    elif [str(v).strip() for v in fila[0]] != encabezados:
        avisar("  [AVISO] La fila 1 de la hoja tiene otros títulos; no se cambian (el programa usa solo la posición de las columnas).")


def asegurar_drive(drive, sheets, valores_env, avisar=print):
    """Crea (o reutiliza) legal-ops, sus subcarpetas, las 2 hojas y las 5 plantillas.
    Devuelve ({variable: id}, [nombres creados])."""
    ids, creados = {}, []

    def nota(nombre, creado):
        if creado:
            creados.append(nombre)

    raiz, c = _asegurar(drive, "raiz", recursos.CARPETA_RAIZ, recursos.MIME_CARPETA, "root")
    nota(recursos.CARPETA_RAIZ, c)
    carpetas = {}
    for rol, nombre, variable in recursos.SUBCARPETAS:
        carpetas[rol], c = _asegurar(drive, rol, nombre, recursos.MIME_CARPETA, raiz,
                                     id_env=valores_env.get(variable) if variable else None)
        nota(nombre, c)
        if variable:
            ids[variable] = carpetas[rol]

    for variable, nombre, encabezados in recursos.HOJAS:
        hoja, c = _asegurar(drive, variable, nombre, recursos.MIME_HOJA, carpetas["hojas"],
                            id_env=valores_env.get(variable))
        nota(nombre, c)
        _encabezados(sheets, hoja, encabezados, avisar)
        ids[variable] = hoja

    for variable, titulo, _ in recursos.PLANTILLAS:
        docx = recursos.PLANTILLAS_DIR / f"{variable}.docx"
        if _vigente(drive, valores_env.get(variable), recursos.MIME_DOC):
            ids[variable] = valores_env[variable]
            continue
        if not docx.exists():
            avisar(f"  [FALTA] No está {docx.name} en la carpeta plantillas/ del proyecto.")
            continue
        ids[variable], c = _asegurar(drive, variable, titulo, recursos.MIME_DOC, carpetas["plantillas"],
                                     crear=_subir_plantilla(drive, docx))
        nota(titulo, c)
    return ids, creados
