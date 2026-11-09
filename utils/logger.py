# utils/logger.py
"""
Mensajes uniformes

Formato: fecha | NIVEL | paso | caso | mensaje
- INFO: progreso normal.
- WARNING: algo que alguien debe mirar (revisión manual, reintento, Lupe ocupado).
- ERROR: fallo, con la traza completa (log.exception).
"""

import logging
import os
import re
import sys
from pathlib import Path
FORMATO = "%(asctime)s | %(levelname)-7s | %(paso)s | %(caso)s | %(message)s"
RUTA_LOG = Path(__file__).resolve().parent.parent / "logs" / "proyecto_e.log"
FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

log = logging.getLogger("proyecto_e")
_REINTENTO = re.compile(
    r"(?:try again in|retry(?:Delay| in)?|reintentar en)[^\d]*"
    r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)\s*(ms|s)?)?",
    re.I,
)
_SEPARADOR = "-" * 60


def segundos_reintento(texto):
    """Segundos que pide esperar un 429 («7m12.5s», «450ms», «33s»). None si no lo dice."""
    hallado = _REINTENTO.search(str(texto or ""))
    if not hallado or not any(hallado.groups()):
        return None
    horas, minutos, valor, unidad = hallado.groups()
    segundos = int(horas or 0) * 3600 + int(minutos or 0) * 60
    if valor:
        segundos += float(valor) / (1000 if (unidad or "s").lower() == "ms" else 1)
    return segundos


def resumir_error(error):
    """Devuelve una causa breve y estable para consola y alertas."""
    texto = " ".join(str(error or "").split())
    bajo = texto.lower()
    if "error code: 413" in bajo or "request too large" in bajo:
        return "Correo demasiado grande para la IA (413)"
    if "429" in texto or "resource_exhausted" in bajo or "rate_limit" in bajo:
        segundos = segundos_reintento(texto)
        espera = f", reintentar en {segundos:g} s" if segundos is not None else ""
        return f"Cuota de IA agotada (429){espera}"
    if "503" in texto or "unavailable" in bajo:
        return "IA no disponible (alta demanda, 503)"
    if "deadline_exceeded" in bajo:
        return "La IA no respondió a tiempo (504)"
    nombre = type(error).__name__
    if nombre == "TargetClosedError" or (
        "target page, context or browser has been closed" in bajo
    ):
        return "El navegador de Lupe se cerró"
    if nombre == "TimeoutError" and "playwright" in type(error).__module__:
        return "Lupe no respondió a tiempo"
    primera = texto.splitlines()[0] if texto else "Error sin detalle"
    return f"{nombre}: {primera}"[:160]


class _FormatoConsola(logging.Formatter):
    def format(self, record):
        if getattr(record, "separador", False):
            return _SEPARADOR
        return super().format(record)


class _Contexto(logging.Filter):
    def filter(self, record):
        if not getattr(record, "paso", None):
            record.paso = os.getenv("PROYECTO_E_PASO", "-")
        if not getattr(record, "caso", None):
            record.caso = "-"
        return True


def setup_logging(log_level=logging.INFO):
    if log.handlers:
        return log

    formatter_consola = _FormatoConsola(fmt=FORMATO, datefmt=FORMATO_FECHA)
    contexto = _Contexto()

    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        consola = logging.StreamHandler(sys.stdout)
        consola.setFormatter(formatter_consola)
        consola.addFilter(contexto)
        log.addHandler(consola)

    RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    archivo = logging.FileHandler(RUTA_LOG, encoding="utf-8")
    archivo.setFormatter(formatter_consola)
    archivo.addFilter(contexto)
    log.addHandler(archivo)

    log.setLevel(log_level)
    log.propagate = False
    return log


class _Adaptador(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = dict(self.extra)
        extra.update(kwargs.get("extra") or {})
        kwargs["extra"] = extra
        return msg, kwargs


def obtener_log(paso=None):
    """Logger del paso (clasificar, rt, slack, entes...). Sin paso, usa el que fijó el orquestador."""
    setup_logging()
    return _Adaptador(log, {"paso": paso, "caso": None})


def log_caso(base, caso):
    """Mismo logger, con el caso (correo del peticionario o nombre del JSON) fijo."""
    extra = dict(getattr(base, "extra", {}) or {})
    extra["caso"] = caso or "-"
    return _Adaptador(getattr(base, "logger", base), extra)


def log_separador(base):
    base.info("", extra={"separador": True, "caso": "-"})


def resumen(base, procesados=0, completados=0, revision_manual=0, errores=0, **otros):
    contadores = {
        "procesados": procesados,
        "completados": completados,
        "revision_manual": revision_manual,
        "errores": errores,
        **otros,
    }
    texto = ", ".join(f"{clave}={valor}" for clave, valor in contadores.items())
    nivel = logging.WARNING if errores else logging.INFO
    base.log(nivel, "Resumen: %s", texto, extra={"resumen": contadores, "caso": "-"})
    return contadores
