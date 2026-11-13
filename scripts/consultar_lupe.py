import re
import time
import unicodedata
from enviar_slack import enviar_slack
import storage
from enviar_correo import (
    enviar_cuenta_habilitada,
    enviar_correo,
    enviar_correo_no_encontrado,
)
from config import CONTROL_CALIDAD
from flow_control import DetenerFlujo, comprobar_detener
from lupe_navegador import SesionLupe, URL_LUPE, aceptar_terminos as _aceptar_terminos, asegurar_sesion
from utils.exceptions import LupeOcupado, LupeSesionRequerida
from utils.logger import log_caso, obtener_log

log = obtener_log()

# Textos del menu mat-menu-item (role=menuitem). Lupe es-ES usa "Teléfono", no "Número de teléfono".
_FILTROS_BUSQUEDA = {
    "ID": ["ID"],
    "telefono": ["Teléfono", "Phone"],
    "documento": ["Identificación", "Identification"],
    "correo": ["Correo", "Email"],
}


def _abrir_menu_tipo_busqueda(page):
    page.locator("div.mat-menu-trigger.search-types.middle.pointer").click()
    page.wait_for_timeout(400)


def _click_filtro_busqueda(page, clave_filtro):
    """Elige ID, telefono, documento o correo probando variantes del menu."""
    etiquetas = _FILTROS_BUSQUEDA.get(clave_filtro, [clave_filtro])
    _abrir_menu_tipo_busqueda(page)
    ultimo_error = None
    for etiqueta in etiquetas:
        try:
            page.get_by_role("menuitem", name=etiqueta, exact=True).click(timeout=5000)
            return etiqueta
        except Exception as error:
            ultimo_error = error
            _abrir_menu_tipo_busqueda(page)
    raise ultimo_error or TimeoutError(f"Filtro no encontrado: {etiquetas}")


_SELECTOR_TARJETA = "div.row.information-card.white"
_SELECTOR_FOTO = "img.circle.responsive-img.ng-star-inserted"
_RECORTE_TARJETA = {"width": 450, "height": 270}
# Esquina de la tarjeta en «Información general» con la ventana de 1280x720 (medida en Lupe).
_POSICION_TARJETA = {"x": 96.25, "y": 209.5}


def _abrir_informacion_general(page):
    """Lupe reabre el perfil en la última sección vista (p. ej. Reportes), donde no está la tarjeta."""
    try:
        icono = page.locator("img[alt='Información general']").first
        if icono.is_visible():
            icono.click()
    except Exception:
        pass
    # El tooltip del icono tapa la esquina de la tarjeta.
    page.mouse.move(700, 700)


def _capturar_perfil(page, ruta_caso, aviso="Captura del perfil tomada", permitir_sin_tarjeta=False):
    _abrir_informacion_general(page)
    time.sleep(3)
    try:
        page.wait_for_selector(_SELECTOR_FOTO, timeout=15000)
        tarjeta = page.locator(_SELECTOR_TARJETA).first
        tarjeta.wait_for(state="visible", timeout=10000)
        box = tarjeta.bounding_box()
    except Exception:
        box = None
    if box:
        origen = {"x": box["x"], "y": box["y"]}
    elif permitir_sin_tarjeta:
        log_caso(log, ruta_caso).warning(
            "La tarjeta del perfil no cargó; se recorta la zona habitual de la tarjeta para que QA la revise"
        )
        origen = _POSICION_TARJETA
    else:
        raise TimeoutError("Lupe no mostró la tarjeta del perfil para la evidencia")
    screenshot_bytes = page.screenshot(clip={**origen, **_RECORTE_TARJETA})
    if not screenshot_bytes:
        raise TimeoutError("Lupe no entregó la captura del perfil")
    if aviso:
        log_caso(log, ruta_caso).info(aviso)

    informacion = storage.leer_caso(ruta_caso)
    informacion["slack"] = informacion.get("slack") or {}
    informacion["slack"]["foto_prueba"] = informacion["slack"].get("foto_prueba") or {}
    informacion["slack"]["foto_prueba"]["aplica"] = True
    storage.guardar_caso(ruta_caso, informacion)

    return screenshot_bytes


def _sin_tildes(texto):
    texto = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _palabras(texto):
    limpio = _sin_tildes(texto).lower()
    return {p for p in re.findall(r"[a-z]+", limpio) if len(p) > 1}


