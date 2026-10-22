"""Pide correo o teléfono cuando la orden no tiene cuenta en Lupe.

Responde en el mismo hilo a GMAIL_REPLY_TO con los helpers de enviar_correo:
borrador si CONTROL_CALIDAD=true, envío si es false.
"""

import html

from enviar_correo import _construir_mensaje, _crear_borrador, _enviar, _modo_borrador
from entes.hoja import (
    ESTADO_MAS_INFO,
    ESTADO_MAS_INFO_ENVIADO,
    anotar_estado,
    cerrar_caso,
)

BORRADOR = "borrador"
ENVIADO = "enviado"


def pedir_informacion(servicio, peticion, clog):
    """Devuelve ``(borrador|enviado, fila_cerrada)``; None si no se entregó."""
    registro = (peticion.get("formulario") or {}).get("registro") or {}
    mensaje = _construir_mensaje(
        peticion.get("asunto") or "Sin asunto",
        _cuerpo(peticion.get("nombre_peticionario") or "", peticion.get("correo_peticionario") or ""),
        peticion.get("message_id") or "",
    )
    borrador = _modo_borrador()
    entregar = _crear_borrador if borrador else _enviar
    if not entregar(servicio, mensaje, peticion.get("thread_id") or ""):
        clog.error("No se pudo pedir la información; el correo sigue sin leer")
        return None
    if borrador:
        guardado = anotar_estado(registro, ESTADO_MAS_INFO, clog)
    else:
        guardado = cerrar_caso(registro, ESTADO_MAS_INFO_ENVIADO, clog)
    if not guardado:
        clog.warning("El correo quedó preparado, pero falta actualizar el Sheet")
    clog.info("Pedido de información: %s", "borrador creado" if borrador else "enviado")
    return (BORRADOR if borrador else ENVIADO), guardado


def _cuerpo(nombre, correo):
    return f"""<html>
<body>
<p>Estimado/a,<br><strong>{html.escape(nombre)}</strong><br>{html.escape(correo)}</p>
<p>Por medio de la presente nos permitimos solicitarle los siguientes datos:</p>
<p>Correo electrónico y/o número de teléfono vinculado a su cuenta en la Plataforma Rappi.</p>
<p>Lo anterior con el fin de gestionar su petición.</p>
<p>Gracias.</p>
</body>
</html>"""
