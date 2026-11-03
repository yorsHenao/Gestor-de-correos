#!/usr/bin/env python3
"""
AgenteClasificador - Módulo de clasificación de correos usando IA Groq/Gemini.

Este agente encapsula la lógica de clasificación de correos que anteriormente
estaba en `ia2.py`. Utiliza las utilidades del proyecto para validar esquemas
y configuración centralizada.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.logger import log
import config
from utils.schemas import validar_clasificacion, CATEGORIAS_VALIDAS
from utils.exceptions import ClassificationError


def obtener_categorias_predeterminadas() -> list:
    """Retorna la lista de categorías válidas disponibles."""
    return CATEGORIAS_VALIDAS.copy()


def obtener_info_ia() -> dict:
    """Retorna información sobre el proveedor de IA configurado."""
    return {
        "ia_provider": config.IA_PROVIDER,
        "groq_api_key": bool(config.GROQ_API_KEY),
        "gemini_api_key": bool(config.GEMINI_API_KEY),
    }


LIMITE_TEXTO = 3000
RESERVA_ADJUNTOS = 1200


def _recortar_texto(cuerpo: str, adjuntos: str) -> tuple:
    """Reserva espacio al adjunto para que un cuerpo largo no lo deje fuera."""
    cuerpo = (cuerpo or "").strip()
    adjuntos = (adjuntos or "").strip()
    if not adjuntos:
        return cuerpo[:LIMITE_TEXTO], ""
    cupo_adj = min(len(adjuntos), RESERVA_ADJUNTOS)
    cupo_cuerpo = LIMITE_TEXTO - cupo_adj
    if len(cuerpo) < cupo_cuerpo:
        cupo_adj = min(len(adjuntos), cupo_adj + (cupo_cuerpo - len(cuerpo)))
    return cuerpo[:cupo_cuerpo], adjuntos[:cupo_adj]


def clasificar_correo(asunto: str, cuerpo: str, adjuntos: str = "") -> dict:
    """
    Clasifica un correo usando la IA configurada y valida su respuesta.

    Args:
        asunto: Asunto del correo.
        cuerpo: Cuerpo del correo.
        adjuntos: Texto extraído de PDF o Word, si el mensaje trae archivos.

    Returns:
        Diccionario con categorías, confianza y resumen.

    Raises:
        ClassificationError: Si la IA falla o devuelve una estructura inválida.
    """
    cuerpo_prompt, adjuntos_prompt = _recortar_texto(cuerpo, adjuntos)
    log.debug("Clasificando correo: asunto='%s...'", asunto[:50])
    system = (
        "Eres un asistente que analiza correos y responde "
        "ÚNICAMENTE con JSON válido, sin texto adicional."
    )
    prompt = f"""
Analiza el siguiente correo. El texto incluye el CUERPO y, si existe, el texto extraído de ADJUNTOS (PDF o Word).
Usa los dos. Si el adjunto pide algo distinto al cuerpo, clasifica también esa solicitud.
Usa hechos y peticiones. Si los hechos dicen que la cuenta está suspendida o inhabilitada, eso cuenta aunque las pretensiones solo pidan informar la causa o aportar pruebas.

Responde SOLO con este JSON:
{{
    "categorias": lista con una o más categorías, o [] si no aplica ninguna,
    "confianza": número entre 0 y 1,
    "resumen": resumen en una sola oración, máximo 15 palabras, de la solicitud principal
}}