def _nombres_cuadran(carta, lupe):
    de_carta = _palabras(carta)
    de_lupe = _palabras(lupe)
    if not de_carta or not de_lupe:
        return False
    if len(de_carta) <= len(de_lupe):
        return de_carta <= de_lupe
    return de_lupe <= de_carta


_PALABRAS_NOMBRE = {"de", "del", "la", "las", "los", "el", "y"}


def _palabras_nombre(texto):
    limpio = _sin_tildes(texto).lower()
    return {
        p
        for p in re.findall(r"[a-z]+", limpio)
        if len(p) > 2 and p not in _PALABRAS_NOMBRE
    }


def _nombres_coinciden(a, b):
    """True si comparten al menos una palabra. No exige el nombre completo."""
    de_a = _palabras_nombre(a)
    de_b = _palabras_nombre(b)
    if not de_a or not de_b:
        return False
    return bool(de_a & de_b)


def _nombre_para_lupe(datos):
    nombre_rt = str(datos.get("nombre_rt") or "").strip()
    if nombre_rt:
        return nombre_rt
    return datos.get("nombre_peticionario")


def _digitos(texto):
    return re.sub(r"\D", "", str(texto or ""))


def _correo_limpio(texto):
    return str(texto or "").strip().lower()


def _correo_ofuscado(texto):
    correo = _correo_limpio(texto)
    return "xxxx" in correo or "@xxx" in correo


def _correo_busqueda(datos):
    """Soy Rappi del PDF si está; si no, el correo de quien envió el Gmail."""
    correo_rt = _correo_limpio(datos.get("correo_rt"))
    if "@" in correo_rt:
        return correo_rt
    return _correo_limpio(datos.get("correo_peticionario"))


def _candidatos(datos):
    lista = []
    if datos.get("id_rt"):
        lista.append((str(datos["id_rt"]).strip(), "ID"))
    documento = _digitos(datos.get("numero_documento"))
    if documento:
        lista.append((documento, "documento"))
    correo = _correo_busqueda(datos)
    if "@" in correo:
        lista.append((correo, "correo"))
    if datos.get("telefono"):
        lista.append((str(datos["telefono"]).strip(), "telefono"))
    return lista


def _cedula_igual(datos, perfil):
    cedula_peticion = _digitos(datos.get("numero_documento"))
    cedula_lupe = _digitos(perfil.get("documento"))
    return bool(cedula_peticion and cedula_lupe and cedula_peticion == cedula_lupe)


def _cedula_contradice(datos, perfil):
    cedula_peticion = _digitos(datos.get("numero_documento"))
    cedula_lupe = _digitos(perfil.get("documento"))
    return bool(cedula_peticion and cedula_lupe and cedula_peticion != cedula_lupe)


def _nombre_ok(datos, perfil):
    return _nombres_coinciden(_nombre_para_lupe(datos), perfil.get("nombre"))


def _cuenta_valida(datos, perfil, clave):
    """Acepta la ficha con los datos del RT del escrito, no con el apodo de Gmail.

    Cédula o Soy Rappi del cuerpo identifican al RT si el nombre coincide (o no
    hay nombre_rt). El correo del From solo vale si el nombre coincide y la
    cédula del escrito no contradice la ficha. El teléfono no valida solo. Un
    ID que abre a otra persona no pasa esta validación: _buscar_cuenta lo
    devuelve como tercero, salvo que el escrito ya sea rt_rl.
    """
    if clave == "telefono":
        return False
    correo_lupe = _correo_limpio(perfil.get("correo"))
    correo_rt = _correo_limpio(datos.get("correo_rt"))
    correo_from = _correo_limpio(datos.get("correo_peticionario"))
    hay_nombre_rt = bool(str(datos.get("nombre_rt") or "").strip())
    nombre_ok = _nombre_ok(datos, perfil)
    nombre_listo = nombre_ok or not hay_nombre_rt
    ofuscado = _correo_ofuscado(correo_lupe)
    correo_rt_ok = (
        "@" in correo_rt
        and correo_rt == correo_lupe
        and not ofuscado
        and nombre_listo
    )
    cedula_ok = _cedula_igual(datos, perfil) and nombre_listo
    id_ok = clave == "ID" and nombre_ok
    from_ok = (
        clave == "correo"
        and "@" in correo_from
        and correo_from == correo_lupe
        and correo_rt != correo_lupe
        and not ofuscado
        and nombre_ok
        and not _cedula_contradice(datos, perfil)
    )
    return correo_rt_ok or cedula_ok or id_ok or from_ok


