import json
import logging
from pathlib import Path

from autenticar import autenticar
from googleapiclient.discovery import build
from config import DRIVE_SHEETS_ID

ESTADO_EN_SLACK = "En Slack - pendiente"
ESTADOS_PENDIENTE_SLACK = frozenset({ESTADO_EN_SLACK, "En Slack"})
ESTADOS_DUPLICADO_RT = ESTADOS_PENDIENTE_SLACK | {"Completado"}
CIERRES_QA = frozenset({"Completado", "Caso manual"})

SHEETS_ID = DRIVE_SHEETS_ID
RUTA_MAPEO = Path(__file__).resolve().parent.parent / "data-json" / "mapeo-causales.json"


def _sheets():
    return build("sheets", "v4", credentials=autenticar())


def leer_rango(spreadsheet_id, rango):
    if not spreadsheet_id:
        return []
    resp = _sheets().spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=rango
    ).execute()
    return resp.get("values", [])


def leer_caso(mensaje_id):
    from app import registro

    datos = registro.leer_caso_rt(mensaje_id)
    if not datos:
        raise FileNotFoundError(f"Caso no encontrado: {mensaje_id}")
    return datos


def guardar_caso(mensaje_id, datos):
    from app import registro

    correo = datos.setdefault("correo", {})
    correo["gmail_id"] = correo.get("gmail_id") or mensaje_id
    estado = _estado_caso(datos)
    registro.guardar_caso_rt(datos, estado)
    _actualizar_sheet(datos)


def _estado_caso(info):
    if info.get("cuenta_no_encontrada"):
        if info.get("completado"):
            return "Sin cuenta - enviado"
        return "Sin cuenta - pendiente"
    if (info.get("slack") or {}).get("estado") == "caso_manual":
        return "Caso manual"
    if info.get("completado"):
        return "Completado"
    if info.get("slack", {}).get("thread_id") and not info.get("slack", {}).get("decision"):
        return ESTADO_EN_SLACK
    return "Nuevo"


def sincronizar_cierres_qa():
    from app import registro

    abiertos = {fila["mensaje_id"] for fila in registro.listar_casos_rt_abiertos()}
    if not abiertos or not SHEETS_ID:
        return
    for fila in leer_rango(SHEETS_ID, "D:G"):
        estado_sheet = str(fila[0]).strip() if fila else ""
        mensaje = str(fila[3]).strip() if len(fila) > 3 else ""
        if mensaje not in abiertos or estado_sheet not in CIERRES_QA:
            continue
        datos = registro.leer_caso_rt(mensaje)
        if not datos:
            continue
        if estado_sheet == "Caso manual":
            datos.setdefault("slack", {})["estado"] = "caso_manual"
            registro.guardar_caso_rt(datos, "Caso manual")
            continue
        datos["completado"] = True
        registro.guardar_caso_rt(datos, _estado_caso(datos))


def listar_pendientes_slack():
    from app import registro

    try:
        sincronizar_cierres_qa()
    except Exception as error:
        logging.getLogger("proyecto_e").warning(
            "No se pudo leer el cierre de QA en el Sheet: %s", error
        )
    return registro.listar_casos_rt_en_slack()


def referencia_duplicado_rt(mensaje_id):
    try:
        info = leer_caso(mensaje_id)
    except FileNotFoundError:
        return None
    lupe = info.get("lupe") or {}
    id_rt = str(lupe.get("id") or (info.get("correo") or {}).get("id_rt") or "").strip()
    regla = str(lupe.get("regla_bloqueo") or "").strip()
    estado = _estado_caso(info)
    if not id_rt or not regla or estado not in ESTADOS_DUPLICADO_RT:
        return None
    return id_rt, estado, regla


def buscar_duplicado_rt(id_rt):
    from app import registro

    return registro.buscar_duplicado_rt(id_rt)


def _fila_sheet(datos):
    correo = datos.get("correo") or {}
    lupe = datos.get("lupe") or {}
    slack = datos.get("slack") or {}
    nombre = (
        lupe.get("nombre")
        or correo.get("nombre_peticionario")
        or correo.get("remitente_nombre")
        or ""
    )
    email = (
        correo.get("correo_peticionario")
        or correo.get("correo_remitente")
        or correo.get("remitente_email")
        or ""
    )
    id_rt = lupe.get("id") or correo.get("id_rt") or ""
    return [
        nombre,
        email,
        str(id_rt),
        _estado_caso(datos),
        correo.get("fecha_recibido") or "",
        slack.get("thread_id") or "",
        correo.get("gmail_id") or "",
    ]


def _actualizar_sheet(datos):
    if not SHEETS_ID:
        return
    guardar_fila(SHEETS_ID, _fila_sheet(datos), columna_clave="G", ultima_columna="G")


def _letra_indice(letra):
    return ord(letra.upper()) - ord("A")


def guardar_fila(spreadsheet_id, fila, columna_clave="A", ultima_columna="G", entrada="RAW"):
    """Actualiza la fila cuya columna clave coincide con la del valor; si no está, la agrega."""
    sheets = _sheets()
    clave = str(fila[_letra_indice(columna_clave)])
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{columna_clave}:{columna_clave}"
    ).execute()
    fila_num = None
    for i, valor in enumerate(resp.get("values", [])):
        if valor and str(valor[0]) == clave:
            fila_num = i + 1
            break

    if fila_num:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"A{fila_num}:{ultima_columna}{fila_num}",
            valueInputOption=entrada,
            body={"values": [fila]},
        ).execute()
    else:
        sheets.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"A:{ultima_columna}",
            valueInputOption=entrada,
            insertDataOption="INSERT_ROWS",
            body={"values": [fila]},
        ).execute()
    return fila_num


def _gmail():
    return build("gmail", "v1", credentials=autenticar())


def marcar_leido(mensaje_id, servicio=None):
    if not mensaje_id:
        raise ValueError("Falta el id del correo para marcarlo como leído.")
    (servicio or _gmail()).users().messages().modify(
        userId="me", id=mensaje_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def cambiar_etiquetas(mensaje_id, agregar=(), quitar=(), servicio=None):
    cuerpo = {
        "addLabelIds": [e for e in agregar if e],
        "removeLabelIds": [e for e in quitar if e],
    }
    (servicio or _gmail()).users().messages().modify(
        userId="me", id=mensaje_id, body=cuerpo
    ).execute()

def leer_mapeo_causales():
    if not RUTA_MAPEO.is_file():
        raise FileNotFoundError("Falta data-json/mapeo-causales.json")
    return json.loads(RUTA_MAPEO.read_text(encoding="utf-8"))