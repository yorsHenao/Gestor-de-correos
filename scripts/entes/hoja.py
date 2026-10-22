import re

import storage
from config import DRIVE_SHEETS_ENTES_ID

_REINTENTO = re.compile(r"reintento\s+(\d+)", re.I)

ESTADO_ENVIADO_ENTES = "enviado a Entes"
ESTADO_REVISION = "revisión manual"
ESTADO_MAS_INFO = "- se pide mas información"
ESTADO_MAS_INFO_ENVIADO = "Se pide mas información- Correo enviado"
ESTADO_SIN_CORREO = "- sin correo- solicitarlo"


def estado_reintento(intentos):
    return f"Error de envío - reintento {intentos}"


def numero_reintento(estado):
    hallado = _REINTENTO.search(estado or "")
    return int(hallado.group(1)) if hallado else 0


def accion_fila(estado, completado=""):
    estado = (estado or "").strip()
    if str(completado or "").strip().lower() == "completado" or estado == ESTADO_ENVIADO_ENTES:
        return "cerrar"
    if not estado or estado.startswith("Error de envío"):
        return "procesar"
    return "quieto"


def indice():
    mapa = {}
    for fila in leer_valores():
        estado = fila[0] if len(fila) > 0 else ""
        mensaje = str(fila[1]).strip() if len(fila) > 1 else ""
        completado = fila[2] if len(fila) > 2 else ""
        if mensaje and mensaje.lower() != "mensaje_id":
            mapa[mensaje] = {"estado": estado, "completado": completado}
    return mapa


def leer_valores():
    if not DRIVE_SHEETS_ENTES_ID:
        return []
    return storage.leer_rango(DRIVE_SHEETS_ENTES_ID, "F:H")


def guardar_fila(registro, estado):
    if not DRIVE_SHEETS_ENTES_ID:
        raise RuntimeError("DRIVE_SHEETS_ENTES_ID no está en el .env")
    mensaje_id = registro.get("mensaje_id") or ""
    fila = [
        registro.get("peticionario") or "",
        registro.get("correo") or "",
        registro.get("id_usuario") or "0",
        registro.get("fecha_peticion") or "",
        registro.get("causal") or "Otro",
        estado,
        mensaje_id,
    ]
    # Se escribe solo A:G: H pertenece a QA y nunca debe tocarla el programa.
    storage.guardar_fila(
        DRIVE_SHEETS_ENTES_ID,
        fila,
        columna_clave="G",
        ultima_columna="G",
        entrada="USER_ENTERED",
    )


def cerrar_caso(registro, estado, clog):
    try:
        guardar_fila(registro, estado)
    except Exception as error:
        clog.error("No se pudo guardar la fila (%s); el correo sigue sin leer: %s", estado, error)
        return False
    try:
        storage.marcar_leido(registro.get("mensaje_id") or "")
    except Exception as error:
        clog.error("La fila quedó «%s», pero el correo sigue sin leer: %s", estado, error)
        return False
    return True


def anotar_estado(registro, estado, clog):
    try:
        guardar_fila(registro, estado)
        return True
    except Exception as error:
        clog.error("No se pudo guardar la fila (%s): %s", estado, error)
        return False