def _motivo_descarte(datos, perfil, clave):
    if clave == "telefono":
        return "teléfono no valida por sí solo"
    if _cedula_contradice(datos, perfil) and clave != "ID":
        return "cédula"
    if clave == "correo" and _correo_ofuscado(perfil.get("correo")):
        return "correo ofuscado"
    if not _nombre_ok(datos, perfil):
        return "nombre"
    return "sin identificador coincidente"


def _texto_campo(page, etiqueta):
    return (
        page.locator("#information")
        .locator("label", has_text=etiqueta)
        .locator("..")
        .locator("span")
        .first.inner_text()
    )


def _leer_perfil(page):
    _aceptar_terminos(page)
    page.wait_for_selector("img.circle.responsive-img.ng-star-inserted")
    estado = page.locator("sk-general-information span.ng-star-inserted").first.inner_text()
    nombre = page.locator("div.name").first.inner_text().split("\n")[0].strip()
    page.get_by_role("button", name="Correo").click()
    page.wait_for_timeout(1000)
    correo = _texto_campo(page, "Correo")
    if _correo_ofuscado(correo):
        page.wait_for_timeout(1000)
        correo = _texto_campo(page, "Correo")
    documento = ""
    try:
        documento = _texto_campo(page, "Identificación")
    except Exception:
        documento = ""
    return {
        "estado": estado.strip(),
        "nombre": nombre,
        "correo": correo.strip(),
        "documento": documento.strip(),
    }


def _aplicar_tipo_caso(informacion, perfil, correo_lupe):
    ya_es_rl = informacion["correo"].get("tipo_representacion", "").lower() == "rt_rl"
    if perfil.get("tercero_por_id") and not ya_es_rl:
        informacion["correo"]["tipo_representacion"] = "tercero"
        informacion["tipo_caso"] = 2
        return 2
    tipo_caso = _detectar_tipo_caso(informacion)
    informacion["tipo_caso"] = tipo_caso
    return tipo_caso


_NUL = re.compile(r"\bnul\d+\b", re.I)


def _perfil_anonimizado(perfil):
    """Cuentas eliminadas en Lupe: nombre o documento quedan Nul + dígitos."""
    for campo in ("nombre", "documento"):
        if _NUL.search(str(perfil.get(campo) or "")):
            return True
    return False


def _ruta_despues_lupe(perfil, tipo_caso=1):
    """Qué hacer con el perfil abierto: atajo habilitado, carta de terceros o Slack."""
    if _perfil_anonimizado(perfil):
        return "revision_manual"
    if perfil.get("tercero_por_id") and tipo_caso != 3:
        return "terceros"
    if perfil.get("estado") == "Habilitado":
        return "habilitado"
    return "slack"


def _detectar_tipo_caso(informacion):
    """Tipo 3 (RT + RL) o 1 (el propio RT).

    El tipo 2 solo lo marca tercero_por_id en _aplicar_tipo_caso. Un Gmail
    distinto no vuelve el caso rt_rl ni tercero: el RT que pide en primera
    persona sigue tipo 1. Cédula, correo o teléfono que abren a otra persona
    se descartan antes y no llegan aquí.
    """
    tipo_representacion = informacion["correo"].get("tipo_representacion", "").lower()
    if tipo_representacion == "rt_rl":
        return 3
    return 1


def manejar_sesion(page, context=None):
    """Deja la página dentro de Lupe (reingreso con Google si hace falta)."""
    asegurar_sesion(page)


def verificar_y_capturar_habilitado(ruta_caso, page=None):
    """Abre el ID ya escalado a OPS y comprueba si la cuenta quedó Habilitada.

    Con `page` reutiliza el navegador del lote; sin ella abre su propia SesionLupe.
    """
    comprobar_detener()
    informacion = storage.leer_caso(ruta_caso)
    id_rt = str((informacion.get("lupe") or {}).get("id") or "").strip()
    if not id_rt:
        log_caso(log, ruta_caso).warning("El caso no tiene ID de Lupe para verificar si quedó Habilitado")
        return None, None

    if page is None:
        with SesionLupe(paso="slack") as lupe:
            return _verificar_habilitado(ruta_caso, id_rt, lupe.pagina())
    return _verificar_habilitado(ruta_caso, id_rt, page)


def _campo_cabecera(page, etiqueta):
    return page.locator("span.field").filter(
        has=page.locator("span.bold", has_text=re.compile(rf"^\s*{re.escape(etiqueta)}\s*:", re.I))
    ).first


