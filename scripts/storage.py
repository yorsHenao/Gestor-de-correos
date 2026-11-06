import json
import logging
from email.utils import parseaddr
from pathlib import Path

from autenticar import autenticar
from googleapiclient.discovery import build
from config import DRIVE_SHEETS_ID

ESTADO_EN_SLACK = "En Slack - pendiente"
ESTADOS_PENDIENTE_SLACK = frozenset({ESTADO_EN_SLACK, "En Slack"})
ESTADOS_DUPLICADO_RT = ESTADOS_PENDIENTE_SLACK | {"Completado"}
ESTADO_PETICION_REPETIDA_GESTIONADO = "Petición repetida - caso gestionado"
ESTADO_PETICION_REPETIDA_PENDIENTE = "Petición repetida - caso pendiente"
ESTADOS_PETICION_REPETIDA = frozenset({
    ESTADO_PETICION_REPETIDA_GESTIONADO,
    ESTADO_PETICION_REPETIDA_PENDIENTE,
})
CIERRES_QA = frozenset({"Completado", "Caso manual"})
TEXTO_PEDIR_CORREO = "pedir correo"

SHEETS_ID = DRIVE_SHEETS_ID
RUTA_MAPEO = Path(__file__).resolve().parent.parent / "data-json" / "mapeo-causales.json"


def falta_correo_peticionario(correo):
    """Vacío o @rappi.com: no es el correo de quien escribió."""
    _, email = parseaddr(str(correo or ""))
    candidato = (email or correo or "").strip().lower()
    if "@" not in candidato or candidato == TEXTO_PEDIR_CORREO:
        return True
    return candidato.endswith("@rappi.com")


def _correo_escrito_en_sheet(valor):
    """Correo que QA puso en la columna B, si ya es de la persona."""
    texto = str(valor or "").strip()
    if falta_correo_peticionario(texto):
        return ""
    return texto


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
    for fila in leer_rango(SHEETS_ID, "B:G"):
        correo_sheet = str(fila[0]).strip() if fila else ""
        estado_sheet = str(fila[2]).strip() if len(fila) > 2 else ""
        mensaje = str(fila[5]).strip() if len(fila) > 5 else ""
        if mensaje not in abiertos:
            continue
        datos = registro.leer_caso_rt(mensaje)
        if not datos:
            continue
        correo_nuevo = _correo_escrito_en_sheet(correo_sheet)
        correo_cambio = False
        if correo_nuevo:
            actual = str((datos.get("correo") or {}).get("correo_peticionario") or "").strip()
            if actual.lower() != correo_nuevo.lower():
                datos.setdefault("correo", {})["correo_peticionario"] = correo_nuevo
                correo_cambio = True
        if estado_sheet == "Caso manual":
            datos.setdefault("slack", {})["estado"] = "caso_manual"
            registro.guardar_caso_rt(datos, "Caso manual")
            continue
        if estado_sheet == "Completado":
            datos["completado"] = True
            registro.guardar_caso_rt(datos, _estado_caso(datos))
            continue
        if correo_cambio:
            registro.guardar_caso_rt(datos, datos.get("estado") or _estado_caso(datos))


def listar_pendientes_slack():
    from app import registro

    try:
        sincronizar_cierres_qa()
    except Exception as error:
        logging.getLogger("proyecto_e").warning(
            "No se pudo leer el cierre de QA en el Sheet: %s", error
        )
    return registro.listar_casos_rt_en_slack()


def listar_respuestas_pendientes():
    from app import registro

    return registro.listar_casos_rt_respuesta_pendiente()


def _ruta_foto_prueba(mensaje_id):
    from app import registro

    nombre = "".join(c for c in str(mensaje_id) if c.isalnum() or c in "-_")
    if not nombre:
        raise ValueError("Falta el id del caso para la imagen de prueba.")
    return registro.DB_PATH.parent / "fotos_prueba" / f"{nombre}.png"


def guardar_foto_prueba(mensaje_id, foto_bytes):
    ruta = _ruta_foto_prueba(mensaje_id)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(foto_bytes)


def leer_foto_prueba(mensaje_id):
    ruta = _ruta_foto_prueba(mensaje_id)
    return ruta.read_bytes() if ruta.is_file() else None


def borrar_foto_prueba(mensaje_id):
    _ruta_foto_prueba(mensaje_id).unlink(missing_ok=True)


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


def estado_peticion_repetida(estado_referencia):
    if estado_referencia in ESTADOS_PENDIENTE_SLACK:
        return ESTADO_PETICION_REPETIDA_PENDIENTE
    return ESTADO_PETICION_REPETIDA_GESTIONADO


def registrar_peticion_repetida(mensaje_id, datos, estado_referencia):
    from app import registro

    correo = datos.setdefault("correo", {})
    correo["gmail_id"] = correo.get("gmail_id") or mensaje_id
    lupe = datos.setdefault("lupe", {})
    lupe["regla_bloqueo"] = ""
    slack = datos.setdefault("slack", {})
    slack["thread_id"] = ""
    datos["completado"] = False
    estado = estado_peticion_repetida(estado_referencia)
    registro.guardar_caso_rt(datos, estado)
    _actualizar_sheet_peticion_repetida(datos, estado)
    return estado


def _fila_peticion_repetida(datos, estado):
    correo = datos.get("correo") or {}
    nombre = (
        correo.get("nombre_peticionario")
        or correo.get("remitente_nombre")
        or ""
    )
    if falta_correo_peticionario(correo.get("correo_peticionario")):
        email = TEXTO_PEDIR_CORREO
    else:
        email = correo.get("correo_peticionario") or ""
    return [
        nombre,
        email,
        str(correo.get("id_rt") or ""),
        estado,
        correo.get("fecha_recibido") or "",
        "",
        correo.get("gmail_id") or "",
    ]


def _actualizar_sheet_peticion_repetida(datos, estado):
    if not SHEETS_ID:
        raise RuntimeError("No hay hoja de Sheets configurada para registrar la petición repetida.")
    guardar_fila(
        SHEETS_ID,
        _fila_peticion_repetida(datos, estado),
        columna_clave="G",
        ultima_columna="G",
    )


def _fila_sheet(datos):
    correo = datos.get("correo") or {}
    lupe = datos.get("lupe") or {}
    slack = datos.get("slack") or {}
    es_tercero = datos.get("tipo_caso") == 2
    if es_tercero:
        nombre = correo.get("nombre_peticionario") or correo.get("remitente_nombre") or ""
        id_rt = correo.get("id_rt") or ""
    else:
        nombre = (
            lupe.get("nombre")
            or correo.get("nombre_peticionario")
            or correo.get("remitente_nombre")
            or ""
        )
        id_rt = lupe.get("id") or correo.get("id_rt") or ""
    if falta_correo_peticionario(correo.get("correo_peticionario")):
        email = TEXTO_PEDIR_CORREO
    else:
        email = correo.get("correo_peticionario") or ""
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