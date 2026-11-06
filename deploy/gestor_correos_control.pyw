#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Gestor de correos - Panel de Control de Escritorio
--------------------------------------------------
- Detecta automáticamente el entorno virtual en `.venv`, `venv`, `env`
  o cualquier carpeta `*\Scripts\python.exe` dentro del proyecto.
- Si fue abierto con el Python global pero existe un venv en el proyecto,
  usa siempre el Python del venv para el servicio y sus dependencias.
- Diseño unificado con el Dashboard Web (`app/dashboard/templates/base.html`),
  con dimensiones amplias y cálculo dinámico de ancho para que ningún texto
  se corte en pantallas con escalado 125% / 150% DPI.
"""

import ctypes
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import messagebox

# Activar High-DPI en Windows
if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    # Sin un AppUserModelID propio, la barra de tareas agrupa la ventana con pythonw.exe
    # y muestra el ícono de Python. Debe fijarse antes de crear la ventana.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Rappi.GestorCorreos")
    except Exception:
        pass

# --- Rutas dinámicas (funciona en cualquier PC y carpeta) ---
DEPLOY_DIR = Path(__file__).resolve().parent
RAIZ = DEPLOY_DIR.parent
LOCK_SERVICIO = RAIZ / "data" / "servicio.lock"
LOCK_CICLO = RAIZ / "data" / "ciclo.lock"
DB_PATH = RAIZ / "data" / "runs.db"
FLAG_PAUSA = RAIZ / "logs" / "servicio.pausado"
FLAG_DETENER = RAIZ / "logs" / "detener.flujo"
FLAG_APAGADO_MANUAL = RAIZ / "logs" / "servicio.apagado_manual"
LUPE_ESTADO_JSON = RAIZ / "logs" / "lupe_sesion_estado.json"
LOG_PROYECTO = RAIZ / "logs" / "proyecto_e.log"
LOG_ARRANQUE = RAIZ / "logs" / "servicio_arranque.log"
PID_SERVICIO = RAIZ / "logs" / "gestor_servicio.pid"
SIGNAL_SHOW = RAIZ / "logs" / "gestor_mostrar.signal"
LOCK_UI = RAIZ / "logs" / "gestor_ui.lock"
ICO_PATH = DEPLOY_DIR / "gestor_correos.ico"
ENV_PATH = RAIZ / ".env"


def descubrir_venv_dir() -> Path | None:
    """Busca `.venv`, `venv`, `env` o cualquier subcarpeta con `Scripts/python.exe`."""
    for nombre in (".venv", "venv", "env"):
        candidato = RAIZ / nombre
        if (candidato / "Scripts" / "python.exe").is_file():
            return candidato
    try:
        for p in RAIZ.glob("*/Scripts/python.exe"):
            if p.is_file():
                return p.parent.parent
    except Exception:
        pass
    return None


VENV_DIR = descubrir_venv_dir()

# Añadir site-packages del venv al sys.path actual por si este script .pyw
# fue lanzado con el Python global del sistema en vez del venv
if VENV_DIR is not None:
    site_pkgs = VENV_DIR / "Lib" / "site-packages"
    if site_pkgs.is_dir() and str(site_pkgs) not in sys.path:
        sys.path.insert(0, str(site_pkgs))

SCRIPTS_PROYECTO = (
    "main.py",
    "clasificar_correos_gmail.py",
    "agente_rts.py",
    "procesar_correos_rt.py",
    "leer_slack.py",
    "procesar_entes.py",
    "consultar_lupe.py",
    "generar_borrador.py",
    "enviar_correo.py",
)

# --- Paleta exacta de app/dashboard/templates/base.html ---
C_FONDO = "#F4F5F7"
C_TARJETA = "#ffffff"
C_BORDE = "#DDE1E6"
C_BORDE_BTN = "#d9dde3"
C_TEXTO = "#1D2129"
C_SUAVE = "#5B6270"
C_NARANJA = "#E8620C"
C_NARANJA_HOVER = "#C9520A"
C_DURAZNO = "#FDEBDD"
C_DURAZNO_BORDE = "#F3C9A8"
C_PAUSA_BG = "#FFF8F2"
C_PAUSA_BORDE = "#F3C9A8"
C_OK_BG = "#e5f4ec"
C_OK_FG = "#3d8b66"
C_AVISO_BG = "#fff1e4"
C_AVISO_FG = "#b86a2a"
C_ERR_BG = "#fdecea"
C_ERR_FG = "#b55248"
C_OMITIDO_BG = "#eef0f3"
C_BTN_HOVER = "#f7f8fa"

# Dimensiones amplias anti-recorte DPI
WIN_W = 680
WIN_H = 500
CONTENT_W = WIN_W - 48  # 632 px
CARD_W = 204            # 3 tarjetas: 204 * 3 + 10 * 2 = 632 px


def leer_env_valor(clave: str, defecto: str = "") -> str:
    try:
        if ENV_PATH.is_file():
            for linea in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
                linea = linea.strip()
                if linea.startswith(f"{clave}="):
                    return linea.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return defecto


def leer_puerto_dashboard() -> int:
    val = leer_env_valor("DASHBOARD_PUERTO", "8000")
    return int(val) if val.isdigit() else 8000


def leer_ciclo_horas() -> float:
    val = leer_env_valor("CICLO_HORAS", "4")
    try:
        return float(val)
    except ValueError:
        return 4.0


PUERTO = leer_puerto_dashboard()
DASHBOARD_URL = f"http://127.0.0.1:{PUERTO}"


def obtener_python_servicio() -> str:
    """
    Devuelve `python.exe` del entorno virtual (`.venv`, `venv`, etc.).
    Se usa `python.exe` con `CREATE_NO_WINDOW` en lugar de `pythonw.exe` para que
    `sys.stdout` y `sys.stderr` sean descriptores válidos y cualquier error de arranque
    quede registrado en `logs/servicio_arranque.log`.
    """
    venv = descubrir_venv_dir()
    if venv is not None:
        py = venv / "Scripts" / "python.exe"
        if py.is_file():
            return str(py)
        pyw = venv / "Scripts" / "pythonw.exe"
        if pyw.is_file():
            return str(pyw)

    actual = Path(sys.executable)
    py_consola = actual.with_name("python.exe")
    if py_consola.is_file():
        return str(py_consola)
    return str(actual)


def puerto_activo(puerto: int = PUERTO) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", puerto), timeout=0.45):
            return True
    except OSError:
        return False


def candado_ocupado(ruta_lock: Path) -> bool:
    if not ruta_lock.exists():
        return False
    try:
        from filelock import FileLock, Timeout
        lock = FileLock(str(ruta_lock))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return True
        else:
            lock.release()
            return False
    except Exception:
        return False


def servicio_corriendo() -> bool:
    return puerto_activo() or candado_ocupado(LOCK_SERVICIO) or candado_ocupado(LOCK_CICLO)


def servicio_pausado() -> bool:
    return FLAG_PAUSA.exists()


def estado_actual() -> str:
    """Devuelve 'prendido', 'pausado' o 'apagado'."""
    if not servicio_corriendo():
        return "apagado"
    if servicio_pausado():
        return "pausado"
    return "prendido"


def leer_ultimo_error_arranque() -> str:
    """Si el servicio no pudo arrancar, lee la causa desde `servicio_arranque.log` o `proyecto_e.log`."""
    for ruta in (LOG_ARRANQUE, LOG_PROYECTO):
        try:
            if ruta.is_file():
                lineas = [
                    l.strip()
                    for l in ruta.read_text(encoding="utf-8", errors="replace").splitlines()
                    if l.strip()
                ]
                for l in reversed(lineas[-15:]):
                    bajo = l.lower()
                    if any(k in bajo for k in ("error", "exception", "modulenotfounderror", "configurationerror", "inválida", "faltan")):
                        partes = l.split("|")
                        limpio = partes[-1].strip() if len(partes) >= 3 else l
                        return limpio[:95]
        except Exception:
            pass
    return ""


def consultar_estado_servicio() -> dict | None:
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/estado", timeout=1.2) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def obtener_info_tarjetas() -> dict:
    ciclo_h = leer_ciclo_horas()
    ciclo_txt = f"Cada {ciclo_h:g} h"
    proximo_txt = "-"
    trabajo = {"activo": False, "finalizando": False, "esperando": False, "etiqueta": "", "estado": None}
    lupe_estado = ""
    lupe_tipo = "omitido"
    mostrar_lupe = False
    envio_txt = "Borrador" if leer_env_valor("CONTROL_CALIDAD", "false").lower() == "true" else "Enviar"
    en_curso = candado_ocupado(LOCK_CICLO)

    remoto = consultar_estado_servicio() if puerto_activo() else None
    if remoto:
        proximo_txt = remoto.get("proximo_txt") or "-"
        trabajo = remoto.get("trabajo") or trabajo
        en_curso = bool(trabajo.get("activo"))
        envio_txt = "Borrador" if remoto.get("borrador") else "Enviar"
        if remoto.get("lupe_atencion"):
            mostrar_lupe = True
            est = remoto.get("estado_lupe") or ""
            if est == "requiere_login":
                lupe_estado, lupe_tipo = "Pide login", "error"
            elif est == "sin_perfil":
                lupe_estado, lupe_tipo = "Sin perfil", "aviso"
            else:
                lupe_estado, lupe_tipo = "Atención", "aviso"
    else:
        try:
            if LUPE_ESTADO_JSON.is_file():
                datos = json.loads(LUPE_ESTADO_JSON.read_text(encoding="utf-8"))
                est = datos.get("estado", "")
                if est == "requiere_login":
                    lupe_estado, lupe_tipo, mostrar_lupe = "Pide login", "error", True
                elif est == "sin_perfil":
                    lupe_estado, lupe_tipo, mostrar_lupe = "Sin perfil", "aviso", True
        except Exception:
            pass

    return {
        "proximo": proximo_txt,
        "ciclo_sub": ciclo_txt,
        "lupe_texto": lupe_estado,
        "lupe_tipo": lupe_tipo,
        "mostrar_lupe": mostrar_lupe,
        "envio": envio_txt,
        "en_curso": en_curso,
        "trabajo": trabajo,
    }


def notificar_windows(titulo: str, mensaje: str):
    def _enviar():
        try:
            from win11toast import notify
            notify(titulo, mensaje, on_click=DASHBOARD_URL)
        except Exception:
            pass
    threading.Thread(target=_enviar, daemon=True).start()


def prender_servicio(en_pausa: bool = False, notificar: bool = True) -> tuple[bool, str]:
    FLAG_PAUSA.parent.mkdir(parents=True, exist_ok=True)
    (RAIZ / "data").mkdir(parents=True, exist_ok=True)
    try:
        FLAG_APAGADO_MANUAL.unlink(missing_ok=True)
        if en_pausa:
            FLAG_PAUSA.write_text("1", encoding="utf-8")
        else:
            FLAG_PAUSA.unlink(missing_ok=True)
            if not (puerto_activo() or candado_ocupado(LOCK_SERVICIO) or candado_ocupado(LOCK_CICLO)):
                FLAG_DETENER.unlink(missing_ok=True)
    except Exception:
        pass

    if servicio_corriendo():
        if notificar:
            if en_pausa:
                notificar_windows("Gestor de correos", "El servicio está en pausa. Panel web activo.")
            else:
                notificar_windows("Gestor de correos", "Servicio activo y ciclos programados habilitados.")
        return True, ""

    py_exe = obtener_python_servicio()
    main_py = str(RAIZ / "main.py")
    banderas = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    venv = descubrir_venv_dir()
    if venv is not None:
        env["VIRTUAL_ENV"] = str(venv)
        scripts_dir = str(venv / "Scripts")
        env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")

    try:
        LOG_ARRANQUE.write_text("", encoding="utf-8")
    except Exception:
        pass

    archivo_salida = None
    try:
        archivo_salida = open(LOG_ARRANQUE, "a", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            [py_exe, "-u", main_py, "servicio"],
            cwd=str(RAIZ),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=archivo_salida,
            stderr=subprocess.STDOUT,
            creationflags=banderas,
        )
        try:
            PID_SERVICIO.write_text(str(proc.pid), encoding="utf-8")
        except Exception:
            pass
    except Exception as exc:
        if archivo_salida is not None:
            try:
                archivo_salida.close()
            except Exception:
                pass
        return False, str(exc)

    # Esperar hasta ~7 segundos; si el proceso termina antes con error, salir de inmediato
    for _ in range(18):
        if servicio_corriendo():
            break
        if proc.poll() is not None:
            break
        time.sleep(0.4)

    if archivo_salida is not None:
        try:
            archivo_salida.flush()
            archivo_salida.close()
        except Exception:
            pass

    activo = servicio_corriendo()
    if activo:
        if notificar:
            if en_pausa:
                notificar_windows("Gestor de correos", "Panel web iniciado en modo pausa.")
            else:
                notificar_windows("Gestor de correos", "Servicio iniciado correctamente.")
        return True, ""

    detalle_err = leer_ultimo_error_arranque() or "No se pudo iniciar main.py servicio (revisa .env o venv)."
    return False, detalle_err


def pausar_servicio(notificar: bool = True):
    FLAG_PAUSA.parent.mkdir(parents=True, exist_ok=True)
    try:
        FLAG_PAUSA.write_text("1", encoding="utf-8")
    except Exception:
        pass
    if notificar:
        notificar_windows("Gestor de correos", "El servicio está en pausa. No se lanzan ciclos programados.")


def reanudar_servicio(notificar: bool = True):
    try:
        FLAG_PAUSA.unlink(missing_ok=True)
    except Exception:
        pass
    if notificar:
        notificar_windows("Gestor de correos", "Ciclos automáticos reanudados.")


def apagar_servicio(notificar: bool = True):
    FLAG_PAUSA.parent.mkdir(parents=True, exist_ok=True)
    try:
        FLAG_APAGADO_MANUAL.write_text("1", encoding="utf-8")
        FLAG_PAUSA.write_text("1", encoding="utf-8")
        FLAG_DETENER.write_text("1", encoding="utf-8")
    except Exception:
        pass

    banderas = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    mi_pid = os.getpid()

    if PID_SERVICIO.is_file():
        try:
            pid_txt = PID_SERVICIO.read_text(encoding="utf-8").strip()
            if pid_txt.isdigit() and int(pid_txt) != mi_pid:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", pid_txt],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=banderas,
                )
        except Exception:
            pass
        try:
            PID_SERVICIO.unlink(missing_ok=True)
        except Exception:
            pass

    if os.name == "nt":
        patrones = "|".join(s.replace(".", "\\.") for s in SCRIPTS_PROYECTO)
        ps_cmd = f"""
        $miPid = {mi_pid}
        $miProc = Get-CimInstance Win32_Process -Filter "ProcessId = $miPid" -ErrorAction SilentlyContinue
        $miPadre = if ($miProc) {{ $miProc.ParentProcessId }} else {{ 0 }}
        $excluir = @($miPid, $miPadre)

        $pids = [System.Collections.Generic.HashSet[int]]::new()

        Get-NetTCPConnection -LocalPort {PUERTO} -ErrorAction SilentlyContinue | ForEach-Object {{
            if ($_.OwningProcess -gt 4 -and ($excluir -notcontains $_.OwningProcess)) {{
                [void]$pids.Add([int]$_.OwningProcess)
            }}
        }}

        Get-CimInstance Win32_Process | Where-Object {{
            $_.Name -match '^pythonw?\\.exe$' -and
            ($excluir -notcontains $_.ProcessId) -and
            $_.CommandLine -notlike '*gestor_correos_control*' -and
            $_.CommandLine -notlike '*lsp_server*' -and
            $_.CommandLine -match '({patrones})'
        }} | ForEach-Object {{
            [void]$pids.Add([int]$_.ProcessId)
        }}

        Get-CimInstance Win32_Process | Where-Object {{
            $_.Name -match '^(chrome|chromium|msedge)\\.exe$' -and
            $_.CommandLine -like '*lupe_profile*'
        }} | ForEach-Object {{
            [void]$pids.Add([int]$_.ProcessId)
        }}

        foreach ($id in $pids) {{
            & taskkill.exe /F /T /PID $id 2>$null | Out-Null
        }}
        """
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=banderas,
                timeout=10,
            )
        except Exception:
            pass

    for _ in range(12):
        if not servicio_corriendo():
            break
        time.sleep(0.3)

    for lock_file in (LOCK_SERVICIO, LOCK_CICLO):
        if lock_file.exists():
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass

    if notificar:
        notificar_windows("Gestor de correos", "El servicio se ha apagado por completo.")


# --- Utilidades de dibujo estilo CSS en Canvas de Tkinter ---
def dibujar_rect_redondeado(canvas: tk.Canvas, x1, y1, x2, y2, r=12, fill="#ffffff", outline="#e6e8ec", width=1, tags=()):
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    puntos = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(puntos, smooth=True, fill=fill, outline=outline, width=width, tags=tags)


class BotonDashboard(tk.Canvas):
    """Botón con el estilo exacto de app/dashboard/templates/base.html y ancho generoso."""

    def __init__(self, parent, texto: str, comando, variante="secundario", width=180, height=38, bg_parent=C_TARJETA):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=bg_parent,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.texto = texto
        self.comando = comando
        self.variante = variante
        self.w = width
        self.h = height
        self.deshabilitado = False
        self.hover = False

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._redibujar()

    def configurar(self, texto=None, variante=None, deshabilitado=None):
        if texto is not None:
            self.texto = texto
        if variante is not None:
            self.variante = variante
        if deshabilitado is not None:
            self.deshabilitado = deshabilitado
            self.configure(cursor="arrow" if deshabilitado else "hand2")
        self._redibujar()

    def _colores(self):
        if self.variante == "principal":
            fill = C_NARANJA_HOVER if (self.hover and not self.deshabilitado) else C_NARANJA
            outline = fill
            fg = "#ffffff"
            weight = "bold"
        elif self.variante == "activo_suave":
            fill = C_DURAZNO
            outline = C_DURAZNO_BORDE
            fg = C_TEXTO
            weight = "bold"
        else:
            fill = C_BTN_HOVER if (self.hover and not self.deshabilitado) else "#ffffff"
            outline = C_BORDE_BTN
            fg = C_TEXTO
            weight = "normal"

        if self.deshabilitado and self.variante != "activo_suave":
            fg = C_SUAVE
            if self.variante == "principal":
                fill = "#f59d84"
                outline = "#f59d84"
                fg = "#ffffff"
        return fill, outline, fg, weight

    def _redibujar(self):
        self.delete("all")
        fill, outline, fg, weight = self._colores()
        dibujar_rect_redondeado(self, 1, 1, self.w - 2, self.h - 2, r=8, fill=fill, outline=outline, width=1)
        self.create_text(
            self.w // 2,
            self.h // 2,
            text=self.texto,
            fill=fg,
            font=("Segoe UI", 9, weight),
        )

    def _on_enter(self, _event):
        if not self.deshabilitado:
            self.hover = True
            self._redibujar()

    def _on_leave(self, _event):
        self.hover = False
        self._redibujar()

    def _on_click(self, _event):
        if not self.deshabilitado and callable(self.comando):
            self.comando()


class GestorCorreosApp:
    def __init__(self, iniciar_oculto: bool = False):
        self.iniciar_oculto = iniciar_oculto
        self.cerrando = False
        self.transicion_en_curso = False
        self.ultimo_error_msg = ""
        self.tray_icon = None
        self.tiene_tray = False

        self.root = tk.Tk()
        self.root.title("Gestor de correos")
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=C_FONDO)

        if ICO_PATH.is_file():
            try:
                self.root.iconbitmap(default=str(ICO_PATH))
            except Exception:
                pass

        self._construir_interfaz()
        self._centrar_ventana()
        self.root.protocol("WM_DELETE_WINDOW", self._al_cerrar_ventana)

        self._iniciar_bandeja()

        if self.iniciar_oculto:
            self.root.withdraw()

        self.root.update_idletasks()
        self._actualizar_ui()
        self._revisar_senal_mostrar()

    def _centrar_ventana(self):
        self.root.update_idletasks()
        alto = max(WIN_H, self.root.winfo_reqheight())
        x = (self.root.winfo_screenwidth() // 2) - (WIN_W // 2)
        y = (self.root.winfo_screenheight() // 2) - (alto // 2)
        self.root.geometry(f"{WIN_W}x{alto}+{x}+{y}")

    def _construir_interfaz(self):
        header = tk.Frame(self.root, bg=C_TARJETA, height=56)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(header, text="Gestor de correos", font=("Segoe UI", 12, "bold"), bg=C_TARJETA, fg=C_TEXTO).pack(side="left", padx=20)
        enlace = tk.Label(header, text="Abrir panel web", font=("Segoe UI", 9, "bold"), bg=C_TARJETA, fg=C_SUAVE, cursor="hand2")
        enlace.pack(side="right", padx=20)
        enlace.bind("<Button-1>", lambda _e: self._accion_abrir_panel())
        enlace.bind("<Enter>", lambda _e: enlace.configure(fg=C_NARANJA))
        enlace.bind("<Leave>", lambda _e: enlace.configure(fg=C_SUAVE))
        tk.Frame(self.root, bg=C_BORDE, height=1).pack(fill="x")
        main = tk.Frame(self.root, bg=C_FONDO, padx=24, pady=18)
        main.pack(fill="both", expand=True)
        self.canvas_banner = tk.Canvas(main, width=CONTENT_W, height=48, bg=C_FONDO, highlightthickness=0, bd=0)
        self.canvas_banner.pack(fill="x", pady=(0, 14))
        card = tk.Frame(main, bg=C_TARJETA, highlightbackground=C_BORDE, highlightthickness=1, padx=18, pady=16)
        card.pack(fill="x", pady=(0, 14))
        self.lbl_estado = tk.Label(card, text="Automatización activa", font=("Segoe UI", 11, "bold"), bg=C_TARJETA, fg=C_TEXTO)
        self.lbl_estado.pack(anchor="w")
        self.lbl_proximo = tk.Label(card, text="Próxima ejecución", font=("Segoe UI", 9), bg=C_TARJETA, fg=C_SUAVE)
        self.lbl_proximo.pack(anchor="w", pady=(6, 16))
        botones = tk.Frame(card, bg=C_TARJETA)
        botones.pack(fill="x", pady=(0, 14))
        self.btn_pausar = BotonDashboard(botones, texto="Pausar", comando=self._accion_pausar, variante="principal", width=155, height=38)
        self.btn_pausar.pack(side="left", padx=(0, 12))
        self.btn_apagar = BotonDashboard(botones, texto="Apagar servicio", comando=self._accion_apagar, variante="secundario", width=155, height=38)
        self.btn_apagar.pack(side="left")
        tk.Frame(card, bg=C_BORDE, height=1).pack(fill="x", pady=(2, 10))
        self.lbl_web = tk.Label(card, text="Consulta la actividad en el panel local", font=("Segoe UI", 9), bg=C_TARJETA, fg=C_SUAVE, cursor="hand2")
        self.lbl_web.pack(anchor="w")
        self.lbl_web.bind("<Button-1>", lambda _e: self._accion_abrir_panel())
        self.frm_trabajo = tk.Frame(main, bg=C_TARJETA, highlightbackground=C_BORDE, highlightthickness=1, padx=18, pady=12)
        self.lbl_trabajo = tk.Label(self.frm_trabajo, text="", font=("Segoe UI", 10, "bold"), bg=C_TARJETA, fg=C_TEXTO)
        self.lbl_trabajo.pack(anchor="w")
        self.frm_lupe = tk.Frame(main, bg=C_TARJETA, highlightbackground=C_BORDE, highlightthickness=1, padx=18, pady=12)
        self.lbl_lupe = tk.Label(self.frm_lupe, text="", font=("Segoe UI", 10, "bold"), bg=C_TARJETA, fg=C_ERR_FG)
        self.lbl_lupe.pack(anchor="w")
        self.pie_ventana = tk.Label(main, text="Al cerrar esta ventana, el servicio conserva su estado.", font=("Segoe UI", 8), bg=C_FONDO, fg=C_SUAVE)
        self.pie_ventana.pack(side="bottom", pady=(12, 0))

    def _dibujar_banner(self, texto: str, bg: str, borde: str, fg: str):
        self.canvas_banner.delete("all")
        dibujar_rect_redondeado(self.canvas_banner, 1, 1, CONTENT_W - 2, 46, r=12, fill=bg, outline=borde, width=1)
        self.canvas_banner.create_text(
            18,
            24,
            text=texto,
            anchor="w",
            width=CONTENT_W - 36,
            fill=fg,
            font=("Segoe UI", 9),
        )

    def _aplicar_estilo_estado(self, est: str, info: dict):
        if est == "pausado":
            estado = "Automatización en pausa"
            texto_pausa = "Reanudar ciclos"
            variante = "principal"
            banner = "No se lanzan ciclos programados. Las acciones manuales continúan disponibles."
            bg, borde, fg = C_PAUSA_BG, C_PAUSA_BORDE, C_TEXTO
        elif est == "prendido":
            estado = "Automatización activa"
            texto_pausa = "Pausar ciclos"
            variante = "principal"
            banner = "Los ciclos programados se ejecutan automáticamente."
            bg, borde, fg = C_OK_BG, "#c3e6d1", C_OK_FG
        else:
            estado = "Servicio apagado"
            texto_pausa = "Iniciar en pausa"
            variante = "principal"
            banner = self.ultimo_error_msg or "El servicio está apagado."
            bg, borde, fg = C_DURAZNO, C_DURAZNO_BORDE, C_TEXTO
        self.lbl_estado.configure(text=estado)
        self.lbl_proximo.configure(text=f"Próxima ejecución: {info.get('proximo') or 'No programada'}")
        self.btn_pausar.configurar(texto=texto_pausa, variante=variante, deshabilitado=False)
        self.btn_apagar.configurar(texto="Apagar servicio" if est != "apagado" else "Apagado", variante="secundario", deshabilitado=(est == "apagado"))
        self._dibujar_banner(banner, bg=bg, borde=borde, fg=fg)
        trabajo = info.get("trabajo") or {}
        if est != "apagado" and info.get("en_curso"):
            texto = "Esperando confirmación de clasificación" if trabajo.get("esperando") else (trabajo.get("etiqueta") or "Ciclo en curso")
            if trabajo.get("finalizando"):
                texto += " · Terminando el caso actual"
            self.lbl_trabajo.configure(text=texto)
            if not self.frm_trabajo.winfo_ismapped():
                self.frm_trabajo.pack(fill="x", before=self.pie_ventana, pady=(0, 12))
        elif self.frm_trabajo.winfo_ismapped():
            self.frm_trabajo.pack_forget()
        if est != "apagado" and info.get("mostrar_lupe"):
            self.lbl_lupe.configure(text=f"Lupe: {info.get('lupe_texto') or 'Requiere atención'}")
            if not self.frm_lupe.winfo_ismapped():
                self.frm_lupe.pack(fill="x", before=self.pie_ventana, pady=(0, 12))
        elif self.frm_lupe.winfo_ismapped():
            self.frm_lupe.pack_forget()

    def _refrescar_ahora(self):
        try:
            est = estado_actual()
            info = obtener_info_tarjetas()
            self._aplicar_estilo_estado(est, info)
        except Exception:
            pass

    def _actualizar_ui(self):
        if self.cerrando:
            return
        if not self.transicion_en_curso:
            self._refrescar_ahora()
        self.root.after(2000, self._actualizar_ui)

    def _revisar_senal_mostrar(self):
        if self.cerrando:
            return
        try:
            if SIGNAL_SHOW.exists():
                SIGNAL_SHOW.unlink(missing_ok=True)
                self.mostrar_ventana()
        except Exception:
            pass
        self.root.after(800, self._revisar_senal_mostrar)

    def _accion_prender(self):
        if self.transicion_en_curso:
            return
        self.transicion_en_curso = True
        self._dibujar_banner("Iniciando el servicio, espera unos segundos...", C_PAUSA_BG, C_PAUSA_BORDE, C_TEXTO)
        self.root.update_idletasks()
        threading.Thread(target=self._hilo_prender, daemon=True).start()

    def _hilo_prender(self):
        try:
            if servicio_corriendo() and servicio_pausado():
                reanudar_servicio(notificar=True)
                ok, err = True, ""
            else:
                ok, err = prender_servicio(en_pausa=False, notificar=True)
            self.ultimo_error_msg = "" if ok else err
        finally:
            self.transicion_en_curso = False
            self.root.after(50, self._refrescar_ahora)

    def _accion_pausar(self):
        if self.transicion_en_curso:
            return
        self.transicion_en_curso = True
        self._dibujar_banner("Poniendo el servicio en pausa...", C_PAUSA_BG, C_PAUSA_BORDE, C_TEXTO)
        self.root.update_idletasks()
        threading.Thread(target=self._hilo_pausar, daemon=True).start()

    def _hilo_pausar(self):
        try:
            if not servicio_corriendo():
                ok, err = prender_servicio(en_pausa=True, notificar=True)
                self.ultimo_error_msg = "" if ok else err
            else:
                pausar_servicio(notificar=True)
                self.ultimo_error_msg = ""
        finally:
            self.transicion_en_curso = False
            self.root.after(50, self._refrescar_ahora)

    def _accion_apagar(self):
        if self.transicion_en_curso:
            return
        info = obtener_info_tarjetas()
        aviso = "Se apagará el servicio por completo."
        if info.get("en_curso"):
            aviso = (
                "Hay un caso o paso en proceso. Si apagas ahora se interrumpirá el servicio."
                + "\n\n"
                + aviso
            )
        if not messagebox.askyesno(
            "Apagar servicio",
            aviso + "\n\n\u00bfConfirmas apagar el servicio?",
        ):
            return
        self.transicion_en_curso = True
        self.ultimo_error_msg = ""
        self._dibujar_banner(
            "Deteniendo todos los procesos del servicio...",
            C_DURAZNO,
            C_DURAZNO_BORDE,
            C_TEXTO,
        )
        self.root.update_idletasks()
        threading.Thread(target=self._hilo_apagar, daemon=True).start()

    def _hilo_apagar(self):
        try:
            apagar_servicio(notificar=True)
        finally:
            self.transicion_en_curso = False
            self.root.after(50, self._refrescar_ahora)

    def _accion_abrir_panel(self):
        if servicio_corriendo():
            webbrowser.open(DASHBOARD_URL)
            return

        respuesta = messagebox.askyesno(
            "Gestor de correos",
            "El servicio está apagado.\n\n"
            "¿Deseas abrir el Panel Web en modo pausa (sin lanzar ciclos automáticos)?",
            parent=self.root,
        )
        if not respuesta:
            return

        self.transicion_en_curso = True
        self._dibujar_banner("Abriendo el panel web en modo pausa...", C_PAUSA_BG, C_PAUSA_BORDE, C_TEXTO)
        self.root.update_idletasks()

        def _abrir_en_pausa():
            try:
                ok, err = prender_servicio(en_pausa=True, notificar=False)
                self.ultimo_error_msg = "" if ok else err
                if ok:
                    webbrowser.open(DASHBOARD_URL)
            finally:
                self.transicion_en_curso = False
                self.root.after(50, self._refrescar_ahora)

        threading.Thread(target=_abrir_en_pausa, daemon=True).start()

    def mostrar_ventana(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _al_cerrar_ventana(self):
        if self.tiene_tray:
            self.root.withdraw()
        else:
            self.cerrar_aplicacion()

    def cerrar_aplicacion(self):
        self.cerrando = True
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def _iniciar_bandeja(self):
        try:
            import pystray
            from PIL import Image, ImageDraw

            def crear_imagen_icono():
                img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(239, 78, 35, 255), outline=(205, 58, 20, 255), width=3)
                d.rectangle((14, 20, 50, 44), fill=(255, 255, 255, 255))
                d.line((14, 20, 32, 33), fill=(239, 78, 35, 255), width=3)
                d.line((50, 20, 32, 33), fill=(239, 78, 35, 255), width=3)
                return img

            menu = pystray.Menu(
                pystray.MenuItem("Abrir Gestor de correos", lambda icon, item: self.root.after(0, self.mostrar_ventana), default=True),
                pystray.MenuItem("Abrir panel web", lambda icon, item: self.root.after(0, self._accion_abrir_panel)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Prender / Reanudar", lambda icon, item: self.root.after(0, self._accion_prender)),
                pystray.MenuItem("Pausar ciclos", lambda icon, item: self.root.after(0, self._accion_pausar)),
                pystray.MenuItem("Apagar todo", lambda icon, item: self.root.after(0, self._accion_apagar)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir del icono", lambda icon, item: self.root.after(0, self.cerrar_aplicacion)),
            )
            self.tray_icon = pystray.Icon("gestor_correos", crear_imagen_icono(), "Gestor de correos", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            self.tiene_tray = True
        except Exception:
            self.tiene_tray = False

    def run(self):
        self.root.mainloop()


def main():
    auto_start = "--auto-start" in sys.argv or "--minimized" in sys.argv

    if auto_start and not FLAG_APAGADO_MANUAL.exists():
        prender_servicio(en_pausa=FLAG_PAUSA.exists(), notificar=True)

    LOCK_UI.parent.mkdir(parents=True, exist_ok=True)
    try:
        from filelock import FileLock, Timeout
        ui_lock = FileLock(str(LOCK_UI))
        try:
            ui_lock.acquire(timeout=0)
        except Timeout:
            if not auto_start:
                try:
                    SIGNAL_SHOW.write_text("1", encoding="utf-8")
                except Exception:
                    pass
            return 0
    except Exception:
        ui_lock = None

    try:
        app = GestorCorreosApp(iniciar_oculto=auto_start)
        app.run()
    finally:
        if ui_lock is not None:
            try:
                ui_lock.release()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