Categorías posibles:
- rt_bloqueado: un rappitendero (repartidor, Soy Rappi, RT) cuya cuenta Rappi suspendió, inhabilitó o bloqueó (sanción). Incluye si pide desbloquear esa cuenta suspendida, Y también si solo pide la razón de la suspensión, pruebas, ID de pedido u orden, fecha, hora, geolocalización, historial de órdenes, debido proceso u otras pretensiones sobre esa cuenta suspendida. No hace falta que escriba "desbloquéenme". También es rt_bloqueado si describe la cuenta como desactivada, eliminada o dada de baja pero pide desbloqueo, menciona bloqueo, suspensión o inhabilitación por Rappi, o debido proceso sobre esa medida. No aplica si solo pide activar o reactivar una cuenta desactivada, eliminada o dada de baja sin suspensión, bloqueo, desbloqueo ni inhabilitación (el RT o el sistema la apagaron): eso es []. Tampoco si solo pide recuperar la clave, el correo de Soy Rappi, el ID de acceso, crear la cuenta, actualizar datos de registro, o verificar si ya está registrado. Tampoco aplica si no puede conectarse por un error de la app, si Incode u otra verificación de identidad se queda en "Procesando" o en error, o si pide revisión de su registro o de su verificación de identidad, y no dice que Rappi suspendió o bloqueó la cuenta.
- eliminar_deuda: un rappitendero (rt, repartidor) solicita eliminar, condonar, exonerar o revisar un "cobro injustificado" o deuda (por pedidos liberados, fallos de la app, rechazos de pago, robos, o cuando la tienda entregó el pedido a otro RT). No incluye incentivos, bonos, recompensas ni semanas no pagadas.
- facturacion: una empresa, persona jurídica o aliado reclama errores en facturas o solicita anulación/reexpedición.
- entes: un cliente final (consumidor de la app Rappi) reclama problemas con un pedido suyo, Rappi Pro, demoras en reembolsos a su tarjeta, transacciones no reconocidas, cargos no autorizados o eliminación de datos personales. No usar para rappitenderos. No usar si quien escribe es un comercio, tienda, restaurante o aliado describiendo problemas operativos en su local (repartidores, llegada, pedidos liberados): eso es reclamo_aliado.
- vuelo: problemas relacionados con vuelos, viajes, hoteles o RappiTravel.
- reclamo_aliado: restaurante, tienda, comercio o aliado reclama pagos, promociones, publicidad u operación en su punto de venta: repartidores que no marcan llegada, pedidos liberados o cancelados que afectan al negocio, fallas de la app en el local, actualizaciones que impiden operar. Aplica aunque el texto diga "repartidores" o "pedidos liberados"; no es entes ni rt_bloqueado si quien reclama es el aliado. No usar para rappitenderos ni consumidores finales.
- otro: SOLO correo corporativo que no es una petición ni trabajo sobre una petición: Meet, Teams, Zoom, calendario, Gemini, Internal Communications, Confluence, Diagnostikare, nómina, boletines, notificaciones de Google, comunicados internos. Nunca uses otro para un derecho de petición, una respuesta a una petición, ni una corrección del equipo.
- []: spam o texto incomprensible. También: soporte de acceso o incentivos, bonos, recompensas y semanas no pagadas de un rappitendero. También: petición de rappitendero que no es suspensión de cuenta ni deuda. También: correo del equipo (Legal, DPS, QA u otro interno) que corrige, completa, aclara o pide hacer algo sobre una petición ya existente de un RT o de un usuario. También: mensaje interno entre revisores que solo pide confirmar, validar o aclarar si un reenvío es el mismo caso, si ya está en gestión, si hay duplicado o doble gestión (sin petición nueva del peticionario). Esos casos van a revisión manual, no a rt_bloqueado, entes ni otro.

Reglas importantes:
- Identifica primero quién escribe: rappitendero ("Soy Rappi"), consumidor final (pide comida/pedido/reembolso a su tarjeta), o aliado/comercio/tienda/restaurante (problema en su negocio). No clasifiques como entes solo porque aparecen "repartidores" o "pedidos liberados" si el remitente es un negocio.
- Si el correo identifica tienda/comercio/aliado y un problema operativo con repartidores en el local, usa reclamo_aliado; no uses [] por ambigüedad ni entes por el vocabulario de repartidores.
- rt_bloqueado si es rappitendero Y (hechos o peticiones) dicen que la cuenta está suspendida, inhabilitada o bloqueada por Rappi, o piden desbloqueo o debido proceso de esa medida. "Desactivada", "eliminada" o "dada de baja" sin suspensión, bloqueo, desbloqueo ni inhabilitación va a []. Si el escrito usa esas palabras junto con suspensión, bloqueo, desbloqueo o debido proceso, es rt_bloqueado. El asunto "Cuenta suspendida" no basta si el texto solo pide quitar una deuda.
- Si pide activar o reactivar una cuenta desactivada, eliminada o dada de baja y no menciona suspensión, bloqueo, desbloqueo ni inhabilitación, no es rt_bloqueado. Devuelve []. Si sí los menciona, es rt_bloqueado.
- IDs de pedido, geolocalización, historial, pruebas y debido proceso no abren otra categoría y no mandan a revisión manual, salvo que también pida quitar deuda u otra categoría mapeada.
- "Bloqueado" cuenta como rt_bloqueado si se refiere a la cuenta del rappitendero (suspensión, bloqueo o desbloqueo), aunque el texto también diga desactivada o eliminada. Si el sistema, la carga o la verificación quedan bloqueados en "Procesando" o en un error, no es rt_bloqueado.
- Si pide quitar o exonerar la deuda Y además la cuenta está suspendida o pide desbloqueo, devuelve ambas: ["rt_bloqueado", "eliminar_deuda"].
- Si un rappitendero pide que le quiten una deuda y la cuenta no está suspendida, clasifícalo solo como eliminar_deuda.
- Incentivos, bonos, recompensas y semanas no pagadas de un rappitendero no son eliminar_deuda. Devuelve categorias: []. Van a revisión manual.
- Soporte de acceso de un rappitendero no es rt_bloqueado ni eliminar_deuda ni entes ni otro. Devuelve categorias: []. Incluye: perdió el correo de Soy Rappi, olvidó la clave o el ID, no puede iniciar sesión o conectarse, no puede crear la cuenta, pide actualizar datos, pide verificar si sus datos ya están registrados, Incode u otra verificación queda en "Procesando" o en error, o pide revisión de su registro o verificación de identidad, sin decir que Rappi suspendió la cuenta.
- Correo del equipo que corrige, completa o pide un ajuste sobre una petición ya radicada de un RT o usuario: categorias: []. No es rt_bloqueado ni otro.
- Coordinación entre revisores (p. ej. pedir confirmación si el reenvío es el mismo caso que ya se gestiona): categorias: []. No confundir con el peticionario que pide "confirmar" un cobro, una suspensión o un reembolso.
- Nunca uses otro para una petición. otro es solo correo corporativo sin petición.
- La confianza debe estar entre 0 y 1; el resumen no puede estar vacío.

