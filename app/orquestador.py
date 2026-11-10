"""

Orden fijo del ciclo: clasificar → rt → slack → entes. RT, Slack 

Códigos de salida de los pasos:
- 0: bien (aviso si el resumen trae errores).
- 20: Lupe pide login → alerta y se omiten los pasos que usan Lupe en este ciclo.
  Quedan en `logs/lupe_pendiente.json` y el scheduler los relanza al volver la sesión.
- 21: Lupe ocupado → aviso; el paso se reintenta en el siguiente ciclo.
- 22: sin conexión a Google/IA → aviso y alerta; el ciclo no sigue y los correos quedan pendientes.
- otro: error → alerta.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from filelock import FileLock, Timeout

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
for ruta in (str(RAIZ), str(SCRIPTS)):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

from app import actividad, registro
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
ETIQUETAS = {
    "clasificar": "Clasificar",
    "rt": "RT",
    "slack": "Slack",
    "entes": "Entes",
    "ciclo": "Ciclo",
}

CODIGO_LOGIN = 20
CODIGO_OCUPADO = 21
CODIGO_RED = 22
TIMEOUT_PASO_SEG = int(float(os.getenv("PASO_TIMEOUT_MIN") or "120") * 60)
FLAG_PAUSA = RAIZ / "logs" / "servicio.pausado"
FLAG_ESPERANDO = RAIZ / "logs" / "revision.esperando"
FLAG_CONTINUAR = RAIZ / "logs" / "revision.continuar"
PENDIENTE_LUPE = RAIZ / "logs" / "lupe_pendiente.json"
CANDADO_CICLO = RAIZ / "data" / "ciclo.lock"
ESPERA_REVISION_SEG = 0.5
_ORIGENES_SIN_PAUSA = frozenset({"manual", "dashboard"})
_confirmar = threading.Event()
_paso_en_curso = None
_cortar_ciclo = False
_lanzamiento_pendiente = False
_lanzamiento_lock = threading.Lock()
_trabajo = {
    "activo": False,
    "paso": None,
    "estado": None,
    "ciclo_id": None,
    "origen": None,
}

_GRAVEDAD = {"ok": 0, "omitido": 0, "aviso": 1, "error": 2}


def _extra(paso="orquestador"):
    return {"extra": {"paso": paso, "caso": "-"}}


def _python():
    """python.exe aunque el servicio corra con pythonw.exe (los hijos necesitan stdout)."""
    actual = Path(sys.executable)
    consola = actual.with_name("python.exe")
    return str(consola if actual.name.lower() == "pythonw.exe" and consola.exists() else actual)


def _etiqueta(nombre):
    return ETIQUETAS.get(nombre, nombre or "")


def _set_trabajo(**valores):
    _trabajo.update(valores)


def estado_trabajo():
    activo = ciclo_en_curso() or _lanzamiento_pendiente
    if not activo:
        return {
            "activo": False,
            "paso": None,
            "estado": None,
            "etiqueta": "",
            "ciclo_id": None,
            "origen": None,
            "deteniendo": False,
            "esperando": False,
            "finalizando": False,
        }
    paso = _paso_en_curso or _trabajo.get("paso")
    esperando = FLAG_ESPERANDO.exists()
    finalizando = bool(_cortar_ciclo) or _trabajo.get("estado") == "finalizando"
    if esperando:
        estado = "esperando_revision"
    elif finalizando:
        estado = "finalizando"
    elif _lanzamiento_pendiente and not _paso_en_curso:
        estado = "iniciando"
    else:
        estado = _trabajo.get("estado") or "ejecutando"
    return {
        "activo": True,
        "paso": paso,
        "estado": estado,
        "etiqueta": _etiqueta(paso) if paso else "Ciclo",
        "ciclo_id": _trabajo.get("ciclo_id") or actividad.ciclo_actual_id(),
        "origen": _trabajo.get("origen"),
        "deteniendo": finalizando,
        "esperando": esperando,
        "finalizando": finalizando,
    }


def pausado():
    return FLAG_PAUSA.exists()


def pausar():
    FLAG_PAUSA.parent.mkdir(parents=True, exist_ok=True)
    FLAG_PAUSA.write_text("1", encoding="utf-8")
    log.warning("Servicio en pausa", **_extra())


def reanudar():
    FLAG_PAUSA.unlink(missing_ok=True)
    log.info("Servicio reanudado", **_extra())


def detener_paso():
    global _cortar_ciclo
    if not ciclo_en_curso() and not _lanzamiento_pendiente:
        return False
    _cortar_ciclo = True
    _set_trabajo(estado="finalizando")
    solicitar_detener()
    log.warning("Parada del paso pedida desde el dashboard", **_extra())
    actividad.registrar(
        "paso_detener",
        "Se solicitó detener el paso en curso.",
        paso=_paso_en_curso or _trabajo.get("paso") or "ciclo",
        detalle="FLAG_DETENER",
        estado="finalizando",
    )
    return True


def paso_detenido():
    return bool(_cortar_ciclo) and ciclo_en_curso()


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
    if not esperando_revision():
        return False
    FLAG_CONTINUAR.parent.mkdir(parents=True, exist_ok=True)
    FLAG_CONTINUAR.write_text("1", encoding="utf-8")
    _confirmar.set()
    actividad.registrar(
        "revision_continuar",
        "Continuación confirmada tras clasificar.",
        paso="clasificar",
        estado="ok",
    )
    return True

def _esperar_confirmacion():
    FLAG_CONTINUAR.unlink(missing_ok=True)
    _confirmar.clear()
    FLAG_ESPERANDO.parent.mkdir(parents=True, exist_ok=True)
    FLAG_ESPERANDO.write_text("1", encoding="utf-8")
    _set_trabajo(paso="clasificar", estado="esperando_revision")
    log.info("Clasificación lista: esperando confirmación en el dashboard", **_extra())
    actividad.registrar(
        "revision_espera",
        "Clasificación lista. Revisa las etiquetas antes de continuar con RT.",
        paso="clasificar",
        estado="esperando",
    )
    try:
        while True:
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
    if _lanzamiento_pendiente:
        return True
    candado = FileLock(str(CANDADO_CICLO))
    try:
        candado.acquire(timeout=0)
    except Timeout:
        return True
    candado.release()
    return False


def reservar_ejecucion():
    global _lanzamiento_pendiente
    with _lanzamiento_lock:
        if _lanzamiento_pendiente:
            return False
        candado = FileLock(str(CANDADO_CICLO))
        try:
            candado.acquire(timeout=0)
        except Timeout:
            return False
        candado.release()
        _lanzamiento_pendiente = True
        _set_trabajo(activo=True, estado="iniciando", paso=None)
        return True


def liberar_reserva():
    global _lanzamiento_pendiente
    with _lanzamiento_lock:
        _lanzamiento_pendiente = False


def paso_en_curso():
    return _paso_en_curso


def pendientes_lupe():
    """{"pasos": [...], "fecha": "AAAA-MM-DD HH:MM:SS"} de los pasos que esperan el login, o None."""
    try:
        datos = json.loads(PENDIENTE_LUPE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pasos = [nombre for nombre in datos.get("pasos") or [] if nombre in PASOS]
    if not pasos:
        return None
    return {"pasos": pasos, "fecha": str(datos.get("fecha") or "")}


def borrar_pendientes_lupe():
    PENDIENTE_LUPE.unlink(missing_ok=True)


def _guardar_pendientes_lupe(pasos):
    previos = (pendientes_lupe() or {}).get("pasos") or []
    juntos = [nombre for nombre in ORDEN if nombre in pasos or nombre in previos]
    try:
        PENDIENTE_LUPE.parent.mkdir(parents=True, exist_ok=True)
        PENDIENTE_LUPE.write_text(
            json.dumps(
                {"pasos": juntos, "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            ),
            encoding="utf-8",
        )
    except OSError as error:
        log.warning("No se pudieron guardar los pasos pendientes de Lupe: %s", error, **_extra())


def _quitar_pendiente_lupe(nombre):
    pendiente = pendientes_lupe()
    if not pendiente or nombre not in pendiente["pasos"]:
        return
    resto = [paso for paso in pendiente["pasos"] if paso != nombre]
    if not resto:
        borrar_pendientes_lupe()
        return
    try:
        PENDIENTE_LUPE.write_text(
            json.dumps({"pasos": resto, "fecha": pendiente["fecha"]}), encoding="utf-8"
        )
    except OSError as error:
        log.warning("No se pudieron actualizar los pasos pendientes de Lupe: %s", error, **_extra())


def ejecutar_paso(nombre, ciclo_id=None, origen="manual", eco=None, modo_automatico=None):
    global _paso_en_curso
    if nombre not in PASOS:
        raise ValueError(f"Paso desconocido: {nombre}. Opciones: {', '.join(ORDEN)}")
    definicion = PASOS[nombre]
    eco = sys.stdout is not None if eco is None else eco
    if modo_automatico is None:
        modo_automatico = origen not in _ORIGENES_SIN_PAUSA

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(SCRIPTS), str(RAIZ), env.get("PYTHONPATH")])
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PROYECTO_E_PASO"] = nombre
    if modo_automatico:
        env["LUPE_MODO"] = "automatico"
        env["PROYECTO_E_AUTOMATICO"] = "1"

    cmd = [_python(), "-u", str(SCRIPTS / definicion["script"]), *definicion["args"]]
    banderas = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log.info("Inicio del paso %s", nombre, **_extra(nombre))
    actividad.registrar(
        "paso_inicio",
        f"Empezó {_etiqueta(nombre)}.",
        paso=nombre,
        ciclo_id=ciclo_id,
    )
    inicio = time.monotonic()
    lineas = []
    codigo = None
    _paso_en_curso = nombre
    _set_trabajo(paso=nombre, estado="finalizando" if _cortar_ciclo else "ejecutando", origen=origen)
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
    detalle = "\n".join(salida.strip().splitlines()[-12:]) if salida.strip() else f"código={codigo}"
    mensaje_fin = {
        "ok": f"{_etiqueta(nombre)} terminó.",
        "aviso": f"{_etiqueta(nombre)} terminó con avisos.",
        "error": f"{_etiqueta(nombre)} terminó con error.",
    }.get(estado, f"{_etiqueta(nombre)} finalizó ({estado}).")
    actividad.registrar(
        "paso_fin",
        mensaje_fin,
        paso=nombre,
        detalle=detalle,
        estado=estado,
        ciclo_id=ciclo_id,
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


def _detalle_sin_red(nombre):
    try:
        from app.scheduler import proximo_ciclo

        proximo = proximo_ciclo()
    except Exception:
        proximo = None
    espera = (
        f"espera el próximo ciclo automático ({proximo:%H:%M})"
        if proximo
        else "espera el próximo ciclo automático"
    )
    return (
        f"El paso {_etiqueta(nombre)} no pudo conectarse. No se perdió nada: los correos "
        "siguen pendientes. Cuando vuelva internet, pulsa «Ciclo completo» en el dashboard "
        f"o {espera}."
    )


def _estado_por_codigo(nombre, codigo, salida):
    if codigo == 0:
        return "aviso" if _errores_en_salida(salida) > 0 else "ok"
    if codigo == CODIGO_LOGIN:
        alertar(
            "lupe_login",
            "Lupe pide iniciar sesión",
            "Usa «Iniciar sesión» en el dashboard y completa el login con Google. "
            f"El paso {nombre} y los siguientes que usan Lupe quedaron en espera "
            "y se reanudan solos al iniciar sesión.",
        )
        return "aviso"
    if codigo == CODIGO_OCUPADO:
        log.warning("Lupe ocupado; %s se reintenta en el siguiente ciclo", nombre, **_extra(nombre))
        return "aviso"
    if codigo == CODIGO_RED:
        alertar("sin_red", "Sin conexión a Google/IA", _detalle_sin_red(nombre))
        return "aviso"
    cola = "\n".join(salida.strip().splitlines()[-8:])
    alertar("paso_error", f"El paso {nombre} terminó con error (código {codigo})", cola)
    return "error"


def ejecutar_ciclo(origen="programado", pasos=None, eco=None, modo_automatico=None):
    """Corre los pasos en orden. Devuelve el estado del ciclo, u «ocupado» si ya hay uno."""
    global _lanzamiento_pendiente
    candado = FileLock(str(CANDADO_CICLO))
    CANDADO_CICLO.parent.mkdir(parents=True, exist_ok=True)
    try:
        candado.acquire(timeout=0)
    except Timeout:
        liberar_reserva()
        log.warning("Ya hay un ciclo en curso; no se arranca otro", **_extra())
        return "ocupado"

    liberar_reserva()
    try:
        return _ciclo(origen, pasos or ORDEN, eco, modo_automatico)
    finally:
        candado.release()
        _set_trabajo(activo=False, paso=None, estado=None, ciclo_id=None, origen=None)


def _ciclo(origen, pasos, eco, modo_automatico):
    global _cortar_ciclo
    from utils.config_manager import validar_criticos

    _cortar_ciclo = False
    nuevo_ciclo()
    ciclo_id = actividad.iniciar_ciclo_id()
    _set_trabajo(activo=True, paso=None, estado="ejecutando", ciclo_id=ciclo_id, origen=origen)
    try:
        registro.marcar_ultimo_ciclo()
    except Exception as error:
        alertar("paso_error", "Base de datos local con problemas", str(error))
        log.error("No se pudo registrar el ciclo en runs.db: %s", error, **_extra())
    FLAG_ESPERANDO.unlink(missing_ok=True)
    FLAG_CONTINUAR.unlink(missing_ok=True)
    _confirmar.clear()
    log.info("Inicio del ciclo (%s): %s", origen, ", ".join(pasos), **_extra())
    actividad.registrar(
        "ciclo_inicio",
        "Empezó el ciclo completo." if len(pasos) > 1 else f"Empezó {_etiqueta(pasos[0])}.",
        paso="ciclo" if len(pasos) > 1 else pasos[0],
        detalle=f"origen={origen}; pasos={','.join(pasos)}",
        ciclo_id=ciclo_id,
    )
    if FLAG_DETENER.exists() and not _cortar_ciclo:
        limpiar_detener()

    try:
        validar_criticos()
    except Exception as error:
        alertar("paso_error", "Configuración inválida", str(error))
        log.error("Configuración inválida: %s", error, **_extra())
        actividad.registrar(
            "ciclo_fin",
            "El ciclo no arrancó por configuración inválida.",
            detalle=str(error),
            estado="error",
            ciclo_id=ciclo_id,
        )
        actividad.cerrar_ciclo_id()
        return "error"

    peor = "ok"
    sin_lupe = False
    auto = modo_automatico
    if auto is None:
        auto = origen not in _ORIGENES_SIN_PAUSA

    for indice, nombre in enumerate(pasos):
        if _cortar_ciclo:
            log.warning("Paso detenido: el ciclo no sigue", **_extra())
            break
        if pausado() and origen not in _ORIGENES_SIN_PAUSA:
            log.warning("Servicio en pausa: el ciclo para antes de %s", nombre, **_extra())
            break
        if sin_lupe and PASOS[nombre]["lupe"]:
            log.warning("Paso %s omitido: Lupe pide iniciar sesión", nombre, **_extra(nombre))
            continue
        resultado = ejecutar_paso(
            nombre,
            ciclo_id=ciclo_id,
            origen=origen,
            eco=eco,
            modo_automatico=auto,
        )
        if resultado["codigo"] == CODIGO_LOGIN:
            sin_lupe = True
            _guardar_pendientes_lupe(
                [nombre] + [paso for paso in pasos[indice + 1 :] if PASOS[paso]["lupe"]]
            )
        elif PASOS[nombre]["lupe"] and resultado["codigo"] not in (CODIGO_OCUPADO, CODIGO_RED):
            _quitar_pendiente_lupe(nombre)
        if _GRAVEDAD[resultado["estado"]] > _GRAVEDAD[peor]:
            peor = resultado["estado"]
        if resultado["codigo"] == CODIGO_RED:
            log.warning("Sin conexión a Google/IA: el ciclo no sigue", **_extra(nombre))
            break
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
    actividad.registrar(
        "ciclo_fin",
        f"El ciclo terminó ({peor}).",
        detalle=f"origen={origen}",
        estado=peor,
        ciclo_id=ciclo_id,
    )
    actividad.cerrar_ciclo_id()
    return peor
