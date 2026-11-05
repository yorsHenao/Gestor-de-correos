"""Casillas del formulario Entes, en el orden de la grabación, y su envío.

El correo se imprime a PDF tal como llegó; no se redacta otro documento.
`enviar_formulario` nunca deja ventanas esperando: devuelve ok, fallo o incierto.
"""

import html
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from config import BASE_DIR, ENTES_TIMEOUT_FORM, URL_FORMULARIO

MAX_ARCHIVOS = 10
MAX_BYTES = 50 * 1024 * 1024
DIAS_LIMITE = 9

PAIS = "co"
CLASE_ACTO = "form_entes.derecho_de_peticion"
MEDIO_DEVOLUCION = "Reversion/Reembolso"
LLAMAR_CLIENTE = "Si"
TEXTO_OK = "Registro creado exitosamente"
# Los asteriscos de las etiquetas son solo text-red-500; los errores de campo llevan text-xs.
ERRORES_CAMPO = "span.text-red-500.text-xs"
CAPTURAS = BASE_DIR / "logs" / "entes_formulario"

ENVIO_OK = "ok"
ENVIO_FALLO = "fallo"
ENVIO_INCIERTO = "incierto"


def armar_formulario(datos, lupe, pdfs, correo_agente):
    """Valores de cada casilla.

    Sin información de la cuenta (rama sin_info) el formulario se envía igual:
    ID Orden e ID Usuario en 0 y el valor que diga el correo, o 0.
    """
    lupe = lupe or {}
    sin_info = lupe.get("rama") == "sin_info"

    correo_agente = (correo_agente or "").strip()
    peticionario = (datos.get("correo_peticionario") or "").strip()
    archivos, omitidos = seleccionar_pdfs(pdfs)
    return {
        "llenar": True,
        "correo_agente": correo_agente,
        "correo_solicitante": correo_agente,
        "radicado": peticionario,
        "pais": PAIS,
        "id_orden": "0" if sin_info else _cero(lupe.get("id_orden")),
        "id_usuario": "0" if sin_info else _cero(lupe.get("id_usuario")),
        "correo_usuario": peticionario,
        "clase_acto": CLASE_ACTO,
        "valor": _cero(datos.get("valor_peticion")) if sin_info else _cero(lupe.get("valor_orden")),
        "fecha_limite": _fecha_limite(datos.get("fecha_llegada_correo")),
        "medio_devolucion": MEDIO_DEVOLUCION,
        "archivos": archivos,
        "archivos_omitidos": omitidos,
        "detalles": (datos.get("resumen") or "").strip(),
        "llamar_cliente": LLAMAR_CLIENTE,
    }


def seleccionar_pdfs(pdfs):
    """Hasta 10 PDF y un total inferior a 50 MB. Lo demás se anota y se deja fuera."""
    elegidos = []
    omitidos = []
    total = 0
    for item in pdfs or []:
        nombre = (item.get("nombre") or "").strip()
        tamano = int(item.get("tamano") or 0)
        if len(elegidos) >= MAX_ARCHIVOS:
            omitidos.append({"nombre": nombre, "motivo": "supera 10 archivos"})
            continue
        if total + tamano >= MAX_BYTES:
            omitidos.append({"nombre": nombre, "motivo": "el total superaría 50 MB"})
            continue
        elegidos.append(
            {
                "nombre": nombre,
                "tamano": tamano,
                "ruta": item.get("ruta") or "",
            }
        )
        total += tamano
    return elegidos, omitidos


def _fecha_limite(fecha_correo):
    texto = str(fecha_correo or "").strip()
    if not texto:
        return ""
    try:
        base = date.fromisoformat(texto[:10])
    except ValueError:
        return ""
    return (base + timedelta(days=DIAS_LIMITE)).isoformat()


def preparar_anexos(playwright, mensaje, datos, carpeta, leer_bytes):
    """Correo impreso, PDF adjunto, o los dos, según dónde esté la petición."""
    ubicacion = (datos.get("ubicacion_peticion") or "cuerpo").strip()
    piezas = []
    destino = Path(carpeta)
    usados = set()
    if ubicacion in ("cuerpo", "ambos"):
        ruta = destino / "correo.pdf"
        imprimir_correo(playwright, mensaje.get("html") or "", mensaje.get("cuerpo") or "", ruta)
        usados.add(ruta.name.lower())
        piezas.append(
            {"nombre": "correo.pdf", "tamano": ruta.stat().st_size, "ruta": str(ruta)}
        )
    if ubicacion in ("adjunto", "ambos"):
        for pdf in mensaje.get("pdfs") or []:
            contenido = leer_bytes(pdf)
            if not contenido:
                continue
            nombre = _nombre_unico(
                _nombre_archivo(pdf.get("nombre") or "adjunto.pdf"), usados
            )
            ruta = destino / nombre
            ruta.write_bytes(contenido)
            piezas.append(
                {"nombre": nombre, "tamano": len(contenido), "ruta": str(ruta)}
            )
    return seleccionar_pdfs(piezas)


def imprimir_correo(playwright, html_correo, texto, destino):
    """Imprime el mensaje tal como llegó. No compone un documento nuevo."""
    contenido = (html_correo or "").strip()
    if not contenido:
        contenido = f"<pre>{html.escape(texto or '')}</pre>"
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.set_content(contenido, wait_until="domcontentloaded", timeout=20000)
        page.pdf(path=str(destino), format="A4", print_background=True)
    finally:
        browser.close()


def _nombre_archivo(nombre):
    limpio = re.sub(r'[<>:"/\\\\|?*]', "_", nombre).strip() or "adjunto.pdf"
    base = limpio[:-4] if limpio.lower().endswith(".pdf") else limpio
    return f"{base[:76].rstrip() or 'adjunto'}.pdf"


