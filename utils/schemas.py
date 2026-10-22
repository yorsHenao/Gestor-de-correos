# utils/schemas.py
"""
Esquemas de datos y lógica de validación 
Garantiza que la comunicación entre agentes e IA tenga formatos válidos.
"""

from .exceptions import ClassificationError, InvalidResponseError
import logging

log = logging.getLogger("proyecto_e")

CATEGORIAS_VALIDAS = [
    "rt_bloqueado",
    "eliminar_deuda",
    "entes",
    "reclamo_aliado",
    "vuelo",
    "facturacion",
    "otro"
]

DECISIONES_VALIDAS = [
    "liberar",
    "mantener_bloqueo",
    "segunda_oportunidad"
]

TIPOS_REPRESENTACION_VALIDOS = ["rt", "tercero", "rt_rl"]

DATOS_EXTRACCION_RT_REQUERIDOS = [
    "peticion",
    "correo_rt",
    "correo_peticionario",
    "nombre_peticionario",
    "nombre_rt",
    "numero_documento",
    "id_rt",
    "telefono",
    "tipo_representacion",
]

def validar_clasificacion(resultado: dict) -> bool:
    """
    Valida la salida de la IA al clasificar un correo.
    """
    if not isinstance(resultado, dict):
        raise ClassificationError("El resultado de la clasificación de IA debe ser un diccionario JSON.")

    categorias = resultado.get("categorias")
    if not isinstance(categorias, list):
        raise ClassificationError("El campo 'categorias' de la clasificación de IA debe ser una lista.")

    for cat in categorias:
        if cat not in CATEGORIAS_VALIDAS:
            raise ClassificationError(f"Categoría inválida devuelta por la IA: '{cat}'. Válidas: {CATEGORIAS_VALIDAS}")

    confianza = resultado.get("confianza")
    if not isinstance(confianza, (int, float)) or not (0 <= confianza <= 1):
        raise ClassificationError(f"El campo 'confianza' de la clasificación de IA debe ser un número entre 0 y 1. Recibido: {confianza}")

    resumen = resultado.get("resumen")
    if not resumen or not isinstance(resumen, str):
        raise ClassificationError("El campo 'resumen' de la clasificación de IA debe ser una cadena de texto no vacía.")

    log.debug(f"Clasificación validada con éxito: {resultado}")
    return True


def validar_decision_slack(resultado: dict) -> bool:
    """
    Valida la decisión extraída del canal de Slack.
    """
    if not isinstance(resultado, dict):
        raise InvalidResponseError("La decisión de Slack debe ser un diccionario JSON.")

    decision = resultado.get("decision")
    if decision not in DECISIONES_VALIDAS:
        raise InvalidResponseError(f"Decisión de Slack inválida: '{decision}'. Válidas: {DECISIONES_VALIDAS}")

    # El campo causal_elegida es opcional, pero si existe debe ser número o cadena vacía/nula
    causal = resultado.get("causal_elegida")
    if causal is not None and not isinstance(causal, (int, str)):
        raise InvalidResponseError(f"La causal elegida de Slack debe ser un entero o cadena. Recibido: {type(causal)}")

    log.debug(f"Decisión de Slack validada con éxito: {resultado}")
    return True


def validar_extraccion_rt(resultado: dict) -> bool:
    """Valida los datos estructurados extraídos de un correo RTS."""
    if not isinstance(resultado, dict):
        raise InvalidResponseError("La extracción RTS debe ser un diccionario JSON.")

    faltantes = [
        campo
        for campo in DATOS_EXTRACCION_RT_REQUERIDOS
        if campo not in resultado
    ]
    if faltantes:
        raise InvalidResponseError(
            f"Faltan campos en la extracción RTS: {', '.join(faltantes)}"
        )

    campos_texto = [
        campo
        for campo in DATOS_EXTRACCION_RT_REQUERIDOS
        if campo != "tipo_representacion"
    ]
    for campo in campos_texto:
        if not isinstance(resultado[campo], str):
            raise InvalidResponseError(
                f"El campo '{campo}' de la extracción RTS debe ser texto."
            )

    if resultado["tipo_representacion"] not in TIPOS_REPRESENTACION_VALIDOS:
        raise InvalidResponseError(
            "Tipo de representación RTS inválido: "
            f"'{resultado['tipo_representacion']}'."
        )

    log.debug("Extracción RTS validada con éxito.")
    return True
