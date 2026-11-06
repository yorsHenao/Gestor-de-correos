from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from config import BASE_DIR

RUTA_ACTIVIDAD = BASE_DIR / "logs" / "actividad.jsonl"
_MAX_LECTURA = 400
_lock = threading.Lock()
_ciclo_actual_id = None


def _ahora_iso():
    return datetime.now().isoformat(timespec="seconds")


def _ruta_ciclo_activo():
    return Path(RUTA_ACTIVIDAD).with_suffix(".ciclo")


def _guardar_ciclo_activo(valor):
    ruta = _ruta_ciclo_activo()
    try:
        if valor:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(valor, encoding="utf-8")
        else:
            ruta.unlink(missing_ok=True)
    except OSError:
        pass


def ciclo_actual_id():
    if _ciclo_actual_id:
        return _ciclo_actual_id
    try:
        return _ruta_ciclo_activo().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def iniciar_ciclo_id():
    global _ciclo_actual_id
    _ciclo_actual_id = uuid.uuid4().hex
    _guardar_ciclo_activo(_ciclo_actual_id)
    return _ciclo_actual_id


def cerrar_ciclo_id():
    global _ciclo_actual_id
    _ciclo_actual_id = None
    _guardar_ciclo_activo(None)


def registrar(tipo, mensaje, *, paso="ciclo", detalle="", estado="", ciclo_id=None):
    evento = {
        "id": uuid.uuid4().hex,
        "ts": _ahora_iso(),
        "tipo": tipo,
        "paso": paso or "ciclo",
        "mensaje": mensaje,
        "detalle": detalle or "",
        "estado": estado or "",
        "ciclo_id": ciclo_id if ciclo_id is not None else ciclo_actual_id(),
    }
    linea = json.dumps(evento, ensure_ascii=False)
    try:
        with _lock:
            RUTA_ACTIVIDAD.parent.mkdir(parents=True, exist_ok=True)
            with RUTA_ACTIVIDAD.open("a", encoding="utf-8") as fh:
                fh.write(linea + "\n")
    except OSError:
        return None
    return evento


def _leer_eventos(ruta=None, limite=_MAX_LECTURA):
    archivo = Path(ruta) if ruta is not None else RUTA_ACTIVIDAD
    if not archivo.is_file():
        return []
    try:
        with _lock:
            with archivo.open(encoding="utf-8", errors="replace") as fh:
                lineas = deque(fh, maxlen=limite)
    except OSError:
        return []
    eventos = []
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        try:
            datos = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(datos, dict) and datos.get("id"):
            eventos.append(datos)
    return eventos


def listar(*, ambito="actual", paso=None, limite=200, ruta=None):
    eventos = _leer_eventos(ruta=ruta, limite=_MAX_LECTURA)
    actual = ciclo_actual_id()
    if ambito == "actual":
        if actual:
            eventos = [e for e in eventos if e.get("ciclo_id") == actual]
        else:
            eventos = []
    elif ambito == "anteriores":
        if actual:
            eventos = [e for e in eventos if e.get("ciclo_id") and e.get("ciclo_id") != actual]
        else:
            eventos = [e for e in eventos if e.get("ciclo_id")]
    if paso and paso != "todos":
        eventos = [e for e in eventos if e.get("paso") == paso]
    eventos.reverse()
    return eventos[:limite]
