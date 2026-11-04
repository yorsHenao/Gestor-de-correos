import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import ajustes, login_lupe, orquestador, scheduler
from app.alertas import pendientes as alertas_pendientes
from config import CICLO_HORAS, DASHBOARD_PUERTO, DRIVE_SHEETS_ENTES_ID, DRIVE_SHEETS_ID

log = logging.getLogger("proyecto_e")
_EXTRA = {"extra": {"paso": "dashboard", "caso": "-"}}

app = FastAPI(title="Proyecto E", docs_url=None, redoc_url=None, openapi_url=None)
plantillas = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_ORIGENES_LOCALES = {f"127.0.0.1:{DASHBOARD_PUERTO}", f"localhost:{DASHBOARD_PUERTO}"}
_CACHE_SHEET = {"momento": 0.0, "datos": None}
_CACHE_SEG = 300

_ORDEN_RT = (
    "En Slack - pendiente",
    "Nuevo",
    "Caso manual",
    "Sin cuenta",
    "Sin cuenta - enviado",
    "Completado",
)
_ORDEN_ENTES = (
    "Error de envío - reintento 1",
    "Error de envío - reintento 2",
    "Error de envío - reintento 3",
    "enviado a Entes",
    "- sin correo- solicitarlo",
    "revisión manual",
)


def _ordenar_conteo(conteo: dict, prioridad: tuple[str, ...]) -> list[tuple[str, int]]:
    def clave(item: tuple[str, int]) -> tuple[int, int | str]:
        estado, _ = item
        if estado in prioridad:
            return (0, prioridad.index(estado))
        return (1, estado.lower())

    return sorted(conteo.items(), key=clave)


@app.middleware("http")
async def solo_local(request: Request, call_next):
    """Los botones (POST) solo se aceptan desde el propio dashboard."""
    if request.method == "POST":
        origen = request.headers.get("origin") or request.headers.get("referer") or ""
        if urlsplit(origen).netloc not in _ORIGENES_LOCALES:
            return PlainTextResponse("Origen no permitido", status_code=403)
    return await call_next(request)


def _render(request, plantilla, **contexto):
    contexto.setdefault("pausado", orquestador.pausado())
    contexto.setdefault("alertas", alertas_pendientes(5))
    contexto.setdefault("pagina_activa", "")
    contexto.setdefault("vista_generada", datetime.now())
    return plantillas.TemplateResponse(request, plantilla, contexto)


def _estado_lupe():
    try:
        from lupe_navegador import estado_sesion

        return estado_sesion()
    except Exception as error:
        return f"desconocido ({error})"


