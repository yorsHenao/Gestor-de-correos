"""Navegador único de Lupe: perfil persistente, candado entre procesos y sesión.

Todo uso de Lupe (RT, Slack, login manual y Entes) abre el navegador con
`SesionLupe`, para compartir el mismo perfil sin chocar entre procesos.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from filelock import FileLock, Timeout
from playwright.sync_api import sync_playwright

from config import (
    LUPE_CHANNEL,
    LUPE_LOCK_ESPERA_SEG,
    LUPE_LOGIN_MAX_SEG,
    LUPE_MODO,
    LUPE_PROFILE_DIR,
    LUPE_REINGRESO_SEG,
    LUPE_SESION_ESTADO,
    LUPE_SESSION_FILE,
    URL_LUPE,
)
from flow_control import comprobar_detener
from utils.exceptions import ErrorInfraestructura, LupeOcupado, LupeSesionRequerida
from utils.logger import obtener_log

log = obtener_log()

SELECTOR_TABLERO = "img.animated.jello"
# Lupe ha cambiado el texto del botón ("Ingresa con Google", "Iniciar sesión con Google").
TEXTO_LOGIN = re.compile(r"(Ingresa|Iniciar sesi[oó]n|Login|Sign in) (con|with) Google", re.I)

_ESTADO_POR_EVENTO = {
    "vigente": "vigente",
    "reingreso_silencioso": "vigente",
    "login_manual": "vigente",
    "requiere_login": "requiere_login",
}


def registrar_evento(evento, paso="", detalle=""):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    estado = _ESTADO_POR_EVENTO.get(evento)
    if not estado:
        return
    try:
        LUPE_SESION_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        LUPE_SESION_ESTADO.write_text(
            json.dumps({"estado": estado, "fecha": ahora, "paso": paso}),
            encoding="utf-8",
        )
    except OSError as error:
        log.warning("Lupe: no se pudo escribir el estado de sesión: %s", error)


def _perfil_existe():
    return LUPE_PROFILE_DIR.is_dir() and any(LUPE_PROFILE_DIR.iterdir())


def estado_sesion():
    """vigente | requiere_login | sin_perfil | desconocido, según la última corrida."""
    if not _perfil_existe():
        return "sin_perfil"
    try:
        datos = json.loads(LUPE_SESION_ESTADO.read_text(encoding="utf-8"))
        return datos.get("estado") or "desconocido"
    except (OSError, ValueError):
        return "desconocido"


def aceptar_terminos(page, espera=0):
    """Cierra el diálogo de términos y condiciones si bloquea el tablero."""
    dialogo = page.locator("terms-and-conditions").first
    try:
        if espera:
            dialogo.wait_for(state="visible", timeout=espera)
        elif not dialogo.is_visible():
            return False
    except Exception:
        return False
    boton = page.locator("terms-and-conditions button.accept-button").or_(
        page.locator("button.btn-flat.accept-button", has_text="Aceptar")
    )
    try:
        boton.first.wait_for(state="visible", timeout=3000)
        boton.first.click()
        page.wait_for_timeout(500)
        log.debug("Lupe: términos y condiciones aceptados")
        return True
    except Exception:
        return False


def _visible(page, selector):
    try:
        return page.locator(selector).first.is_visible()
    except Exception:
        return False


def _boton_login(page):
    return page.get_by_text(TEXTO_LOGIN).first


def _login_visible(page):
    try:
        return _boton_login(page).is_visible()
    except Exception:
        return False


def _en_lupe(page):
    return page.url.startswith(URL_LUPE)


def _estado_pagina(page, segundos):
    """dentro | boton_google | google | desconocido."""
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if _en_lupe(page) and _visible(page, SELECTOR_TABLERO):
            return "dentro"
        if _login_visible(page):
            return "boton_google"
        if "accounts.google.com" in page.url:
            return "google"
        page.wait_for_timeout(500)
    return "desconocido"


def _esperar_tablero(page, segundos):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        try:
            if _en_lupe(page):
                aceptar_terminos(page)
                if _visible(page, SELECTOR_TABLERO):
                    return True
            page.wait_for_timeout(1000)
        except Exception:
            time.sleep(1)
    return False


def _esperar_overlay(page):
    try:
        page.wait_for_selector(".cdk-overlay-backdrop", state="hidden", timeout=3000)
    except Exception:
        pass


def asegurar_sesion(page, modo=None, paso=""):
    """Deja la página dentro de Lupe o lanza LupeSesionRequerida.

    Primero intenta el reingreso silencioso con Google. Si Google pide clave,
    2FA o elegir cuenta: en modo automático sale; en interactivo espera a una persona.
    """
    modo = (modo or LUPE_MODO).strip().lower()
    aceptar_terminos(page, 3000)
    estado = _estado_pagina(page, 30)
    if estado == "dentro":
        registrar_evento("vigente", paso)
        _esperar_overlay(page)
        return

    registrar_evento("expirada", paso, estado)
    log.info("Lupe: sesión expirada; intentando reingreso con Google")
    if estado == "boton_google":
        try:
            _boton_login(page).click()
        except Exception:
            pass

    if _esperar_tablero(page, LUPE_REINGRESO_SEG):
        registrar_evento("reingreso_silencioso", paso)
        log.info("Lupe: sesión renovada sin intervención")
        aceptar_terminos(page, 3000)
        _esperar_overlay(page)
        return

    if modo != "interactivo":
        url = urlsplit(page.url)
        donde = f"{url.netloc}{url.path}"
        registrar_evento("requiere_login", paso, donde)
        raise LupeSesionRequerida(details=f"Google pide login ({donde})")

    log.warning(
        "Lupe: complete el inicio de sesión en la ventana de Chromium (máximo %s s)",
        LUPE_LOGIN_MAX_SEG,
    )
    if _esperar_tablero(page, LUPE_LOGIN_MAX_SEG):
        registrar_evento("login_manual", paso)
        log.info("Lupe: sesión iniciada")
        aceptar_terminos(page, 3000)
        _esperar_overlay(page)
        return

    registrar_evento("requiere_login", paso, "tiempo agotado esperando el login manual")
    raise LupeSesionRequerida(details="Tiempo agotado esperando el login manual")


class CandadoLupe:
    """Bloqueo de archivo para que un solo proceso use el perfil de Lupe."""

    def __init__(self, paso):
        self.paso = paso
        self.ruta = Path(f"{LUPE_PROFILE_DIR}.lock")
        self.ruta_info = Path(f"{LUPE_PROFILE_DIR}.lock.info")
        self._lock = FileLock(str(self.ruta))

    @property
    def tomado(self):
        return self._lock.is_locked

    def _dueno(self):
        try:
            datos = json.loads(self.ruta_info.read_text(encoding="utf-8"))
            return f"{datos.get('paso')} desde {datos.get('desde')}"
        except (OSError, ValueError):
            return "otro proceso"

    def tomar(self, espera_seg=None):
        if self.tomado:
            return
        espera = LUPE_LOCK_ESPERA_SEG if espera_seg is None else espera_seg
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        limite = time.monotonic() + espera
        proximo_aviso = 0.0
        while True:
            try:
                self._lock.acquire(timeout=0)
                break
            except Timeout:
                pass
            ahora = time.monotonic()
            if ahora >= limite:
                dueno = self._dueno()
                registrar_evento("ocupado", self.paso, dueno)
                raise LupeOcupado(details=f"En uso por {dueno}")
            if ahora >= proximo_aviso:
                log.info("Lupe en uso por otro paso (%s); esperando", self._dueno())
                proximo_aviso = ahora + 60
            comprobar_detener("Parada mientras se esperaba a que Lupe quedara libre.")
            time.sleep(min(2, max(0.1, limite - ahora)))

        self.ruta_info.write_text(
            json.dumps(
                {
                    "paso": self.paso,
                    "pid": os.getpid(),
                    "desde": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            ),
            encoding="utf-8",
        )

    def soltar(self):
        if not self.tomado:
            return
        try:
            self.ruta_info.unlink(missing_ok=True)
        except OSError:
            pass
        self._lock.release()


class SesionLupe:
    """Abre Lupe una vez por lote con el perfil persistente.

    El navegador se abre en la primera llamada a `pagina()`; si nadie la pide,
    no se abre ni se toma el candado. Al salir del `with` se cierra todo.
    """

    def __init__(self, paso=None, modo=None):
        self.paso = paso or Path(sys.argv[0]).stem or "lupe"
        self.modo = modo or LUPE_MODO
        self._candado = CandadoLupe(self.paso)
        self._playwright = None
        self._context = None
        self._page = None
        self._sesion_ok = False
        self._reaperturas = 0
        self._cerrando = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()
        return False

    def pagina(self):
        """Página dentro de Lupe; reabre el navegador una vez si se cerró."""
        if self._navegador_activo():
            return self._page
        if self._context is not None:
            if self._reaperturas >= 1:
                raise ErrorInfraestructura(
                    "El navegador de Lupe se cerró otra vez en el mismo lote"
                )
            self._reaperturas += 1
            log.warning("Lupe: el navegador se cerró; reabriendo")
            self._cerrar_navegador()
        self._abrir()
        return self._page

    def reabrir(self):
        """Fuerza una reapertura tras un TargetClosedError que dejó objetos aparentemente vivos."""
        if self._reaperturas >= 1:
            raise ErrorInfraestructura(
                "El navegador de Lupe se cerró otra vez en el mismo lote"
            )
        self._reaperturas += 1
        self._cerrar_navegador()
        self._abrir()
        return self._page

    def _navegador_activo(self):
        if self._page is None or self._context is None:
            return False
        try:
            navegador = self._context.browser
            return (
                not self._page.is_closed()
                and self._page in self._context.pages
                and (navegador is None or navegador.is_connected())
            )
        except Exception:
            return False

    def playwright(self):
        """Playwright del lote, para abrir otros navegadores sin tocar Lupe ni el candado.

        Dos sync_playwright no pueden convivir en el mismo hilo.
        """
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        return self._playwright

    def _abrir(self):
        self._candado.tomar()
        try:
            nuevo = not _perfil_existe()
            LUPE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self.playwright()
            opciones = {
                "headless": False,
                "locale": "es-ES",
                "viewport": {"width": 1280, "height": 720},
            }
            if LUPE_CHANNEL:
                opciones["channel"] = LUPE_CHANNEL
            self._context = self._playwright.chromium.launch_persistent_context(
                str(LUPE_PROFILE_DIR), **opciones
            )
            self._context.on("close", self._al_cerrar_contexto)
            paginas = self._context.pages
            self._page = paginas[0] if paginas else self._context.new_page()
            estado_previo = self._leer_sesion_json() if nuevo else None
            if estado_previo:
                self._migrar_cookies(estado_previo)
            self._page.goto(URL_LUPE, wait_until="domcontentloaded", timeout=60_000)
            if estado_previo and self._migrar_local_storage(estado_previo):
                self._page.reload(wait_until="domcontentloaded", timeout=60_000)
            asegurar_sesion(self._page, self.modo, self.paso)
            self._sesion_ok = True
        except Exception:
            self._cerrar_navegador()
            self._candado.soltar()
            raise

    def _al_cerrar_contexto(self, _contexto):
        if not self._cerrando:
            registrar_evento("navegador_cerrado", self.paso, "cierre inesperado")

    @staticmethod
    def _leer_sesion_json():
        try:
            return json.loads(Path(LUPE_SESSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _migrar_cookies(self, estado):
        try:
            self._context.add_cookies(estado.get("cookies") or [])
            registrar_evento("migracion_sesion_json", self.paso, "cookies")
            log.info("Lupe: perfil nuevo, cookies importadas de sesion.json")
        except Exception as error:
            log.warning("Lupe: no se pudieron importar las cookies de sesion.json: %s", error)

    def _migrar_local_storage(self, estado):
        origen = URL_LUPE.rstrip("/")
        for item in estado.get("origins") or []:
            if item.get("origin") != origen or not item.get("localStorage"):
                continue
            try:
                self._page.evaluate(
                    "items => items.forEach(i => localStorage.setItem(i.name, i.value))",
                    item["localStorage"],
                )
                return True
            except Exception:
                return False
        return False

    def _cerrar_navegador(self):
        if self._context is not None:
            self._cerrando = True
            if self._sesion_ok:
                try:
                    self._context.storage_state(path=str(LUPE_SESSION_FILE))
                except Exception:
                    pass
            try:
                self._context.close()
            except Exception:
                pass
            finally:
                self._cerrando = False
        self._context = None
        self._page = None
        self._sesion_ok = False

    def cerrar(self):
        """Cierra el navegador, exporta sesion.json de respaldo y suelta el candado."""
        self._cerrar_navegador()
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._candado.soltar()


def salir_por_lupe(error):
    """Termina el script con el código de salida del error de Lupe (20 o 21)."""
    nivel = log.error if isinstance(error, LupeSesionRequerida) else log.warning
    nivel("Lupe: %s", error)
    sys.exit(error.codigo)


if __name__ == "__main__":
    print(f"Perfil: {LUPE_PROFILE_DIR}")
    print(f"Estado de la sesión: {estado_sesion()}")
