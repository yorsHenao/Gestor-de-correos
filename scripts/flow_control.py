"""Detención cooperativa de pasos largos (clasificar, Lupe, Slack)."""

import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLAG_DETENER = ROOT / "logs" / "detener.flujo"


class DetenerFlujo(Exception):
    """El operador pidió parar; el paso termina limpio sin forzar el cierre del proceso."""


def limpiar_detener() -> None:
    if FLAG_DETENER.exists():
        FLAG_DETENER.unlink(missing_ok=True)


def solicitar_detener() -> None:
    FLAG_DETENER.parent.mkdir(parents=True, exist_ok=True)
    FLAG_DETENER.write_text("1", encoding="utf-8")


def debe_detener() -> bool:
    return FLAG_DETENER.exists()


def comprobar_detener(
    mensaje: str = "el paso termina sin procesar más casos.",
) -> None:
    if debe_detener():
        limpiar_detener()
        logging.getLogger("proyecto_e").warning("Parada solicitada: %s", mensaje)
        raise DetenerFlujo(mensaje)
