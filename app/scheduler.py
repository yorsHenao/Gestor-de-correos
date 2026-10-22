"""Programador del ciclo: cada CICLO_HORAS, con recuperación del ciclo perdido.

- misfire_grace_time: si el PC estuvo suspendido a la hora del ciclo, se corre al despertar.
- coalesce + max_instances=1: nunca dos ciclos a la vez (además, el orquestador tiene su candado).
- Al arrancar, si el último ciclo es más viejo que CICLO_HORAS, el primero corre a los 2 minutos.
"""

import logging
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app import orquestador, registro
from config import CICLO_HORAS

log = logging.getLogger("proyecto_e")
_EXTRA = {"extra": {"paso": "scheduler", "caso": "-"}}

TRABAJO = "ciclo"
ESPERA_INICIAL = timedelta(minutes=2)

_scheduler = None


def _ciclo_programado():
    if orquestador.pausado():
        log.warning("Ciclo programado omitido: el servicio está en pausa", **_EXTRA)
        return
    try:
        orquestador.ejecutar_ciclo(origen="programado", eco=False)
    except Exception:
        log.exception("El ciclo programado falló", **_EXTRA)


def _primera_ejecucion():
    ahora = datetime.now()
    ultimo = registro.ultimo_ciclo()
    if ultimo:
        inicio = datetime.fromisoformat(ultimo["inicio"])
        proximo = inicio + timedelta(hours=CICLO_HORAS)
        if proximo > ahora + ESPERA_INICIAL:
            return proximo
    return ahora + ESPERA_INICIAL


def iniciar():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": int(CICLO_HORAS * 3600),
        }
    )
    _scheduler.add_job(
        _ciclo_programado,
        "interval",
        hours=CICLO_HORAS,
        id=TRABAJO,
        next_run_time=_primera_ejecucion(),
    )
    _scheduler.start()
    log.info(
        "Scheduler iniciado: ciclo cada %s h; próximo %s",
        CICLO_HORAS,
        proximo_ciclo().strftime("%Y-%m-%d %H:%M") if proximo_ciclo() else "—",
        **_EXTRA,
    )
    return _scheduler


def detener():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def proximo_ciclo():
    if _scheduler is None:
        return None
    trabajo = _scheduler.get_job(TRABAJO)
    return trabajo.next_run_time.replace(tzinfo=None) if trabajo and trabajo.next_run_time else None


def lanzar_en_segundo_plano(funcion, *args, **kwargs):
    """Para los botones del dashboard: la petición HTTP vuelve al instante."""
    hilo = threading.Thread(target=funcion, args=args, kwargs=kwargs, daemon=True)
    hilo.start()
    return hilo
