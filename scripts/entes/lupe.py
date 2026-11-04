"""Búsqueda de la cuenta y la orden del usuario final en Lupe.

Recibe la página de `SesionLupe` (perfil persistente y candado): no abre navegador.
Cada búsqueda entra por /dashboard, así que no importa en qué pantalla dejó Lupe el paso RT.
Los id de las grabaciones no se dejan fijos: la fila se elige por el id de la petición
y los números se leen del texto en pantalla.
"""

import re
import time
from datetime import date

from consultar_lupe import (
    _correo_limpio,
    _correo_ofuscado,
    _nombres_cuadran,
    _sin_tildes,
)
from entes.telefono import telefono_colombia
from lupe_navegador import URL_LUPE, aceptar_terminos, asegurar_sesion
from utils.logger import obtener_log

log = obtener_log("entes")

_ID = re.compile(r"\d{5,}")
_ID_CUENTA = re.compile(r"ID:\s*(\d{5,})")
_VALOR = re.compile(r"\$\s*([\d.,]+)")
_GENERICAS = {"plaza", "tienda", "centro", "local", "rappi", "pedido", "orden", "ordenes"}
_MESES = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}


def consultar_cuenta(
    page,
    correo="",
    telefono="",
    id_orden="",
    nombre_comercio="",
    fecha_orden="",
    nombre_peticionario="",
    abrir_ordenes=True,
    caso_log=None,
):
    """Busca la cuenta. Si abrir_ordenes es falso, para al confirmarla."""
    clog = caso_log or log
    correo = (correo or "").strip()
    telefono_busqueda = telefono_colombia(telefono)[0]
    id_orden = re.sub(r"\D", "", id_orden or "")
    if not correo and not telefono_busqueda:
        raise ValueError("Hace falta correo o teléfono para buscar la cuenta.")

    inicio = time.monotonic()
    _entrar_lupe(page)
    if correo:
        _buscar(page, correo, "correo")
    else:
        _buscar(page, telefono_busqueda, "telefono")
    if not _cuenta_encontrada(page):
        clog.info("Lupe: sin cuenta para la búsqueda")
        return _vacio("sin_cuenta")
    perfil = _leer_perfil(page, correo, telefono_busqueda)
    segundos = time.monotonic() - inicio
    if not _cuenta_valida(correo, telefono_busqueda, nombre_peticionario, perfil):
        clog.info(
            "Lupe: cuenta descartada, la ficha no coincide con la petición (%.1f s)", segundos
        )
        return _vacio("sin_info")
    id_usuario = _leer_id_cuenta(page)
    clog.info("Lupe: cuenta validada, id usuario %s (%.1f s)", id_usuario or "?", segundos)
    if not abrir_ordenes:
        return _con_usuario(_vacio("cuenta"), id_usuario)
    _abrir_ordenes(page)
    return _con_usuario(
        _resolver_orden(page, id_orden, nombre_comercio, fecha_orden),
        id_usuario,
    )


def _entrar_lupe(page):
    """Entra al tablero de usuarios. Si la sesión venció, la renueva con el Sprint 1."""
    destino = f"{URL_LUPE.rstrip('/')}/dashboard"
    page.goto(destino, wait_until="domcontentloaded")
    aceptar_terminos(page, 3000)
    if _tablero_listo(page, 8000):
        return
    asegurar_sesion(page, paso="entes")
    page.goto(destino, wait_until="domcontentloaded")
    aceptar_terminos(page, 3000)
    if not _tablero_listo(page, 15000):
        raise RuntimeError("Lupe no mostró el buscador de usuarios tras renovar la sesión.")


def _tablero_listo(page, espera=8000):
    try:
        page.get_by_role("searchbox", name="search").wait_for(state="visible", timeout=espera)
        return True
    except Exception:
        if aceptar_terminos(page):
            try:
                page.get_by_role("searchbox", name="search").wait_for(
                    state="visible", timeout=espera
                )
                return True
            except Exception:
                pass
        return False


def _buscar(page, texto, filtro):
    if filtro == "telefono":
        page.locator("div.mat-menu-trigger.search-types.middle.pointer").click()
        page.get_by_role("menuitem", name="Teléfono", exact=True).click()
    caja = page.get_by_role("searchbox", name="search")
    caja.click()
    caja.fill(texto)
    page.get_by_text("Buscar", exact=True).click()
    aceptar_terminos(page)


def _boton_ordenes(page):
    candidato = page.get_by_role("button", name="Órdenes Órdenes")
    if candidato.count() > 0:
        return candidato.first
    return page.get_by_role("button", name=re.compile(r"Órdenes")).first


