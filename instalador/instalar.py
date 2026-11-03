"""Pasos del instalador que van después del .venv (los llama INSTALAR.bat).

    python -m instalador.instalar               instalación completa (se puede repetir)
    python -m instalador.instalar --actualizar  solo agrega al .env las variables nuevas

Cada paso muestra [OK] o [FALTA] y se salta si ya está hecho. Nunca imprime claves.
"""

import argparse
import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for ruta in (str(RAIZ), str(RAIZ / "scripts")):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

from instalador import archivo_env

ENV = RAIZ / ".env"
EJEMPLO = RAIZ / ".env.example"

_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# variable: (pregunta, validación, oculta)
PREGUNTAS = {
    "SLACK_BOT_TOKEN": ("Token del bot de Slack (te lo da Jeisembert, empieza por xoxb-)",
                        lambda v: v.startswith("xoxb-") and len(v) > 20, True),
    "GMAIL_REPLY_TO": ("Correo al que se responden los casos (GMAIL_REPLY_TO)",
                       lambda v: bool(_CORREO.match(v)), False),
}
IA = {
    "GROQ_API_KEY": ("Clave de Groq (empieza por gsk_)", lambda v: v.startswith("gsk_") and len(v) > 20),
    "GEMINI_API_KEY": ("Clave de Gemini (empieza por AIza)", lambda v: v.startswith("AIza") and len(v) > 20),
}


def ok(texto):
    print(f"[OK]    {texto}")


def falta(texto, como=""):
    print(f"[FALTA] {texto}")
    if como:
        print(f"        → {como}")


def _preguntar(pregunta, valida, oculta, opcional=False):
    while True:
        extra = " (Enter para omitir)" if opcional else ""
        if oculta:
            valor = getpass.getpass(f"  {pregunta}{extra} (no se ve al escribir): ").strip()
        else:
            valor = input(f"  {pregunta}{extra}: ").strip()
        if not valor and opcional:
            return ""
        if valida(valor):
            return valor
        print("  Ese valor no tiene el formato esperado; inténtalo de nuevo.")


# ── Pasos ────────────────────────────────────────────────────────────────

def paso_env(preguntar=_preguntar):
    agregadas = archivo_env.completar_desde_ejemplo(ENV, EJEMPLO)
    if agregadas and len(agregadas) < 40:
        print(f"  Variables nuevas agregadas al .env: {', '.join(agregadas)}")
    valores = archivo_env.leer(ENV)
    for variable, (pregunta, valida, oculta) in PREGUNTAS.items():
        if not valida(valores.get(variable, "")):
            archivo_env.escribir(ENV, variable, preguntar(pregunta, valida, oculta))
    valores = archivo_env.leer(ENV)
    if not any(valida(valores.get(v, "")) for v, (_, valida) in IA.items()):
        print("  Se necesita al menos una clave de IA (Groq o Gemini). Cada persona usa las suyas.")
        while True:
            for variable, (pregunta, valida) in IA.items():
                valor = preguntar(pregunta, valida, True, opcional=True)
                if valor:
                    archivo_env.escribir(ENV, variable, valor)
            valores = archivo_env.leer(ENV)
            if any(valida(valores.get(v, "")) for v, (_, valida) in IA.items()):
                break
            print("  Hace falta al menos una de las dos.")
    valores = archivo_env.leer(ENV)
    proveedor = (valores.get("IA_PROVIDER") or "groq").lower()
    tiene = {v: IA[v][1](valores.get(v, "")) for v in IA}
    if proveedor == "groq" and not tiene["GROQ_API_KEY"] and not valores.get("GROQ_API_KEY2"):
        archivo_env.escribir(ENV, "IA_PROVIDER", "gemini")
    elif proveedor == "gemini" and not tiene["GEMINI_API_KEY"]:
        archivo_env.escribir(ENV, "IA_PROVIDER", "groq")
    ok(".env con Slack, correo de respuesta y claves de IA")
    return True


def _cargar_config():
    """Recarga config con el .env actual (los pasos van llenando variables)."""
    import importlib

    from dotenv import load_dotenv

    load_dotenv(ENV, override=True)
    import config
    return importlib.reload(config)


def paso_credenciales(elegir=None):
    from instalador import google_cuenta

    config = _cargar_config()
    destino = Path(config.GOOGLE_CREDENTIALS_FILE)
    if destino.exists() and google_cuenta.credencial_valida(destino)[0]:
        ok("credentials.json")
        return True
    print("  Se abrirá una ventana para elegir tu archivo de credenciales de Google (puede tener cualquier nombre).")
    if google_cuenta.asegurar_credenciales(destino, **({"elegir": elegir} if elegir else {})):
        ok("credentials.json copiado a data-json/credentials.json")
        return True
    falta("credentials.json", "Descarga el JSON del cliente OAuth (Aplicación de escritorio) y vuelve a correr INSTALAR.bat")
    return False


