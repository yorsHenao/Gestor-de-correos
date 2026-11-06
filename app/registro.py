import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("PROYECTO_E_DB", RAIZ / "data" / "runs.db"))

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS hueco_entes (
    mensaje_id TEXT PRIMARY KEY,
    peticionario TEXT,
    correo TEXT,
    id_usuario TEXT,
    fecha_peticion TEXT,
    causal TEXT,
    motivo TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ultimo_ciclo (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    inicio TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS casos_rt (
    mensaje_id TEXT PRIMARY KEY,
    id_rt TEXT,
    estado TEXT NOT NULL,
    regla_bloqueo TEXT,
    thread_slack TEXT,
    datos TEXT NOT NULL
);
DROP TABLE IF EXISTS ejecuciones;
DROP TABLE IF EXISTS eventos;
DROP TABLE IF EXISTS alertas;
DROP TABLE IF EXISTS entes_intentos;
"""


def ahora():
    return datetime.now().isoformat(timespec="seconds")


def conectar():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(DB_PATH, timeout=30)
    conexion.row_factory = sqlite3.Row
    conexion.executescript(_ESQUEMA)
    return conexion


def _ejecutar(sql, parametros=()):
    with closing(conectar()) as conexion, conexion:
        cursor = conexion.execute(sql, parametros)
        return cursor.lastrowid


def _consultar(sql, parametros=()):
    with closing(conectar()) as conexion:
        return [dict(fila) for fila in conexion.execute(sql, parametros).fetchall()]


def marcar_ultimo_ciclo():
    _ejecutar(
        "INSERT INTO ultimo_ciclo (id, inicio) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET inicio = excluded.inicio",
        (ahora(),),
    )


def ultimo_ciclo():
    filas = _consultar("SELECT inicio FROM ultimo_ciclo WHERE id = 1")
    return filas[0] if filas else None


def guardar_hueco(mensaje_id, datos, motivo):
    _ejecutar(
        "INSERT INTO hueco_entes (mensaje_id, peticionario, correo, id_usuario, "
        "fecha_peticion, causal, motivo) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(mensaje_id) DO UPDATE SET "
        "peticionario = excluded.peticionario, correo = excluded.correo, "
        "id_usuario = excluded.id_usuario, fecha_peticion = excluded.fecha_peticion, "
        "causal = excluded.causal, motivo = excluded.motivo",
        (
            mensaje_id,
            (datos or {}).get("peticionario") or "",
            (datos or {}).get("correo") or "",
            (datos or {}).get("id_usuario") or "0",
            (datos or {}).get("fecha_peticion") or "",
            (datos or {}).get("causal") or "Otro",
            motivo,
        ),
    )


def leer_hueco(mensaje_id):
    filas = _consultar("SELECT * FROM hueco_entes WHERE mensaje_id = ?", (mensaje_id,))
    return filas[0] if filas else None


def borrar_hueco(mensaje_id):
    _ejecutar("DELETE FROM hueco_entes WHERE mensaje_id = ?", (mensaje_id,))


def guardar_caso_rt(datos, estado):
    correo = datos.get("correo") or {}
    lupe = datos.get("lupe") or {}
    slack = datos.get("slack") or {}
    mensaje_id = str(correo.get("gmail_id") or "").strip()
    if not mensaje_id:
        raise ValueError("El caso RT no tiene id de mensaje.")
    _ejecutar(
        "INSERT INTO casos_rt (mensaje_id, id_rt, estado, regla_bloqueo, thread_slack, datos) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(mensaje_id) DO UPDATE SET "
        "id_rt = excluded.id_rt, estado = excluded.estado, "
        "regla_bloqueo = excluded.regla_bloqueo, thread_slack = excluded.thread_slack, "
        "datos = excluded.datos",
        (
            mensaje_id,
            str(lupe.get("id") or correo.get("id_rt") or "").strip(),
            estado,
            str(lupe.get("regla_bloqueo") or "").strip(),
            str(slack.get("thread_id") or "").strip(),
            json.dumps(datos, ensure_ascii=False),
        ),
    )
    return mensaje_id


def leer_caso_rt(mensaje_id):
    filas = _consultar("SELECT * FROM casos_rt WHERE mensaje_id = ?", (mensaje_id,))
    if not filas:
        return None
    datos = json.loads(filas[0]["datos"])
    datos["estado"] = filas[0]["estado"]
    return datos


def listar_casos_rt_abiertos():
    return _consultar(
        "SELECT mensaje_id, estado FROM casos_rt "
        "WHERE estado NOT IN ("
        "'Completado', 'Caso manual', 'Sin cuenta - enviado', "
        "'Petición repetida - caso gestionado', "
        "'Petición repetida - caso pendiente')"
    )


def listar_casos_rt_en_slack():
    return [
        fila["mensaje_id"]
        for fila in _consultar(
            "SELECT mensaje_id FROM casos_rt WHERE estado = ?",
            ("En Slack - pendiente",),
        )
    ]


def buscar_duplicado_rt(id_rt):
    id_rt = str(id_rt or "").strip()
    if not id_rt:
        return None
    filas = _consultar(
        "SELECT mensaje_id, estado, regla_bloqueo FROM casos_rt "
        "WHERE id_rt = ? AND regla_bloqueo != '' "
        "AND estado IN ('En Slack - pendiente', 'Completado') "
        "ORDER BY CASE estado WHEN 'En Slack - pendiente' THEN 0 ELSE 1 END",
        (id_rt,),
    )
    if not filas:
        return None
    fila = filas[0]
    return fila["mensaje_id"], fila["estado"], fila["regla_bloqueo"]


def anotar_duplicado_rt(mensaje_id, datos_correo):
    datos = leer_caso_rt(mensaje_id)
    if not datos:
        return
    duplicados = datos.setdefault("correo", {}).setdefault("duplicados", [])
    if any(item.get("message_id") == datos_correo.get("message_id") for item in duplicados):
        return
    duplicados.append({
        "message_id": datos_correo.get("message_id"),
        "thread_id_gmail": datos_correo.get("thread_id_gmail"),
        "asunto": datos_correo.get("asunto"),
        "fecha_recibido": datos_correo.get("fecha_recibido"),
    })
    guardar_caso_rt(datos, datos.get("estado") or "Completado")
