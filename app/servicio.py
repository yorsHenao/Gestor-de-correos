"""`python main.py servicio`: scheduler y dashboard en un solo proceso.

Una sola instancia por PC: si ya hay otra, sale con código 3.
"""

import logging
import sys

from filelock import FileLock, Timeout

from app import registro, scheduler
from app.alertas import alertar
from config import DASHBOARD_PUERTO, DASHBOARD_URL

log = logging.getLogger("proyecto_e")
_EXTRA = {"extra": {"paso": "servicio", "caso": "-"}}
CANDADO = registro.RAIZ / "data" / "servicio.lock"


def iniciar_servicio():
    import uvicorn

    from app.dashboard.web import app as aplicacion

    CANDADO.parent.mkdir(parents=True, exist_ok=True)
    candado = FileLock(str(CANDADO))
    try:
        candado.acquire(timeout=0)
    except Timeout:
        log.warning("El servicio ya está corriendo en este PC (%s)", DASHBOARD_URL, **_EXTRA)
        return 3

    try:
        scheduler.iniciar()
        log.info("Servicio iniciado; dashboard en %s", DASHBOARD_URL, **_EXTRA)
        uvicorn.run(
            aplicacion,
            host="127.0.0.1",
            port=DASHBOARD_PUERTO,
            log_config=None,
            access_log=False,
        )
    except Exception as error:
        log.exception("El servicio se detuvo por un error", **_EXTRA)
        alertar("servicio", "El servicio Proyecto E se detuvo", str(error))
        return 1
    finally:
        scheduler.detener()
        candado.release()
        log.info("Servicio detenido", **_EXTRA)
    return 0


if __name__ == "__main__":
    sys.exit(iniciar_servicio())
