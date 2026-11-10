import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import actividad, ajustes, login_lupe, orquestador, scheduler
from app.alertas import descartar as descartar_alertas
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
    "Completado",
    "Petición repetida - caso gestionado",
    "En Slack - pendiente",
    "Petición repetida - caso pendiente",
    "Petición repetida - revisar",
    "Nuevo",
    "Caso manual",
    "Sin cuenta",
    "Sin cuenta - enviado",
    "Sin cuenta - pendiente",
)
_ORDEN_ENTES = (
    "enviado a Entes",
    "Enviado",
    "Error de envío - reintento 1",
    "Error de envío - reintento 2",
    "Error de envío - reintento 3",
    "- sin correo- solicitarlo",
    "Sin correo, solicitarlo",
    "revisión manual",
    "Se pide más información",
    "Correo enviado",
)

_ATENCION_RT = {
    "en slack - pendiente",
    "nuevo",
    "caso manual",
    "petición repetida - revisar",
}
_ATENCION_ENTES = {
    "error de envío - reintento 1",
    "error de envío - reintento 2",
    "error de envío - reintento 3",
    "- sin correo- solicitarlo",
    "sin correo, solicitarlo",
    "revisión manual",
    "se pide más información",
}


def normalizar_conteo_estados(conteo):
    agrupado = {}
    for estado, cantidad in (conteo or {}).items():
        if estado is None:
            continue
        etiqueta = " ".join(str(estado).split())
        if not etiqueta:
            continue
        try:
            n = int(cantidad)
        except (TypeError, ValueError):
            continue
        clave = etiqueta.casefold()
        if clave in agrupado:
            agrupado[clave] = (agrupado[clave][0], agrupado[clave][1] + n)
        else:
            agrupado[clave] = (etiqueta, n)
    return {etiqueta: n for etiqueta, n in agrupado.values()}


def _ordenar_conteo(conteo: dict, prioridad: tuple[str, ...]) -> list[tuple[str, int]]:
    prioridad_cf = {p.casefold(): i for i, p in enumerate(prioridad)}

    def clave(item: tuple[str, int]) -> tuple[int, int | str]:
        estado, _ = item
        cf = estado.casefold()
        if cf in prioridad_cf:
            return (0, prioridad_cf[cf])
        return (1, cf)

    return sorted(conteo.items(), key=clave)


def _lista_casos(conteo, prioridad, atencion):
    lista = []
    for estado, n in _ordenar_conteo(conteo, prioridad):
        lista.append(
            {
                "estado": estado,
                "n": n,
                "atencion": estado.casefold() in atencion or estado.casefold().startswith("error de envío"),
            }
        )
    return lista


@app.middleware("http")
async def solo_local(request: Request, call_next):
    """Los botones (POST) solo se aceptan desde el propio dashboard."""
    if request.method == "POST":
        origen = request.headers.get("origin") or request.headers.get("referer") or ""
        if urlsplit(origen).netloc not in _ORIGENES_LOCALES:
            return PlainTextResponse("Origen no permitido", status_code=403)
    return await call_next(request)


def _estado_lupe():
    try:
        from lupe_navegador import estado_sesion

        return estado_sesion()
    except Exception as error:
        return f"desconocido ({error})"


def _tarjeta_lupe(estado_lupe, login, abierto):
    """(vigente, título, ayuda) para la tarjeta de Lupe del inicio."""
    vigente = estado_lupe == "vigente" and not abierto
    if vigente:
        return True, "Sesión vigente", "Los pasos con Lupe pueden ejecutarse."
    if abierto:
        return False, "Desconectado", "Abriendo ventana de login…"
    if login.get("estado") == "error":
        return False, "Desconectado", "No se completó el último intento."
    if estado_lupe == "sin_perfil":
        return False, "Desconectado", "Aún no hay sesión de Lupe en este PC."
    if orquestador.pendientes_lupe():
        return False, "Desconectado", "Hay pasos en espera; se reanudan al iniciar sesión."
    if estado_lupe == "requiere_login":
        return False, "Desconectado", "Inicia sesión para que corran los pasos con Lupe."
    return False, "Sin verificar", "Inicia sesión para confirmar la sesión de Lupe."


