"""Subagente para extraer datos de un correo de rappitendero."""

from typing import Any
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
from utils.schemas import validar_extraccion_rt


class AgenteExtractorRT:
    """Extrae datos estructurados necesarios para crear un caso RTS."""

    def extraer(self, asunto: str, cuerpo: str) -> dict[str, Any]:
        """Extrae petición, identidad y datos de búsqueda del rappitendero."""
        system = (
            "Eres un asistente que analiza correos y responde "
            "ÚNICAMENTE con JSON válido, sin texto adicional."
        )
        prompt = f"""
Analiza el siguiente correo y extrae la información del rappitendero.
Responde SOLO con un JSON con esta estructura:
{{
    "peticion": "Resumen en una sola línea, máximo 120 caracteres, de lo que pide el rappitendero (sin texto legal ni saludos).",
    "correo_rt": "Correo Soy Rappi (u otro correo de la cuenta del rappitendero) si aparece en el cuerpo o en un PDF; si no, vacío.",
    "correo_peticionario": "Correo de quien envió físicamente el Gmail, no el Soy Rappi del PDF.",
    "nombre_peticionario": "Nombre o apodo de quien envió el Gmail.",
    "nombre_rt": "Nombre del rappitendero en el cuerpo o PDF, cuya cuenta se pide.",
    "numero_documento": "Cédula del rappitendero del cuerpo o PDF; si no, vacío.",
    "id_rt": "Identificador numérico de la cuenta RT, tomado del asunto, del cuerpo o del PDF; no teléfono, cédula, IMEI, tarjeta, pedido ni orden.",
    "telefono": "Teléfono del rappitendero si aparece; no confundir con datos técnicos.",
    "tipo_representacion": "rt"
}}

Reglas:
- En correos reenviados, el peticionario es quien aparece en From: del mensaje reenviado.
- La petición debe ser breve (máx. 120 caracteres): acción concreta del rappitendero, sin intro del intermediario.
- nombre_rt, correo_rt, numero_documento y telefono salen del cuerpo o del PDF; id_rt también puede salir del asunto. Son del rappitendero cuya cuenta se pide. Nunca copies ahí el apodo ni el Gmail de quien envió.
- Para id_rt, toma solo los dígitos que siguen a id, ID, id:, id cuenta, id RT, ID Soy Rappi o identificador de repartidor, aunque vengan pegados a otra palabra o signo. Ejemplos: "id 1607441cuenta" -> 1607441; "ID:404811." -> 404811. No uses números precedidos de cédula, CC, PPT, teléfono, pedido u orden. Si el asunto y el cuerpo traen IDs distintos, usa el del cuerpo o PDF.
- Si el escrito nombra a una persona (por ejemplo Hugo) y el From es otra (por ejemplo Yisel), nombre_rt es Hugo.
- Si el From y el cuerpo son la misma persona, nombre_rt puede ser ese nombre.
- tipo_representacion es rt_rl solo si el escrito dice que quien pide es una firma, un abogado o un representante legal acreditado. Un Gmail de intermediario o un apodo no lo vuelve rt_rl.
- Quien escribe en primera persona ("mi cuenta", "mi ID", "quiero repartir") es rt, aunque el Gmail sea de otra persona.
- tipo_representacion es rt si el rappitendero pide por su propia cuenta.
- correo_rt es el correo de la cuenta del rappitendero (casi siempre Soy Rappi) si está en el cuerpo o en el PDF. Nunca lo dejes vacío si aparece. El Soy Rappi del PDF no va en correo_peticionario.
- Si un dato no aparece, devuelve una cadena vacía.

ASUNTO: {asunto}
CUERPO: {cuerpo}
"""
        resultado = llamar_json(prompt, system, operacion="extracción de correo")
        validar_extraccion_rt(resultado)
        peticion = " ".join(str(resultado.get("peticion", "")).split())
        if len(peticion) > 200:
            resultado["peticion"] = peticion[:199].rstrip() + "…"
        else:
            resultado["peticion"] = peticion

        # Si la IA devolvió un número u otro dato en lugar de email, moverlo al campo correcto
        correo_pet = resultado.get("correo_peticionario", "")
        if correo_pet and "@" not in correo_pet:
            # Si no tiene @, probablemente es un número o documento
            # Moverlo al campo correcto (preferir telefono, luego numero_documento)
            if not resultado.get("telefono") and correo_pet.isdigit():
                resultado["telefono"] = correo_pet
            elif not resultado.get("numero_documento") and len(correo_pet) > 5:
                resultado["numero_documento"] = correo_pet
            # Limpiar correo_peticionario dejarlo vacío
            resultado["correo_peticionario"] = ""

        correo_rt = str(resultado.get("correo_rt") or "").strip()
        if correo_rt and "@" not in correo_rt:
            resultado["correo_rt"] = ""
        else:
            resultado["correo_rt"] = correo_rt.lower()

        resultado["nombre_rt"] = " ".join(str(resultado.get("nombre_rt") or "").split())

        return resultado