"""Lectura acotada de capturas para completar datos de una orden."""

import json

from config import ENTES_MAX_IMAGENES
from gmail_cuerpo import bytes_parte
from ia_cliente import llamar_gemini_vision
from utils.exceptions import es_infraestructura
from utils.logger import resumir_error

MIN_IMAGEN = 8 * 1024


def completar_con_imagenes(servicio, mensaje, datos, clog):
    """Lee las capturas más grandes y resume los fallos en una sola línea."""
    candidatas = sorted(
        mensaje.get("imagenes") or [],
        key=lambda item: item.get("tamano") or 0,
        reverse=True,
    )
    candidatas = [
        imagen
        for imagen in candidatas
        if not (imagen.get("tamano") or 0) or imagen["tamano"] >= MIN_IMAGEN
    ][:ENTES_MAX_IMAGENES]
    leidas = 0
    errores = []
    for indice, imagen in enumerate(candidatas, 1):
        if _esencial(datos):
            break
        nombre = imagen.get("nombre") or f"imagen sin nombre ({indice})"
        try:
            contenido = bytes_parte(servicio, mensaje["id"], imagen)
            if len(contenido) < MIN_IMAGEN:
                continue
            texto = _leer_imagen(contenido, imagen.get("mime") or "image/jpeg")
            _rellenar_vacios(datos, _parsear_vision(texto))
            leidas += 1
        except Exception as error:
            errores.append((nombre, error))
            if es_infraestructura(error):
                break

    if errores:
        nombre, error = errores[-1]
        clog.warning(
            "Imágenes: %d de %d leídas (%s en %s); se sigue con el cuerpo",
            leidas,
            len(candidatas),
            resumir_error(error),
            nombre,
        )
    elif candidatas:
        clog.info("Imágenes: %d de %d leídas", leidas, len(candidatas))


def _leer_imagen(contenido, mime):
    return llamar_gemini_vision(
        contenido,
        (
            "Mira esta imagen de una petición de la app Rappi. "
            "Puede ser una captura del pedido, un chat o una foto del producto. "
            "Responde SOLO un JSON con esta forma: "
            '{"fecha_orden":"","nombre_comercio":"","id_orden_en_peticion":""}. '
            "fecha_orden es la fecha del pedido si aparece. "
            "nombre_comercio es la tienda si aparece, aunque el nombre no esté completo. "
            "id_orden_en_peticion solo si está escrito un id de orden. "
            "Si la imagen no muestra fecha, tienda ni id, deja los tres vacíos. No inventes."
        ),
        mime_type=mime,
    )


def _parsear_vision(texto):
    limpio = (texto or "").strip()
    if limpio.startswith("```"):
        limpio = limpio.strip("`")
        if limpio.lower().startswith("json"):
            limpio = limpio[4:].strip()
    objeto = json.loads(limpio)
    if not isinstance(objeto, dict):
        raise ValueError("La lectura de la imagen no devolvió un objeto JSON.")
    return {
        "fecha_orden": objeto.get("fecha_orden") or objeto.get("fecha") or "",
        "nombre_comercio": objeto.get("nombre_comercio") or objeto.get("tienda") or "",
        "id_orden_en_peticion": (
            objeto.get("id_orden_en_peticion") or objeto.get("id_orden") or ""
        ),
    }


def _rellenar_vacios(datos, hallado):
    for campo in ("fecha_orden", "nombre_comercio", "id_orden_en_peticion"):
        if not str(datos.get(campo) or "").strip() and str(hallado.get(campo) or "").strip():
            datos[campo] = " ".join(str(hallado[campo]).split())


def _esencial(datos):
    if str(datos.get("id_orden_en_peticion") or "").strip():
        return True
    return bool(
        str(datos.get("fecha_orden") or "").strip()
        and str(datos.get("nombre_comercio") or "").strip()
    )
