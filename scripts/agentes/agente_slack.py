"""Interpreta la decisión de OPS en el hilo de Slack."""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ia_cliente import llamar_json
from utils.schemas import validar_decision_slack


class AgenteSlack:
    """Lee el hilo de OPS y devuelve la decisión sobre la cuenta."""

    def interpretar_respuesta(self, texto: str) -> dict:
        """Interpreta una respuesta de operador usando el prompt de Slack."""
        system = (
            "Eres un asistente que analiza respuestas de Slack y responde "
            "ÚNICAMENTE con JSON válido, sin texto adicional."
        )
        prompt = f"""
Analiza el hilo de Slack de operaciones sobre un rappitendero bloqueado.
Puede haber varios mensajes (confirmaciones, delegaciones, cierres como "gracias").
Responde SOLO con este JSON:
{{"decision": "mantener_bloqueo" | "liberar" | "segunda_oportunidad"}}

Reglas:
- Extrae la decisión operativa sobre la cuenta en CUALQUIER mensaje del hilo, no solo el último.
- Ignora saludos, agradecimientos ("gracias", "ok", "listo") y mensajes que no definan bloqueo o liberación.
- Si hay contradicción, prevalece la ÚLTIMA afirmación explícita sobre mantener bloqueo, liberar/habilitar o segunda oportunidad (no el último mensaje genérico).
- mantener_bloqueo: se mantiene bloqueo, no se reactiva, sigue bloqueado, etc.
- liberar: liberar, habilitar, reactivar, desbloquear, cuenta habilitada, etc.
- segunda_oportunidad: segunda oportunidad explícita.

Hilo de operaciones (orden cronológico):
{texto}
"""
        resultado = llamar_json(prompt, system, operacion="respuesta de Slack")
        validar_decision_slack(resultado)
        return resultado
