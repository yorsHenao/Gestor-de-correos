"""

Orden fijo del ciclo: clasificar → rt → slack → entes. RT, Slack 

Códigos de salida de los pasos:
- 0: bien (aviso si el resumen trae errores).
- 20: Lupe pide login → alerta y se omiten los pasos que usan Lupe en este ciclo.
- 21: Lupe ocupado → aviso; el paso se reintenta en el siguiente ciclo.
- otro: error → alerta.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from filelock import FileLock, Timeout

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
for ruta in (str(RAIZ), str(SCRIPTS)):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

from app import registro
from app.alertas import alertar, nuevo_ciclo
from flow_control import FLAG_DETENER, limpiar_detener, solicitar_detener

log = logging.getLogger("proyecto_e")

PASOS = {
    "clasificar": {"script": "clasificar_correos_gmail.py", "args": [], "lupe": False},
    "rt": {"script": "agentes/agente_rts.py", "args": ["lupe"], "lupe": True},
    "slack": {"script": "agentes/agente_rts.py", "args": ["slack"], "lupe": True},
    "entes": {"script": "procesar_entes.py", "args": [], "lupe": True},
}
ORDEN = ["clasificar", "rt", "slack", "entes"]

CODIGO_LOGIN = 20
CODIGO_OCUPADO = 21
TIMEOUT_PASO_SEG = int(float(os.getenv("PASO_TIMEOUT_MIN") or "120") * 60)
FLAG_PAUSA = RAIZ / "logs" / "servicio.pausado"
FLAG_ESPERANDO = RAIZ / "logs" / "revision.esperando"
FLAG_CONTINUAR = RAIZ / "logs" / "revision.continuar"
CANDADO_CICLO = RAIZ / "data" / "ciclo.lock"
ESPERA_REVISION_SEG = 0.5
_confirmar = threading.Event()
_paso_en_curso = None
_cortar_ciclo = False

_GRAVEDAD = {"ok": 0, "omitido": 0, "aviso": 1, "error": 2}


def _extra(paso="orquestador"):
    return {"extra": {"paso": paso, "caso": "-"}}


def _python():
    """python.exe aunque el servicio corra con pythonw.exe (los hijos necesitan stdout)."""
    actual = Path(sys.executable)
    consola = actual.with_name("python.exe")
    return str(consola if actual.name.lower() == "pythonw.exe" and consola.exists() else actual)


# ── Pausa del servicio ───────────────────────────────────────────────────

def pausado():
    return FLAG_PAUSA.exists()


def pausar():
    """No arranca más ciclos y pide al paso en curso que pare al terminar su caso."""
    FLAG_PAUSA.parent.mkdir(parents=True, exist_ok=True)
    FLAG_PAUSA.write_text("1", encoding="utf-8")
    solicitar_detener()
    log.warning("Servicio en pausa", **_extra())


def reanudar():
    FLAG_PAUSA.unlink(missing_ok=True)
    limpiar_detener()
    log.info("Servicio reanudado", **_extra())


def detener_paso():
    global _cortar_ciclo
    if not ciclo_en_curso():
        return False
    _cortar_ciclo = True
    solicitar_detener()
    log.warning("Parada del paso pedida desde el dashboard", **_extra())
    return True


def paso_detenido():
    return _cortar_ciclo and ciclo_en_curso()


def _en_pausa():
    return pausado()


def _revision_activa():
    return os.getenv("REVISION_CLASIFICACION", "").strip().lower() == "true"


def decision_tras_clasificar(nombre, estado, quedan_pasos, revision):
    """Tras clasificar: seguir, esperar confirmación o parar si el paso falló."""
    if nombre != "clasificar" or not quedan_pasos or not revision:
        return "seguir"
    if estado == "error":
        return "parar"
    return "esperar"


def esperando_revision():
    return FLAG_ESPERANDO.exists() and ciclo_en_curso()


def confirmar_continuacion():
    FLAG_CONTINUAR.parent.mkdir(parents=True, exist_ok=True)
    FLAG_CONTINUAR.write_text("1", encoding="utf-8")
    _confirmar.set()


def _esperar_confirmacion():
    """El ciclo sigue en este hilo hasta Continuar, hasta quitar la revisión o hasta la pausa."""
    FLAG_CONTINUAR.unlink(missing_ok=True)
    _confirmar.clear()
    FLAG_ESPERANDO.parent.mkdir(parents=True, exist_ok=True)
    FLAG_ESPERANDO.write_text("1", encoding="utf-8")
    log.info("Clasificación lista: esperando confirmación en el dashboard", **_extra())
    try:
        while True:
            if _en_pausa():
                log.warning("Servicio en pausa: el ciclo para durante la revisión", **_extra())
                return False
            if _cortar_ciclo:
                log.warning("Paso detenido: el ciclo no sigue", **_extra())
                return False
            if not _revision_activa():
                log.info("Revisión desactivada: el ciclo sigue", **_extra())
                return True
            if _confirmar.is_set() or FLAG_CONTINUAR.exists():
                log.info("Confirmación recibida: el ciclo sigue", **_extra())
                return True
            _confirmar.wait(ESPERA_REVISION_SEG)
    finally:
        FLAG_ESPERANDO.unlink(missing_ok=True)
        FLAG_CONTINUAR.unlink(missing_ok=True)
        _confirmar.clear()


def ciclo_en_curso():
    candado = FileLock(str(CANDADO_CICLO))
    try:
        candado.acquire(timeout=0)
    except Timeout:
        return True
    candado.release()
    return False


# ── Un paso ──────────────────────────────────────────────────────────────

def paso_en_curso():
    return _paso_en_curso


def ejecutar_paso(nombre, ciclo_id=None, origen="manual", eco=None):
    global _paso_en_curso
    del ciclo_id
    if nombre not in PASOS:
        raise ValueError(f"Paso desconocido: {nombre}. Opciones: {', '.join(ORDEN)}")
    definicion = PASOS[nombre]
    eco = sys.stdout is not None if eco is None else eco

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(SCRIPTS), str(RAIZ), env.get("PYTHONPATH")])
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PROYECTO_E_PASO"] = nombre
    if origen != "manual":
        env["LUPE_MODO"] = "automatico"
        env["PROYECTO_E_AUTOMATICO"] = "1"

    cmd = [_python(), "-u", str(SCRIPTS / definicion["script"]), *definicion["args"]]
    banderas = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log.info("Inicio del paso %s", nombre, **_extra(nombre))
    inicio = time.monotonic()
    lineas = []
    codigo = None
    _paso_en_curso = nombre
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(SCRIPTS),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=banderas,
        )
        lector = threading.Thread(target=_leer_salida, args=(proc, lineas, eco), daemon=True)
        lector.start()
        try:
            codigo = proc.wait(timeout=TIMEOUT_PASO_SEG)
        except subprocess.TimeoutExpired:
            proc.kill()
            codigo = proc.wait()
            lineas.append(f"\n[orquestador] Paso detenido: superó {TIMEOUT_PASO_SEG // 60} min\n")
        lector.join(timeout=10)
    except Exception as error:
        lineas.append(f"\n[orquestador] No se pudo ejecutar el paso: {error}\n")
        codigo = -1
    finally:
        _paso_en_curso = None

    salida = "".join(lineas)
    minutos = (time.monotonic() - inicio) / 60
    estado = _estado_por_codigo(nombre, codigo, salida)
    nivel = logging.INFO if estado == "ok" else logging.WARNING if estado == "aviso" else logging.ERROR
    log.log(
        nivel,
        "Fin del paso %s: %s (código %s, %.1f min)",
        nombre,
        estado,
        codigo,
        minutos,
        **_extra(nombre),
    )
    return {"paso": nombre, "estado": estado, "codigo": codigo}


def _leer_salida(proc, lineas, eco):
    from app.alertas import anotar_desde_linea

    for linea in proc.stdout:
        if anotar_desde_linea(linea):
            continue
        lineas.append(linea)
        if eco:
            print(linea, end="", flush=True)


def _errores_en_salida(salida):
    import re

    for linea in reversed(salida.splitlines()):
        if "Resumen:" in linea and "errores=" in linea:
            hallado = re.search(r"errores=(\d+)", linea)
            return int(hallado.group(1)) if hallado else 0
    return 0


def _estado_por_codigo(nombre, codigo, salida):
    if codigo == 0:
        return "aviso" if _errores_en_salida(salida) > 0 else "ok"
    if codigo == CODIGO_LOGIN:
        alertar(
            "lupe_login",
            "Lupe pide iniciar sesión",
            "Ejecuta «python main.py login» en este PC y completa el login con Google. "
            f"El paso {nombre} y los siguientes que usan Lupe se omitieron en este ciclo.",
        )
        return "aviso"
    if codigo == CODIGO_OCUPADO:
        log.warning("Lupe ocupado; %s se reintenta en el siguiente ciclo", nombre, **_extra(nombre))
        return "aviso"
    cola = "\n".join(salida.strip().splitlines()[-8:])
    alertar("paso_error", f"El paso {nombre} terminó con error (código {codigo})", cola)
    return "error"


# ── El ciclo completo ────────────────────────────────────────────────────

def ejecutar_ciclo(origen="programado", pasos=None, eco=None):
    """Corre los pasos en orden. Devuelve el estado del ciclo, u «ocupado» si ya hay uno."""
    candado = FileLock(str(CANDADO_CICLO))
    CANDADO_CICLO.parent.mkdir(parents=True, exist_ok=True)
    try:
        candado.acquire(timeout=0)
    except Timeout:
        log.warning("Ya hay un ciclo en curso; no se arranca otro", **_extra())
        return "ocupado"

    try:
        return _ciclo(origen, pasos or ORDEN, eco)
    finally:
        candado.release()


def _ciclo(origen, pasos, eco):
    global _cortar_ciclo
    from utils.config_manager import validar_criticos

    _cortar_ciclo = False
    nuevo_ciclo()
    try:
        registro.marcar_ultimo_ciclo()
    except Exception as error:
        alertar("paso_error", "Base de datos local con problemas", str(error))
        log.error("No se pudo registrar el ciclo en runs.db: %s", error, **_extra())
    FLAG_ESPERANDO.unlink(missing_ok=True)
    FLAG_CONTINUAR.unlink(missing_ok=True)
    _confirmar.clear()
    log.info("Inicio del ciclo (%s): %s", origen, ", ".join(pasos), **_extra())
    if FLAG_DETENER.exists():
        limpiar_detener()

    try:
        validar_criticos()
    except Exception as error:
        alertar("paso_error", "Configuración inválida", str(error))
        log.error("Configuración inválida: %s", error, **_extra())
        return "error"

    peor = "ok"
    sin_lupe = False
    for indice, nombre in enumerate(pasos):
        if _cortar_ciclo:
            log.warning("Paso detenido: el ciclo no sigue", **_extra())
            break
        if pausado() and origen != "manual":
            log.warning("Servicio en pausa: el ciclo para antes de %s", nombre, **_extra())
            break
        if sin_lupe and PASOS[nombre]["lupe"]:
            log.warning("Paso %s omitido: Lupe pide iniciar sesión", nombre, **_extra(nombre))
            continue
        resultado = ejecutar_paso(nombre, origen=origen, eco=eco)
        if resultado["codigo"] == CODIGO_LOGIN:
            sin_lupe = True
        if _GRAVEDAD[resultado["estado"]] > _GRAVEDAD[peor]:
            peor = resultado["estado"]
        if _cortar_ciclo:
            log.warning("Paso detenido: el ciclo no sigue", **_extra())
            break
        decision = decision_tras_clasificar(
            nombre,
            resultado["estado"],
            bool(pasos[indice + 1 :]),
            _revision_activa(),
        )
        if decision == "parar":
            log.warning("Clasificar terminó con error: el ciclo no sigue", **_extra(nombre))
            break
        if decision == "esperar" and not _esperar_confirmacion():
            break

    log.info("Fin del ciclo: %s", peor, **_extra())
    return peor
