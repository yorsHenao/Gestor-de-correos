#excepciones personalizadas para el proyecto.

class AgentError(Exception):
    """Excepción base para todos los errores de agentes en el proyecto."""
    def __init__(self, message="Error en la ejecución del agente", details=None):
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self):
        if self.details:
            return f"{self.message} | Detalles: {self.details}"
        return self.message


class LupeSesionRequerida(AgentError):
    """Lupe pide login con Google y no se pudo reingresar sin una persona."""
    codigo = 20

    def __init__(self, message="Lupe requiere iniciar sesión (python scripts/iniciar_sesion.py)", details=None):
        super().__init__(message, details)


class LupeOcupado(AgentError):
    """Otro proceso tiene el perfil de Lupe y se agotó la espera del candado."""
    codigo = 21

    def __init__(self, message="Lupe está en uso por otro paso; se reintentará en la siguiente ronda", details=None):
        super().__init__(message, details)


class ErrorInfraestructura(AgentError):
    """Fallo transitorio externo que no debe consumir intentos de un caso."""

    def __init__(self, message="Fallo transitorio de infraestructura", details=None):
        super().__init__(message, details)


def es_infraestructura(error):
    """Reconoce caídas transitorias de IA, navegador y servicios de Google."""
    if isinstance(error, (ErrorInfraestructura, LupeSesionRequerida, LupeOcupado)):
        return True
    texto = str(error or "")
    bajo = texto.lower()
    nombre = type(error).__name__
    if nombre == "TargetClosedError":
        return True
    if "target page, context or browser has been closed" in bajo:
        return True
    if "todos los proveedores de ia fallaron" in bajo:
        return True
    if any(marca in bajo for marca in ("resource_exhausted", "rate_limit", "unavailable")):
        return True
    if "429" in texto or "503" in texto:
        return True
    estado = getattr(getattr(error, "resp", None), "status", 0)
    if estado == 429 or estado >= 500:
        return True
    return any(
        marca in bajo
        for marca in (
            "connection reset",
            "connection aborted",
            "connection refused",
            "remote end closed",
            "temporarily unavailable",
            "timed out",
            "timeout",
            "name resolution",
            "network is unreachable",
        )
    )


class ClassificationError(AgentError):
    """Lanzada cuando el AgenteClasificador no puede determinar la categoría del correo o da baja confianza."""
    def __init__(self, message="Error en la clasificación del correo", details=None, resumen=None):
        super().__init__(message, details)
        self.resumen = resumen


class ConfigurationError(AgentError):
    """Lanzada cuando hay un error en la configuración (.env faltante o variables vacías)."""
    def __init__(self, message="Error de configuración en el entorno", details=None):
        super().__init__(message, details)


class InvalidResponseError(AgentError):
    """Lanzada cuando una respuesta externa (Slack, API de Gmail, etc.) es inválida o inesperada."""
    def __init__(self, message="Respuesta inválida o inesperada recibida", details=None):
        super().__init__(message, details)
