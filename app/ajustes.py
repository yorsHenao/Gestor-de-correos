"""Valores que el dashboard cambia en caliente: envío y confirmación tras clasificar."""

import os
import re

from config import BASE_DIR

RUTA_ENV = BASE_DIR / ".env"


def borrador():
    return os.getenv("CONTROL_CALIDAD", "false").strip().lower() == "true"


def confirmar_tras_clasificar():
    return os.getenv("REVISION_CLASIFICACION", "false").strip().lower() == "true"


def calidad_editable(en_curso, en_pausa):
    """El modo de envío no cambia con un ciclo andando, salvo que el servicio esté en pausa."""
    return (not en_curso) or en_pausa


def guardar(nombre, activo, ruta=None):
    texto = "true" if activo else "false"
    os.environ[nombre] = texto
    escribir_variable(RUTA_ENV if ruta is None else ruta, nombre, texto)


def escribir_variable(ruta, nombre, texto):
    """Reemplaza una línea CLAVE=valor. El resto del archivo queda igual."""
    original = ruta.read_bytes() if ruta.is_file() else b""
    fin = b"\r\n" if b"\r\n" in original else b"\n"
    lineas = original.splitlines()
    nueva = f"{nombre}={texto}".encode("utf-8")
    patron = re.compile(rf"^\s*{re.escape(nombre)}\s*=".encode("utf-8"))
    hallada = False
    salida = []
    for linea in lineas:
        if not hallada and patron.match(linea):
            salida.append(nueva)
            hallada = True
        else:
            salida.append(linea)
    if not hallada:
        salida.append(nueva)
    ruta.write_bytes(fin.join(salida) + fin)