_ESTADOS = {e.lower(): e for e in ("Habilitado", "Inhabilitado", "Bloqueado", "Suspendido")}


def _leer_estado_global(page):
    campo = _campo_cabecera(page, "Estado")
    campo.wait_for(state="visible", timeout=10000)
    texto = re.sub(r"^\s*Estado\s*:\s*", "", campo.inner_text(timeout=10000), flags=re.I).strip()
    if texto.lower() not in _ESTADOS:
        raise TimeoutError("Lupe no confirmó el estado global de la cuenta")
    return _ESTADOS[texto.lower()]


def _campo_tarjeta(page, etiqueta):
    rotulo = page.locator("label", has_text=re.compile(rf"^\s*{re.escape(etiqueta)}\s*$", re.I))
    return page.locator("#information li").filter(has=rotulo).filter(visible=True).first


def _leer_estado_tarjeta(page, id_rt):
    """Estado de «Información general». Lupe puede mostrar otro distinto en la cabecera."""
    campo_id = _campo_tarjeta(page, "Id")
    campo_id.wait_for(state="visible", timeout=15000)
    if campo_id.locator("span").first.inner_text(timeout=10000).strip() != str(id_rt):
        raise TimeoutError("La tarjeta de Lupe todavía muestra otro perfil")
    texto = _campo_tarjeta(page, "Estado").locator("span").first.inner_text(timeout=10000).strip()
    if texto.lower() not in _ESTADOS:
        raise TimeoutError("Lupe no confirmó el estado de la tarjeta")
    return _ESTADOS[texto.lower()]


def _verificar_habilitado(ruta_caso, id_rt, page):
    try:
        page.goto(URL_LUPE)
        manejar_sesion(page)
        page.locator("img.animated.jello").click()
        page.get_by_text("Rappitendero").click()
        page.get_by_text("Perfiles").click()
        _aceptar_terminos(page)

        page.locator("#search").click()
        page.locator("#search").fill(id_rt)
        _click_filtro_busqueda(page, "ID")
        page.wait_for_timeout(2000)
        _aceptar_terminos(page)
        if page.locator("lupe-help-message").is_visible():
            log_caso(log, ruta_caso).warning("Lupe no abrió el ID %s", id_rt)
            return None, None

        estado = _leer_estado_global(page)
        id_visible = _campo_cabecera(page, "ID").inner_text(timeout=10000)
        if not re.fullmatch(rf"\s*ID\s*:\s*{re.escape(str(id_rt))}\s*", id_visible, re.I):
            raise TimeoutError("Lupe no confirmó el ID de la cuenta consultada")
        nombre_campo = page.locator("div.name").first
        if nombre_campo.is_visible():
            nombre = nombre_campo.inner_text().split("\n")[0].strip()
        else:
            nombre = re.sub(
                r"^\s*Rt\s*:\s*", "", _campo_cabecera(page, "Rt").inner_text(timeout=10000), flags=re.I
            ).strip()
        if not nombre:
            raise TimeoutError("Lupe no confirmó el nombre de la cuenta consultada")
        if _perfil_anonimizado({"nombre": nombre, "documento": ""}):
            log_caso(log, ruta_caso).warning(
                "Lupe: perfil anonimizado (%s); no se trata como Habilitada", nombre
            )
            return False, None

        if estado != "Habilitado":
            return False, None

        _abrir_informacion_general(page)
        try:
            estado_tarjeta = _leer_estado_tarjeta(page, id_rt)
        except Exception as error:
            log_caso(log, ruta_caso).warning(
                "Lupe: la cabecera dice Habilitado pero no se pudo leer la tarjeta (%s); sigue pendiente",
                error,
            )
            return None, None
        if estado_tarjeta != "Habilitado":
            log_caso(log, ruta_caso).warning(
                "Lupe: cabecera Habilitado, tarjeta %s; no se responde como habilitada", estado_tarjeta
            )
            return False, None

        foto_bytes = _capturar_perfil(
            page,
            ruta_caso,
            aviso="Cuenta Habilitada tras OPS: captura tomada",
            permitir_sin_tarjeta=True,
        )
        informacion = storage.leer_caso(ruta_caso)
        informacion["lupe"] = informacion.get("lupe") or {}
        informacion["lupe"]["estado"] = estado
        storage.guardar_caso(ruta_caso, informacion)
        return True, foto_bytes
    except (DetenerFlujo, LupeSesionRequerida, LupeOcupado):
        raise
    except Exception:
        log_caso(log, ruta_caso).exception("Error verificando el ID %s en Lupe", id_rt)
        return None, None



