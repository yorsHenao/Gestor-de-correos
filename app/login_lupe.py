"""Login manual de Lupe lanzado desde el dashboard.

Ejecuta `python main.py login` (la misma lógica que en la terminal) como proceso
aparte, para no bloquear el servidor web. Solo permite un login a la vez y no
arranca si un ciclo o un paso está usando el perfil de Lupe.
"""

import logging
import os
import subprocess
import threading
from datetime import datetime

from filelock import FileLock, Timeout

from app import orquestador
from config import LUPE_LOGIN_MAX_SEG, LUPE_PROFILE_DIR

log = logging.getLogger("proyecto_e")
_EXTRA = {"extra": {"paso": "login_lupe", "caso": "-"}}

_candado = threading.Lock()
_estado = {"estado": "inactivo", "mensaje": "", "momento": None}
_proceso = None


def _fijar(estado, mensaje):
    _estado.update(estado=estado, mensaje=mensaje, momento=datetime.now())


def estado():
    """Copia del estado: inactivo | abriendo | ok | error | ocupado."""
    return dict(_estado)


def en_curso():
    return _proceso is not None and _proceso.poll() is None


def _perfil_ocupado():
    """True si otro proceso tiene el candado del perfil de Lupe (no lo retiene)."""
    candado = FileLock(f"{LUPE_PROFILE_DIR}.lock")
    try:
        candado.acquire(timeout=0)
    except Timeout:
        return True
    candado.release()
    return False


def lanzar():
    """Lanza el login en segundo plano. Devuelve (lanzado, mensaje)."""
    global _proceso
    with _candado:
        if en_curso():
            return False, "El navegador de Lupe ya está abierto para iniciar sesión."
        if orquestador.ciclo_en_curso() or _perfil_ocupado():
            mensaje = "Lupe está en uso por un ciclo o paso; intenta cuando termine."
            _fijar("ocupado", mensaje)
            log.warning("Login de Lupe no lanzado: %s", mensaje, **_EXTRA)
            return False, mensaje
        comando = [orquestador._python(), "-u", str(orquestador.RAIZ / "main.py"), "login"]
        banderas = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            _proceso = subprocess.Popen(
                comando,
                cwd=str(orquestador.RAIZ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=banderas,
            )
        except OSError as error:
            _fijar("error", f"No se pudo abrir el navegador: {error}")
            log.error("Login de Lupe: no se pudo lanzar: %s", error, **_EXTRA)
            return False, _estado["mensaje"]
        _fijar("abriendo", f"Abriendo navegador… completa el login (máximo {LUPE_LOGIN_MAX_SEG} s).")
        log.info("Login de Lupe lanzado desde el dashboard", **_EXTRA)
        threading.Thread(target=_esperar, args=(_proceso,), daemon=True).start()
        return True, _estado["mensaje"]


def _esperar(proceso):
    codigo = proceso.wait()
    if codigo == 0:
        _fijar("ok", "Sesión iniciada.")
        log.info("Login de Lupe: sesión iniciada", **_EXTRA)
    elif codigo == 20:
        _fijar("error", "No se completó el login (tiempo agotado o ventana cerrada).")
        log.error("Login de Lupe: no se completó (código 20)", **_EXTRA)
    elif codigo == 21:
        _fijar("ocupado", "Lupe estaba en uso por otro proceso; intenta de nuevo.")
        log.warning("Login de Lupe: perfil ocupado (código 21)", **_EXTRA)
    else:
        _fijar("error", f"El login terminó con error (código {codigo}). Revisa el registro.")
        log.error("Login de Lupe: terminó con código %s", codigo, **_EXTRA)