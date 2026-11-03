"""Lectura y escritura del .env sin perder comentarios ni valores existentes."""

import re
from pathlib import Path

_LINEA = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _limpiar(valor):
    valor = valor.strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
        valor = valor[1:-1]
    return valor


def leer(ruta):
    """{variable: valor} de las líneas activas (las comentadas se ignoran)."""
    ruta = Path(ruta)
    if not ruta.exists():
        return {}
    valores = {}
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        hallado = _LINEA.match(linea)
        if hallado:
            valores[hallado.group(1)] = _limpiar(hallado.group(2))
    return valores


def escribir(ruta, variable, valor):
    """Pone variable=valor: reemplaza la línea activa si existe; si no, la agrega al final."""
    ruta = Path(ruta)
    valor = str(valor).replace("\r", "").replace("\n", "").strip()
    lineas = ruta.read_text(encoding="utf-8-sig").splitlines() if ruta.exists() else []
    for i, linea in enumerate(lineas):
        hallado = _LINEA.match(linea)
        if hallado and hallado.group(1) == variable:
            lineas[i] = f"{variable}={valor}"
            break
    else:
        lineas.append(f"{variable}={valor}")
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def completar_desde_ejemplo(ruta_env, ruta_ejemplo):
    """Crea el .env desde .env.example o le agrega (con su valor de ejemplo) las variables
    nuevas que no tenga. Nunca cambia ni borra lo que ya hay. Devuelve las agregadas."""
    ruta_env, ruta_ejemplo = Path(ruta_env), Path(ruta_ejemplo)
    if not ruta_env.exists():
        ruta_env.write_text(ruta_ejemplo.read_text(encoding="utf-8"), encoding="utf-8")
        return list(leer(ruta_env))
    actuales = leer(ruta_env)
    nuevas = [(k, v) for k, v in leer(ruta_ejemplo).items() if k not in actuales]
    if nuevas:
        texto = ruta_env.read_text(encoding="utf-8-sig").rstrip("\n")
        texto += "\n\n# Agregadas por el instalador (variables nuevas de .env.example)\n"
        texto += "\n".join(f"{k}={v}" for k, v in nuevas) + "\n"
        ruta_env.write_text(texto, encoding="utf-8")
    return [k for k, _ in nuevas]