def _casos_por_estado(forzar=False):
    if (
        not forzar
        and _CACHE_SHEET["datos"] is not None
        and time.monotonic() - _CACHE_SHEET["momento"] < _CACHE_SEG
    ):
        return _CACHE_SHEET["datos"]
    datos = {
        "rt": {},
        "entes": {},
        "rt_lista": [],
        "entes_lista": [],
        "error": "",
        "actualizado": None,
        "vacio": True,
    }
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
            crudo = Counter(v[0] for v in valores if v and v[0])
            datos[clave] = normalizar_conteo_estados(dict(crudo))
        datos["actualizado"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        datos["vacio"] = not (datos["rt"] or datos["entes"])
    except Exception as error:
        datos["error"] = str(error)
        log.warning("No se pudieron leer los conteos: %s", error, **_EXTRA)
    datos["rt_lista"] = _lista_casos(datos["rt"], _ORDEN_RT, _ATENCION_RT)
    datos["entes_lista"] = _lista_casos(datos["entes"], _ORDEN_ENTES, _ATENCION_ENTES)
    _CACHE_SHEET.update(momento=time.monotonic(), datos=datos)
    return datos


def _proximo_texto():
    proximo = scheduler.proximo_ciclo()
    if proximo is None:
        return None, ""
    return proximo.isoformat(timespec="seconds"), proximo.strftime("%d/%m %H:%M")


def _contexto_comun():
    trabajo = orquestador.estado_trabajo()
    en_curso = trabajo["activo"]
    en_pausa = orquestador.pausado()
    estado_lupe = _estado_lupe()
    login = login_lupe.estado()
    lupe_atencion = estado_lupe in ("requiere_login", "sin_perfil") or login.get("estado") == "error"
    login_abierto = login_lupe.en_curso()
    lupe_vigente, lupe_titulo, lupe_ayuda = _tarjeta_lupe(estado_lupe, login, login_abierto)
    if lupe_vigente:
        descartar_alertas("lupe_login")
    proximo_iso, proximo_txt = _proximo_texto()
    return {
        "pausado": en_pausa,
        "alertas": alertas_pendientes(5),
        "vista_generada": datetime.now(),
        "proximo": scheduler.proximo_ciclo(),
        "proximo_iso": proximo_iso,
        "proximo_txt": proximo_txt,
        "en_curso": en_curso,
        "trabajo": trabajo,
        "estado_lupe": estado_lupe,
        "login_lupe": login,
        "login_abierto": login_abierto,
        "lupe_atencion": lupe_atencion,
        "lupe_vigente": lupe_vigente,
        "lupe_titulo": lupe_titulo,
        "lupe_ayuda": lupe_ayuda,
        "ciclo_horas": CICLO_HORAS,
        "borrador": ajustes.borrador(),
        "confirmar": ajustes.confirmar_tras_clasificar(),
        "calidad_bloqueada": not ajustes.calidad_editable(en_curso, en_pausa),
        "esperando": trabajo["esperando"],
        "deteniendo": trabajo["finalizando"],
        "paso_en_curso": trabajo["paso"],
        "etiqueta_paso": trabajo["etiqueta"],
        "pasos": [(nombre, orquestador.ETIQUETAS.get(nombre, nombre)) for nombre in orquestador.ORDEN],
    }


def _render(request, plantilla, **contexto):
    base = _contexto_comun()
    base.update(contexto)
    base.setdefault("pagina_activa", "")
    return plantillas.TemplateResponse(request, plantilla, base)


def _estado_payload():
    ctx = _contexto_comun()
    return {
        "pausado": ctx["pausado"],
        "ciclo_horas": ctx["ciclo_horas"],
        "proximo": ctx["proximo_iso"],
        "proximo_txt": ctx["proximo_txt"],
        "trabajo": ctx["trabajo"],
        "borrador": ctx["borrador"],
        "confirmar": ctx["confirmar"],
        "calidad_bloqueada": ctx["calidad_bloqueada"],
        "estado_lupe": ctx["estado_lupe"],
        "lupe_atencion": ctx["lupe_atencion"],
        "lupe_vigente": ctx["lupe_vigente"],
        "lupe_titulo": ctx["lupe_titulo"],
        "lupe_ayuda": ctx["lupe_ayuda"],
        "login_lupe": ctx["login_lupe"],
        "login_abierto": ctx["login_abierto"],
        "servicio": True,
    }


@app.get("/")
def inicio(request: Request):
    return _render(
        request,
        "inicio.html",
        casos=_casos_por_estado(),
        pagina_activa="inicio",
    )




@app.get("/registro")
def registro_legacy():
    return RedirectResponse("/actividad", status_code=303)


@app.get("/actividad")
def vista_actividad(request: Request):
    ambito = request.query_params.get("ambito", "actual")
    paso = request.query_params.get("paso", "todos")
    if ambito not in ("actual", "anteriores"):
        ambito = "actual"
    eventos = actividad.listar(ambito=ambito, paso=None if paso == "todos" else paso)
    return _render(
        request,
        "actividad.html",
        pagina_activa="actividad",
        eventos=eventos,
        ambito=ambito,
        filtro_paso=paso,
    )


@app.get("/estado")
def estado_json():
    return JSONResponse(_estado_payload())


@app.get("/api/actividad")
def api_actividad(request: Request):
    ambito = request.query_params.get("ambito", "actual")
    paso = request.query_params.get("paso", "todos")
    if ambito not in ("actual", "anteriores"):
        ambito = "actual"
    eventos = actividad.listar(ambito=ambito, paso=None if paso == "todos" else paso)
    return JSONResponse(
        {
            "ambito": ambito,
            "paso": paso,
            "eventos": eventos,
            "ciclo_actual": actividad.ciclo_actual_id(),
            "vacio": not eventos,
        }
    )


@app.get("/api/casos")
def api_casos(request: Request):
    forzar = request.query_params.get("actualizar") == "1"
    return JSONResponse(_casos_por_estado(forzar=forzar))


def _volver(destino="/"):
    return RedirectResponse(destino, status_code=303)


def _lanzar_manual(pasos=None):
    if not orquestador.reservar_ejecucion():
        return False
    kwargs = {"origen": "manual", "eco": False, "modo_automatico": False}
    if pasos is not None:
        kwargs["pasos"] = pasos
    scheduler.lanzar_en_segundo_plano(orquestador.ejecutar_ciclo, **kwargs)
    return True


@app.post("/acciones/ciclo")
def accion_ciclo():
    if _lanzar_manual():
        log.info("Ciclo lanzado desde el dashboard", **_EXTRA)
    return _volver()


@app.post("/acciones/paso/{nombre}")
def accion_paso(nombre: str):
    if nombre not in orquestador.PASOS:
        raise HTTPException(status_code=404, detail="Paso desconocido")
    if _lanzar_manual(pasos=[nombre]):
        log.info("Paso %s lanzado desde el dashboard", nombre, **_EXTRA)
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
    _casos_por_estado(forzar=True)
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