def _buscar_cuenta(page, datos_correo):
    for cuenta, clave_filtro in _candidatos(datos_correo):
        comprobar_detener("Parada durante búsqueda en Lupe (caso actual no guardado).")
        page.locator("#search").click()
        page.locator("#search").fill(cuenta)
        try:
            _click_filtro_busqueda(page, clave_filtro)
        except Exception as error:
            log.warning("Lupe: no se pudo usar el filtro %s: %s", clave_filtro, error)
            continue
        page.wait_for_timeout(2000)
        _aceptar_terminos(page)
        if page.locator("lupe-help-message").is_visible():
            log.info("Lupe: sin ficha (%s) %s", clave_filtro, cuenta)
            continue
        perfil = _leer_perfil(page)
        if _perfil_anonimizado(perfil):
            return perfil, clave_filtro
        if _cuenta_valida(datos_correo, perfil, clave_filtro):
            return perfil, clave_filtro
        if clave_filtro == "ID":
            es_rl = str(datos_correo.get("tipo_representacion") or "").lower() == "rt_rl"
            if not es_rl:
                perfil["tercero_por_id"] = True
            log.info(
                "Lupe: el ID abre a otra persona (%s)",
                "rt_rl, se conserva la ficha" if es_rl else "queda como tercero",
            )
            return perfil, clave_filtro
        log.info(
            "Lupe: cuenta descartada (%s), no coincide con la petición (%s)",
            clave_filtro,
            _motivo_descarte(datos_correo, perfil, clave_filtro),
        )

    return None, None


def _dejar_sin_correo_peticionario(ruta_caso, informacion, clog, motivo):
    """No hay correo de quien escribió: revisión manual, sin respuesta."""
    informacion.setdefault("slack", {})["estado"] = "caso_manual"
    storage.guardar_caso(ruta_caso, informacion)
    clog.warning(
        "Resultado: sin correo del peticionario (%s); a revisión manual",
        motivo,
    )
    return "revision_manual"


def consultar_lupe(ruta_caso, page=None):
    """Busca el caso en Rappitendero > Perfiles y decide el siguiente paso.

    Con `page` reutiliza el navegador del lote; sin ella abre su propia SesionLupe.
    """
    if page is None:
        with SesionLupe(paso="rt") as lupe:
            return _consultar_caso(ruta_caso, lupe.pagina())
    return _consultar_caso(ruta_caso, page)