def _casos_por_estado():
    if _CACHE_SHEET["datos"] is not None and time.monotonic() - _CACHE_SHEET["momento"] < _CACHE_SEG:
        datos = _CACHE_SHEET["datos"]
        datos.setdefault("rt_lista", _ordenar_conteo(datos.get("rt", {}), _ORDEN_RT))
        datos.setdefault("entes_lista", _ordenar_conteo(datos.get("entes", {}), _ORDEN_ENTES))
        return datos
    datos = {"rt": {}, "entes": {}, "error": "", "actualizado": None}
    try:
        import storage

        sheets = storage._sheets()
        for clave, hoja, columna in (("rt", DRIVE_SHEETS_ID, "D"), ("entes", DRIVE_SHEETS_ENTES_ID, "F")):
            if not hoja:
                continue
            valores = (
                sheets.spreadsheets()
                .values()
                .get(spreadsheetId=hoja, range=f"{columna}2:{columna}")
                .execute()
                .get("values", [])
            )
            datos[clave] = dict(Counter(v[0] for v in valores if v and v[0]).most_common())
        datos["actualizado"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    except Exception as error:
        datos["error"] = str(error)
        log.warning("No se pudieron leer los conteos: %s", error, **_EXTRA)
    datos["rt_lista"] = _ordenar_conteo(datos["rt"], _ORDEN_RT)
    datos["entes_lista"] = _ordenar_conteo(datos["entes"], _ORDEN_ENTES)
    _CACHE_SHEET.update(momento=time.monotonic(), datos=datos)
    return datos


_ETIQUETAS_PASOS = {
    "clasificar": "Clasificar",
    "rt": "RT",
    "slack": "Slack",
    "entes": "Entes",
}


@app.get("/")
def inicio(request: Request):
    en_curso = orquestador.ciclo_en_curso()
    en_pausa = orquestador.pausado()
    nombre_paso = orquestador.paso_en_curso()
    return _render(
        request,
        "inicio.html",
        proximo=scheduler.proximo_ciclo(),
        en_curso=en_curso,
        refresco_seg=10 if en_curso or login_lupe.en_curso() else 0,
        estado_lupe=_estado_lupe(),
        login_lupe=login_lupe.estado(),
        login_abierto=login_lupe.en_curso(),
        casos=_casos_por_estado(),
        pasos=[(nombre, _ETIQUETAS_PASOS.get(nombre, nombre)) for nombre in orquestador.ORDEN],
        ciclo_horas=CICLO_HORAS,
        borrador=ajustes.borrador(),
        confirmar=ajustes.confirmar_tras_clasificar(),
        calidad_bloqueada=not ajustes.calidad_editable(en_curso, en_pausa),
        esperando=orquestador.esperando_revision(),
        deteniendo=orquestador.paso_detenido(),
        paso_en_curso=nombre_paso,
        etiqueta_paso=_ETIQUETAS_PASOS.get(nombre_paso, nombre_paso) if nombre_paso else "",
        pagina_activa="inicio",
    )


def _lineas_registro(limite=200):
    from utils.logger import RUTA_LOG

    if not RUTA_LOG.is_file():
        return ""
    texto = RUTA_LOG.read_text(encoding="utf-8", errors="replace")
    return "\n".join(texto.splitlines()[-limite:])


@app.get("/registro")
def registro(request: Request):
    en_curso = orquestador.ciclo_en_curso()
    return _render(
        request,
        "registro.html",
        lineas=_lineas_registro(),
        en_curso=en_curso,
        refresco_seg=10 if en_curso else 0,
        pagina_activa="registro",
    )


def _volver(destino="/"):
    return RedirectResponse(destino, status_code=303)


@app.post("/acciones/ciclo")
def accion_ciclo():
    if orquestador.ciclo_en_curso():
        return _volver()
    log.info("Ciclo lanzado desde el dashboard", **_EXTRA)
    scheduler.lanzar_en_segundo_plano(orquestador.ejecutar_ciclo, origen="dashboard", eco=False)
    return _volver()


@app.post("/acciones/paso/{nombre}")
def accion_paso(nombre: str):
    if nombre not in orquestador.PASOS:
        raise HTTPException(status_code=404, detail="Paso desconocido")
    if orquestador.ciclo_en_curso():
        return _volver()
    log.info("Paso %s lanzado desde el dashboard", nombre, **_EXTRA)
    scheduler.lanzar_en_segundo_plano(
        orquestador.ejecutar_ciclo, origen="dashboard", pasos=[nombre], eco=False
    )
    return _volver()


@app.post("/acciones/pausar")
def accion_pausar():
    orquestador.pausar()
    return _volver()


@app.post("/acciones/detener")
def accion_detener():
    orquestador.detener_paso()
    return _volver()


@app.post("/acciones/reanudar")
def accion_reanudar():
    orquestador.reanudar()
    return _volver()


@app.post("/acciones/refrescar-sheet")
def accion_refrescar_sheet():
    _CACHE_SHEET["datos"] = None
    return _volver()


@app.post("/acciones/calidad/{modo}")
def accion_calidad(modo: str):
    if modo not in ("borrador", "enviar"):
        raise HTTPException(status_code=404, detail="Modo desconocido")
    if not ajustes.calidad_editable(orquestador.ciclo_en_curso(), orquestador.pausado()):
        log.warning("Modo de envío no cambiado: hay un ciclo en curso", **_EXTRA)
        return _volver()
    ajustes.guardar("CONTROL_CALIDAD", modo == "borrador")
    log.info("Envío en %s", "borrador" if modo == "borrador" else "directo", **_EXTRA)
    return _volver()


@app.post("/acciones/revision/{modo}")
def accion_revision(modo: str):
    if modo not in ("continuo", "confirmar"):
        raise HTTPException(status_code=404, detail="Modo desconocido")
    ajustes.guardar("REVISION_CLASIFICACION", modo == "confirmar")
    log.info("Tras clasificar: %s", modo, **_EXTRA)
    return _volver()


@app.post("/acciones/continuar")
def accion_continuar():
    orquestador.confirmar_continuacion()
    log.info("Continuación confirmada desde el dashboard", **_EXTRA)
    return _volver()


@app.post("/acciones/login-lupe")
def accion_login_lupe():
    login_lupe.lanzar()
    return _volver()


@app.get("/acciones/login-lupe/estado")
def estado_login_lupe():
    return {**login_lupe.estado(), "abierto": login_lupe.en_curso(), "sesion": _estado_lupe()}