def _nombre_unico(nombre, usados):
    base = nombre[:-4]
    candidato = nombre
    numero = 2
    while candidato.lower() in usados:
        sufijo = f"_{numero}"
        candidato = f"{base[:76 - len(sufijo)].rstrip()}{sufijo}.pdf"
        numero += 1
    usados.add(candidato.lower())
    return candidato


def enviar_formulario(playwright, campos, timeout_seg=None):
    """Abre el formulario, lo llena y pulsa Submit. Cierra el navegador siempre.

    - ok: apareció «Registro creado exitosamente».
    - fallo: no se llegó a pulsar Submit, el formulario rechazó un campo o mostró otro
      mensaje. Se puede reintentar sin riesgo de duplicar.
    - incierto: se pulsó Submit y no hubo respuesta en `timeout_seg`.
      No se reintenta, para no enviar dos veces.
    """
    timeout_seg = timeout_seg or ENTES_TIMEOUT_FORM
    browser = playwright.chromium.launch(headless=False)
    try:
        context = browser.new_context(locale="es-ES", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        try:
            page.goto(URL_FORMULARIO, wait_until="domcontentloaded", timeout=60000)
            _llenar_campos(page, campos)
            ya_vistos = set(_mensajes(page))
            page.get_by_role("button", name="Submit").click(timeout=10000)
        except Exception as error:
            return {"estado": ENVIO_FALLO, "detalle": f"No se completó el llenado: {_corto(error)}"}
        return _esperar_confirmacion(page, ya_vistos, timeout_seg)
    finally:
        try:
            browser.close()
        except Exception:
            pass


def _esperar_confirmacion(page, ya_vistos, timeout_seg):
    pasos = max(1, int(timeout_seg * 2))
    for _ in range(pasos):
        if page.is_closed():
            return {"estado": ENVIO_INCIERTO, "detalle": "La ventana se cerró antes de la confirmación"}
        try:
            mensajes = _mensajes(page)
        except Exception:
            page.wait_for_timeout(500)
            continue
        for texto in mensajes:
            if texto in ya_vistos:
                continue
            if TEXTO_OK in texto:
                return {"estado": ENVIO_OK, "detalle": texto}
            return {"estado": ENVIO_FALLO, "detalle": f"El formulario respondió: {texto[:300]}"}
        errores = _errores_campo(page)
        if errores:
            return {
                "estado": ENVIO_FALLO,
                "detalle": f"El formulario rechazó campos: {'; '.join(errores)[:300]}",
            }
        page.wait_for_timeout(500)
    return {
        "estado": ENVIO_INCIERTO,
        "detalle": f"Sin confirmación {timeout_seg} s después de pulsar Submit; "
        f"{_estado_boton(page)}{_captura(page)}",
    }


def _errores_campo(page):
    """Errores de validación bajo cada campo. Con ellos el formulario no envía nada."""
    try:
        textos = page.locator(ERRORES_CAMPO).all_inner_texts()
    except Exception:
        return []
    return [" ".join(texto.split()) for texto in textos if texto.strip()]


def _estado_boton(page):
    try:
        if page.get_by_role("button", name="Submit").is_disabled():
            return "el formulario seguía enviando"
        return "el formulario terminó sin mensaje"
    except Exception:
        return "no se pudo leer el formulario"


def _captura(page):
    try:
        CAPTURAS.mkdir(parents=True, exist_ok=True)
        ruta = CAPTURAS / f"incierto_{datetime.now():%Y%m%d_%H%M%S}.png"
        page.screenshot(path=str(ruta), full_page=True)
        return f" (captura: {ruta})"
    except Exception:
        return ""


def _mensajes(page):
    cajas = page.locator("div.p-4.rounded")
    textos = []
    for indice in range(cajas.count()):
        caja = cajas.nth(indice)
        if not caja.is_visible():
            continue
        texto = " ".join(caja.inner_text().split())
        if texto:
            textos.append(texto)
    return textos


def _llenar_campos(page, campos):
    page.get_by_role("textbox", name="Correo de agente").wait_for(
        state="visible", timeout=20000
    )
    page.get_by_role("textbox", name="Correo de agente").fill(campos["correo_agente"])
    page.get_by_role("textbox", name="Correo de solicitante").fill(
        campos["correo_solicitante"]
    )
    page.get_by_role("textbox", name="Número de Radicado entidad").fill(campos["radicado"])
    page.locator('select[name="country"]').select_option(campos["pais"])
    page.get_by_role("textbox", name="ID Orden").fill(campos["id_orden"])
    page.get_by_role("textbox", name="ID Usuario").fill(campos["id_usuario"])
    page.get_by_role("textbox", name="Correo del usuario").fill(campos["correo_usuario"])
    page.locator('select[name="contactReason"]').select_option(campos["clase_acto"])
    page.get_by_placeholder("Valor a cargar").fill(campos["valor"])
    page.locator('input[name="limitDate"]').fill(campos["fecha_limite"])
    page.locator('select[name="medioDevolucion"]').select_option(campos["medio_devolucion"])
    rutas = [item["ruta"] for item in campos.get("archivos") or [] if item.get("ruta")]
    if rutas:
        page.locator('input[type="file"]').first.set_input_files(rutas)
    page.get_by_role("textbox", name="Detalles de la incidencia").fill(campos["detalles"])
    page.locator('select[name="llamarCliente"]').select_option(campos["llamar_cliente"])


def _corto(error):
    return " ".join(str(error).split())[:300]


def _cero(valor):
    texto = str(valor or "").strip()
    return texto or "0"