def _consultar_caso(ruta_caso, page):
    comprobar_detener()
    clog = log_caso(log, ruta_caso)
    informacion = storage.leer_caso(ruta_caso)
    candidatos = _candidatos(informacion.get("correo", {}))
    dato_busqueda = candidatos[0][0] if candidatos else "no disponible"
    clog.info("Lupe: buscando %s", dato_busqueda)

    page.goto(URL_LUPE)
    manejar_sesion(page)

    page.locator("img.animated.jello").click()
    page.get_by_text("Rappitendero").click()
    page.get_by_text("Perfiles").click()
    _aceptar_terminos(page)

    perfil, _clave = _buscar_cuenta(page, informacion["correo"])
    if not perfil:
        if storage.falta_correo_peticionario(informacion["correo"].get("correo_peticionario")):
            return _dejar_sin_correo_peticionario(
                ruta_caso, informacion, clog, "sin cuenta"
            )
        enviado = enviar_correo_no_encontrado(ruta_caso, dato_busqueda)
        informacion["cuenta_no_encontrada"] = True
        informacion["completado"] = enviado
        storage.guardar_caso(ruta_caso, informacion)
        if enviado:
            clog.warning("Resultado: sin cuenta en Lupe; se pidió información al peticionario")
        else:
            clog.error("Resultado: sin cuenta en Lupe y no se pudo pedir información")
        return None

    if _perfil_anonimizado(perfil):
        nombre = perfil["nombre"]
        estado = perfil["estado"]
        informacion["lupe"]["nombre"] = nombre
        informacion["lupe"]["estado"] = estado
        informacion["lupe"]["correo"] = perfil.get("correo", "")
        informacion["lupe"]["documento"] = perfil.get("documento", "")
        informacion["lupe"]["id"] = informacion["correo"].get("id_rt", "")
        informacion["lupe"]["regla_bloqueo"] = ""
        informacion["slack"]["estado"] = "caso_manual"
        storage.guardar_caso(ruta_caso, informacion)
        clog.warning(
            "Resultado: cuenta anonimizada en Lupe (%s | %s); a revisión manual",
            nombre,
            estado,
        )
        return "revision_manual"

    nombre = perfil["nombre"]
    estado = perfil["estado"]
    correo_lupe = perfil["correo"]
    tipo_caso = _aplicar_tipo_caso(informacion, perfil, correo_lupe)
    clog.info("Lupe: perfil %s | %s | tipo de caso %s", nombre, estado, tipo_caso)
    ruta = _ruta_despues_lupe(perfil, tipo_caso)

    if ruta == "habilitado":
        if storage.falta_correo_peticionario(informacion["correo"].get("correo_peticionario")):
            informacion["lupe"]["nombre"] = nombre
            informacion["lupe"]["estado"] = estado
            informacion["lupe"]["correo"] = correo_lupe
            informacion["lupe"]["documento"] = perfil["documento"]
            informacion["lupe"]["id"] = informacion["correo"].get("id_rt", "")
            informacion["lupe"]["regla_bloqueo"] = ""
            return _dejar_sin_correo_peticionario(
                ruta_caso, informacion, clog, "cuenta ya habilitada"
            )
        foto_bytes = _capturar_perfil(page, ruta_caso)

        informacion = storage.leer_caso(ruta_caso)
        informacion["lupe"]["nombre"] = nombre
        informacion["lupe"]["estado"] = estado
        informacion["lupe"]["correo"] = correo_lupe
        informacion["lupe"]["documento"] = perfil["documento"]
        informacion["lupe"]["id"] = informacion["correo"].get("id_rt", "")
        informacion["lupe"]["regla_bloqueo"] = ""
        _aplicar_tipo_caso(informacion, perfil, correo_lupe)
        storage.guardar_caso(ruta_caso, informacion)

        enviado = enviar_cuenta_habilitada(
            ruta_caso,
            foto_bytes,
            crear_borrador=CONTROL_CALIDAD,
        )
        if enviado:
            informacion = storage.leer_caso(ruta_caso)
            informacion["completado"] = True
            storage.guardar_caso(ruta_caso, informacion)
            clog.info(
                "Resultado: completado, cuenta ya Habilitada (%s)",
                "borrador" if CONTROL_CALIDAD else "enviado",
            )
        else:
            clog.error("Resultado: cuenta Habilitada, pero no se pudo responder el correo")
        return

    if ruta == "terceros":
        informacion["correo"]["tipo_representacion"] = "tercero"
        informacion["tipo_caso"] = 2
        if storage.falta_correo_peticionario(informacion["correo"].get("correo_peticionario")):
            return _dejar_sin_correo_peticionario(
                ruta_caso, informacion, clog, "tercero"
            )
        storage.guardar_caso(ruta_caso, informacion)
        from generar_borrador import generar_borrador

        generar_borrador(ruta_caso, formato="terceros")
        enviado = enviar_correo(ruta_caso, crear_borrador=CONTROL_CALIDAD)
        if enviado:
            informacion = storage.leer_caso(ruta_caso)
            informacion["completado"] = True
            storage.guardar_caso(ruta_caso, informacion)
            clog.info("Resultado: completado, el ID es de otra persona (respuesta de terceros)")
        else:
            clog.error("Resultado: respuesta de terceros sin enviar")
        return

    # pestaña reports
    _aceptar_terminos(page)
    page.locator("img[alt='Reportes']").click()

    id_valor = page.locator("span.bold", has_text="ID:").evaluate("el => el.nextSibling.textContent")
    regla_valor = page.locator("td.mat-column-rule").first.inner_text()

    informacion["lupe"]["id"] = id_valor.strip()
    informacion["lupe"]["regla_bloqueo"] = regla_valor.strip()
    informacion["lupe"]["estado"] = estado
    informacion["lupe"]["nombre"] = nombre
    informacion["lupe"]["correo"] = correo_lupe
    informacion["lupe"]["documento"] = perfil["documento"]
    informacion["tipo_caso"] = tipo_caso

    storage.guardar_caso(ruta_caso, informacion)

    enviar_slack(ruta_caso)
    clog.info("Resultado: a Slack para decisión de OPS (regla %s)", regla_valor.strip())