Ejemplos:
- Repartidor presenta un Derecho de Petición por cobro injustificado tras liberar un pedido en la app -> ["eliminar_deuda"]
- RT reporta en Soy Rappi que llegó a la tienda pero le entregaron el pedido a otro repartidor y le cobraron la deuda a él -> ["eliminar_deuda"]
- RT reclama un bono, un incentivo o una semana que no le pagaron, sin pedir quitar una deuda -> []
- RT fue asaltado entregando un RappiFavor y exige que le eliminen la deuda y le desbloqueen la cuenta -> ["rt_bloqueado", "eliminar_deuda"]
- RT solicita la exoneración de la deuda y el desbloqueo de la cuenta suspendida por Rappi -> ["rt_bloqueado", "eliminar_deuda"]
- Asunto dice "Cuenta suspendida", pero en el texto solo pide que le quiten la deuda de un pedido que el cliente canceló -> ["eliminar_deuda"]
- RT pide activar su cuenta desactivada, sin suspensión ni bloqueo -> []
- RT dice que su cuenta fue desactivada y también habla de un bloqueo; pide que la activen o la desbloqueen -> ["rt_bloqueado"]
- RT pide que, si su cuenta está suspendida o desactivada, se estudie reactivarla -> ["rt_bloqueado"]
- RT dice que la app fue desactivada y pide el desbloqueo de la cuenta -> ["rt_bloqueado"]
- RT pide que le desbloqueen la cuenta suspendida y no menciona deuda -> ["rt_bloqueado"]
- RT (hechos: es repartidor y su cuenta está suspendida) pide la razón, IDs de orden, geolocalización y debido proceso, sin pedir quitar deuda -> ["rt_bloqueado"]
- RT perdió el correo de Soy Rappi y olvidó la clave y el ID; no dice que la cuenta esté suspendida -> []
- RT no puede crear ni entrar a la cuenta y pide verificar si sus datos ya están registrados -> []
- RT no puede conectarse: Incode queda en "Procesando" o en error, pide revisión de la verificación y que habiliten su acceso para trabajar, sin decir que Rappi suspendió la cuenta -> []
- RT pide revisión manual de su proceso de registro porque no logra conectarse ni trabajar; no dice que la cuenta esté suspendida -> []
- RT pide actualizar datos porque no lo deja conectar y aparece un error; el sistema queda bloqueado en la carga -> []
- El cuerpo solo saluda y el adjunto pide quitar una deuda por un pedido liberado -> ["eliminar_deuda"]
- Cliente de Rappi reporta que su pedido llegó incompleto y exige la devolución de su dinero a su tarjeta -> ["entes"]
- Usuario indica que le hicieron un doble cobro al pedir por Rappi y solicita el reembolso inmediato -> ["entes"]
- Persona externa exige devolución por transacción no reconocida en su tarjeta y eliminación de sus datos -> ["entes"]
- El equipo pide corregir, completar o hacer un ajuste sobre una petición ya presentada de un RT o usuario -> []
- Colega del equipo pide confirmar si un correo reenviado es el mismo caso que ya están gestionando -> []
- Comercio o tienda reporta que los repartidores no pueden marcar llegada y los pedidos se liberan solos -> ["reclamo_aliado"]
- Invitación a Google Meet sin pedido al equipo legal o DPS -> ["otro"]
- Correo de Internal Communications con actualización de políticas, sin solicitud -> ["otro"]
- Nota automática de Gemini con resumen de reunión, sin queja ni petición -> ["otro"]
- Correo de nómina o de Diagnostikare informativo, sin derecho de petición -> ["otro"]
- Boletín sobre RappiCard o RappiTravel sin reclamo del destinatario -> ["otro"]
- RT presenta derecho de petición sobre un tema que no es suspensión de cuenta ni deuda -> []

ASUNTO: {asunto}
CUERPO:
{cuerpo_prompt}
ADJUNTOS:
{adjuntos_prompt}
"""

    try:
        from ia_cliente import llamar_json

        resultado = llamar_json(
            prompt, system, operacion="clasificacion", anunciar=False
        )
        validar_clasificacion(resultado)
        log.debug(
            "Clasificación: categorías=%s, confianza=%.2f",
            resultado["categorias"],
            resultado["confianza"],
        )
        return resultado
    except ClassificationError:
        raise
    except Exception as error:
        log.debug("Error al clasificar correo: %s", error)
        raise ClassificationError(f"Error técnico al consultar la IA: {error}") from error


if __name__ == "__main__":
    print("Categorías disponibles:", obtener_categorias_predeterminadas())
    print("Proveedor configurado:", obtener_info_ia()["ia_provider"])