def _coincidencias(correo_peticion, telefono_peticion, nombre_peticion, perfil):
    correo_peticion = _correo_limpio(correo_peticion)
    correo_lupe = _correo_limpio(perfil.get("correo"))
    telefono_peticion = telefono_colombia(telefono_peticion)[0]
    telefono_lupe = telefono_colombia(perfil.get("telefono"))[0]
    correo_ok = bool(
        correo_peticion
        and "@" in correo_peticion
        and correo_peticion == correo_lupe
        and not _correo_ofuscado(correo_lupe)
    )
    telefono_ok = bool(
        telefono_peticion and telefono_lupe and telefono_peticion == telefono_lupe
    )
    nombre_ok = _nombres_cuadran(nombre_peticion, perfil.get("nombre"))
    return correo_ok, telefono_ok, nombre_ok


def _cuenta_valida(correo_peticion, telefono_peticion, nombre_peticion, perfil):
    """Acepta la ficha hallada por el identificador propio del peticionario.

    En Entes la búsqueda se hace por el correo o el teléfono colombiano del
    peticionario. Si Lupe devolvió una ficha para ese dato exacto, la identidad
    queda confirmada: ese correo/teléfono está registrado en esa cuenta. Un correo
    o teléfono que no exista en Lupe no abre ficha (va a pedir información), así que
    no hay riesgo de asociar la cuenta de otra persona.

    El nombre proviene del «De/From» del reenvío de Gmail y puede ser un apodo o
    traer variantes ortográficas (p. ej. Jesenia vs Jessenia); por eso es solo
    informativo y nunca descarta una cuenta encontrada por correo o teléfono.
    """
    correo_ok, telefono_ok, _nombre_ok = _coincidencias(
        correo_peticion, telefono_peticion, nombre_peticion, perfil
    )
    return correo_ok or telefono_ok


def _campo_ficha(page, etiqueta):
    """En la ficha, cada dato es «span.field» con la etiqueta en «span.bold»."""
    return page.locator("span.field").filter(
        has=page.locator("span.bold", has_text=re.compile(rf"^\s*{etiqueta}:"))
    ).first


def _valor_campo(campo, etiqueta):
    texto = campo.inner_text(timeout=3000)
    texto = re.sub(rf"^\s*{etiqueta}:\s*", "", texto)
    return texto.replace("visibility", "").replace("phone", "").split("\n")[0].strip()


def _leer_perfil(page, correo, telefono):
    """Lee el nombre de la ficha y conserva el identificador con que se buscó.

    La búsqueda se hace por correo si hay, y si no por teléfono: ese dato ya
    coincide con la cuenta encontrada, así que basta con guardarlo. El nombre es
    informativo (Sheet y textos); no se revela el teléfono oculto solo para
    cuadrar un nombre, porque el identificador buscado ya confirma la identidad.
    """
    aceptar_terminos(page)
    nombre = ""
    try:
        nombre = _valor_campo(_campo_ficha(page, "Usuario"), "Usuario")
    except Exception:
        pass
    perfil = {"nombre": nombre, "correo": "", "telefono": ""}
    if correo:
        perfil["correo"] = _correo_limpio(correo)
    else:
        perfil["telefono"] = telefono
    return perfil


def _cuenta_encontrada(page):
    aceptar_terminos(page)
    boton = _boton_ordenes(page)
    try:
        boton.wait_for(state="visible", timeout=12000)
        return True
    except Exception:
        return False


def _leer_id_cuenta(page):
    """En la ficha de la cuenta, antes de Órdenes, el texto es «ID:» y el número."""
    aceptar_terminos(page)
    marca = page.get_by_text(_ID_CUENTA).first
    try:
        marca.wait_for(state="visible", timeout=8000)
    except Exception:
        return ""
    hallado = _ID_CUENTA.search(marca.inner_text())
    if not hallado:
        return ""
    return hallado.group(1)


def _con_usuario(resultado, id_usuario):
    if id_usuario:
        resultado["id_usuario"] = id_usuario
    return resultado


def _abrir_ordenes(page):
    aceptar_terminos(page)
    _boton_ordenes(page).click(timeout=10000)
    aceptar_terminos(page)


def _filas_ordenes(page):
    """Filas de datos de la tabla #orders. El encabezado es mat-header-row."""
    return page.locator("#orders mat-row")