def paso_autorizar():
    os.environ.pop("PROYECTO_E_AUTOMATICO", None)
    _cargar_config()
    try:
        import importlib

        import autenticar as modulo
        importlib.reload(modulo)
        print("  Si se abre el navegador, entra con tu cuenta de Google y acepta los permisos.")
        creds = modulo.autenticar()
    except Exception as error:
        falta(f"Autorización de Google: {error}", "Vuelve a correr INSTALAR.bat y acepta todos los permisos")
        return None
    ok("Autorización de Google (token.json)")
    return creds


def paso_etiquetas(creds, construir=None):
    from googleapiclient.discovery import build

    from instalador import google_cuenta

    construir = construir or build
    try:
        gmail = construir("gmail", "v1", credentials=creds)
        ids, creadas = google_cuenta.asegurar_etiquetas(gmail, archivo_env.leer(ENV))
    except Exception as error:
        falta(f"Etiquetas de Gmail: {error}", "Revisa tu conexión y vuelve a correr INSTALAR.bat")
        return False
    _escribir_ids(ids)
    ok("Etiquetas de Gmail" + (f" (creadas: {', '.join(sorted(set(creadas)))})" if creadas else " (ya existían)"))
    return True


def paso_drive(creds, construir=None):
    from googleapiclient.discovery import build

    from instalador import google_cuenta

    construir = construir or build
    try:
        drive = construir("drive", "v3", credentials=creds)
        sheets = construir("sheets", "v4", credentials=creds)
        ids, creados = google_cuenta.asegurar_drive(drive, sheets, archivo_env.leer(ENV))
    except Exception as error:
        falta(f"Drive (legal-ops, hojas y plantillas): {error}", "Revisa tu conexión y vuelve a correr INSTALAR.bat")
        return False
    _escribir_ids(ids)
    from instalador import recursos
    esperadas = {v for v, _, _ in recursos.PLANTILLAS} | {v for v, _, _ in recursos.HOJAS}
    if esperadas - set(ids):
        falta("Drive: faltan " + ", ".join(sorted(esperadas - set(ids))))
        return False
    ok("Drive: carpeta legal-ops, hojas y plantillas" + (f" (creados: {len(creados)})" if creados else " (ya existían)"))
    return True


def _escribir_ids(ids):
    """Escribe los IDs solo donde el .env está vacío o tiene otro valor no vigente."""
    actuales = archivo_env.leer(ENV)
    for variable, valor in ids.items():
        if actuales.get(variable, "").strip() != valor:
            archivo_env.escribir(ENV, variable, valor)


def paso_lupe(preguntar=input):
    _cargar_config()
    import lupe_navegador

    if lupe_navegador.estado_sesion() == "vigente":
        ok("Sesión de Lupe")
        return True
    respuesta = preguntar("  ¿Abrir ahora el inicio de sesión de Lupe? (s/n): ").strip().lower()
    if respuesta.startswith("s"):
        subprocess.call([sys.executable, str(RAIZ / "main.py"), "login"], cwd=str(RAIZ))
    if lupe_navegador.estado_sesion() == "vigente":
        ok("Sesión de Lupe")
        return True
    print("[AVISO] Lupe sin sesión: hazlo luego con «Iniciar sesión en Lupe» en el Dashboard o «python main.py login».")
    return True


def paso_diagnostico():
    return subprocess.call([sys.executable, str(RAIZ / "scripts" / "diagnostico.py")], cwd=str(RAIZ)) == 0


def instalar():
    print("\n== Configuración del Proyecto E ==\n")
    resultados = {"env": paso_env(), "credenciales": paso_credenciales()}
    creds = paso_autorizar() if resultados["credenciales"] else None
    resultados["google"] = creds is not None
    if creds is not None:
        resultados["etiquetas"] = paso_etiquetas(creds)
        resultados["drive"] = paso_drive(creds)
    else:
        falta("Etiquetas y Drive", "Primero hace falta la autorización de Google")
        resultados["etiquetas"] = resultados["drive"] = False
    paso_lupe()
    print("\n== Diagnóstico ==\n")
    resultados["diagnostico"] = paso_diagnostico()
    pendientes = [k for k, v in resultados.items() if not v]
    if pendientes:
        print(f"\nInstalación INCOMPLETA. Pendiente: {', '.join(pendientes)}.")
        print("Corrige lo que salió en [FALTA] y vuelve a correr INSTALAR.bat: solo hará lo que falte.")
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="instalador.instalar")
    parser.add_argument("--actualizar", action="store_true", help="Solo agrega variables nuevas al .env")
    args = parser.parse_args(argv)
    if args.actualizar:
        agregadas = archivo_env.completar_desde_ejemplo(ENV, EJEMPLO)
        ok("Variables nuevas en el .env: " + (", ".join(agregadas) if agregadas else "ninguna"))
        return 0
    return instalar()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