def _resolver_orden(page, id_orden, nombre_comercio="", fecha_orden=""):
    aceptar_terminos(page)
    filas = _filas_ordenes(page)
    try:
        filas.filter(has_text=_ID).first.wait_for(state="visible", timeout=15000)
    except Exception:
        textos = [filas.nth(i).inner_text() for i in range(filas.count())]
        return _sin_elegir("sin_orden", textos)

    textos = [filas.nth(i).inner_text() for i in range(filas.count())]
    if id_orden:
        indices = [i for i, texto in enumerate(textos) if id_orden in texto and _ID.search(texto)]
    else:
        fecha = _normalizar_fecha(fecha_orden)
        if not fecha or not _palabras_utiles(nombre_comercio):
            return _sin_elegir("sin_orden", textos)
        indices = [
            i
            for i, texto in enumerate(textos)
            if _ID.search(texto) and _fecha_en_fila(fecha, texto) and _tienda_parecida(nombre_comercio, texto)
        ]
    if not indices:
        return _sin_elegir("sin_orden", textos)
    if len(indices) > 1:
        return _sin_elegir("empate", [textos[i] for i in indices])
    filas.nth(indices[0]).click()
    resultado = _leer_orden(page, id_orden)
    resultado["fila"] = _muestra([textos[indices[0]]])[0]
    return resultado


def _sin_elegir(rama, textos):
    resultado = _vacio(rama)
    resultado["filas"] = _muestra(textos)
    return resultado


def _muestra(textos):
    vistos = []
    for texto in textos:
        limpio = " ".join(str(texto or "").split())
        if limpio and limpio not in vistos:
            vistos.append(limpio[:180])
        if len(vistos) == 8:
            break
    return vistos


def _normalizar_fecha(fecha):
    texto = _sin_tildes(fecha).lower()
    if not texto:
        return ""
    numerica = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", texto)
    if numerica:
        dia, mes, anio = numerica.groups()
        return f"{int(dia):02d}/{int(mes):02d}/{anio}"
    invertida = re.search(r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", texto)
    if invertida:
        anio, mes, dia = invertida.groups()
        return f"{int(dia):02d}/{int(mes):02d}/{anio}"
    escrita = re.search(r"(\d{1,2})\s+(?:de\s+)?([a-z]+)(?:\s+(?:de\s+)?(\d{4}))?", texto)
    if not escrita or escrita.group(2) not in _MESES:
        return ""
    anio = escrita.group(3) or str(date.today().year)
    return f"{int(escrita.group(1)):02d}/{_MESES[escrita.group(2)]}/{anio}"


def _fecha_en_fila(fecha, texto):
    plano = texto or ""
    return fecha in plano or fecha.replace("/", "-") in plano


def _palabras_utiles(nombre):
    plano = _sin_tildes(nombre).lower()
    return [
        palabra
        for palabra in re.findall(r"[a-z0-9]+", plano)
        if palabra not in _GENERICAS and (len(palabra) >= 3 or palabra.isdigit())
    ]


def _tienda_parecida(nombre, texto):
    plano = _sin_tildes(texto).lower()
    return any(palabra in plano for palabra in _palabras_utiles(nombre))


def _leer_orden(page, id_orden):
    page.locator("info-cards").first.wait_for(state="visible", timeout=8000)
    tarjetas = page.locator("info-cards").first.inner_text()
    leido = _id_mas_largo(tarjetas)
    id_pedido = id_orden or leido
    valor = _valor(tarjetas)
    usuario = _leer_usuario(page, id_pedido)
    if not id_pedido or not valor:
        resultado = _vacio("sin_orden")
        resultado["lectura"] = {
            "id_pedido": id_pedido,
            "id_usuario": usuario,
            "valor_orden": valor,
        }
        return resultado
    return {
        "id_orden": id_pedido,
        "id_usuario": usuario or "0",
        "valor_orden": valor,
        "rama": "ok",
    }


def _leer_usuario(page, id_pedido):
    """Tras abrir la orden, la ficha «Usuario» muestra «ID Usuario:» y el número."""
    fichas = page.locator("div").filter(has_text=re.compile(r"^Usuario$"))
    if fichas.count() == 0:
        return ""
    fichas.last.dispatch_event("click")
    try:
        page.get_by_text(re.compile(r"ID\s*Usuario")).first.wait_for(
            state="visible", timeout=8000
        )
    except Exception:
        return ""
    texto = page.locator("info-cards").filter(has_text=re.compile(r"ID\s*Usuario")).first.inner_text()
    hallado = re.search(r"ID\s*Usuario:?\s*(\d+)", texto, re.IGNORECASE)
    if hallado and hallado.group(1) != id_pedido:
        return hallado.group(1)
    return ""


def _id_mas_largo(texto):
    numeros = _ID.findall(texto or "")
    if not numeros:
        return ""
    return max(numeros, key=len)


def _valor(texto):
    hallado = _VALOR.search(texto or "")
    if not hallado:
        return ""
    return hallado.group(1).replace(".", "").replace(",", "")


def _vacio(rama):
    return {
        "id_orden": "0",
        "id_usuario": "0",
        "valor_orden": "0",
        "rama": rama,
    }
