"""Agente Entes: reglas y extracción M1 de una petición ya etiquetada."""

import re
import sys
from datetime import date
from email.utils import parseaddr
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from entes.telefono import telefono_colombia
from ia_cliente import llamar_json

UBICACIONES = ("cuerpo", "adjunto", "ambos")
BUSQUEDAS = ("ninguna", "cuenta", "orden")
CAUSALES = (
    "Orden no entregada",
    "Cancelación y cobro de orden",
    "Cobros no reconocidos",
    "Cargos por prime/pro",
    "Otro",
    "Orden Incompleta",
    "Se duplicó el pedido/cobro",
    "Orden en mal estado",
    "Eliminación cuenta",
    "Reembolso en Créditos",
    "Inconvenientes con la App",
    "Orden incorrecta (productos errados)",
    "Cuenta Bloqueada",
    "No devolución de la $",
    "Eliminación Deuda",
    "Cuenta utilizada por terceros",
    "Actualización de datos",
    "informacion de cuenta",
    "Precios diferentes",
    "Inconveniente Rappifavor",
    "Queja Datos Personales",
    "Queja Cashback 99%",
    "Eliminación Datos",
)
_ANIO = re.compile(r"\b(?:19|20)\d{2}\b")
_DIA_MES = re.compile(r"^\d{1,2}[/-]\d{1,2}$")
_NOMBRES_RAPPI = {
    "legal",
    "notificaciones",
    "notificaciones rappi",
    "rappi",
}


class AgenteEntes:
    """Lee el texto de la petición y devuelve los datos para Lupe. No clasifica el tema."""

    def extraer(
        self,
        asunto: str,
        cuerpo: str,
        remitente: str,
        fecha_correo: str,
        adjuntos: list[dict[str, Any]],
        texto_pdf: str = "",
    ) -> dict[str, Any]:
        anio = date.today().year
        nombres = [
            str(item.get("nombre", "")).strip()
            for item in adjuntos
            if str(item.get("nombre", "")).strip()
            and not str(item.get("mime", "")).startswith("image/")
        ]
        lista_adjuntos = ", ".join(nombres) if nombres else "(ninguno)"
        system = (
            "Eres un asistente que analiza correos de peticiones de usuarios "
            "de la app Rappi y responde ÚNICAMENTE con JSON válido, sin texto adicional."
        )
        prompt = f"""
El correo ya está en la etiqueta Gmail "entes". No clasifiques el tema jurídico ni decidas si es una petición.
Extrae las pistas de búsqueda y di qué hay que buscar. El id de usuario del formulario no se extrae aquí: lo devuelve Lupe.
Año en curso, para cualquier fecha que no traiga año: {anio}.

Responde SOLO con un JSON con esta estructura:
{{
    "correo_peticionario": "Correo del usuario que presenta la petición.",
    "telefono": "Teléfono de Colombia en 10 dígitos sin indicativo; si no, cadena vacía.",
    "telefono_extranjero": false,
    "id_orden_en_peticion": "Id de orden solo si está escrito; si no, cadena vacía.",
    "fecha_orden": "Fecha del pedido; si no aparece, cadena vacía.",
    "nombre_comercio": "Nombre del comercio si aparece; si no, cadena vacía.",
    "multi_pedido": false,
    "ubicacion_peticion": "cuerpo",
    "busqueda": "orden",
    "resumen": "El usuario pide el reembolso de un pedido que no recibió.",
    "causal": "Problemas con el RT",
    "valor_peticion": "Monto en pesos que reclama el usuario; si no aparece, cadena vacía."
}}

Reglas:
- correo_peticionario es el usuario de la petición, no quien asigna el caso dentro de Rappi.
- Si el mensaje es un reenvío, toma el último From: o De: del bloque reenviado.
- Descarta cualquier correo @rappi.com (Legal, buzón QA, notificaciones, quien reenvía o asigna).
- Si el único correo visible es @rappi.com, deja correo_peticionario vacío.
- Si un dato no aparece, devuelve una cadena vacía. No inventes ids, teléfonos, comercios ni correos.
- telefono: solo revisamos casos de Colombia. Devuelve el número en 10 dígitos sin indicativo (ej. 3001234567). Si trae +57 o 57 al inicio, quítalo.
- Si el número es de otro país (+51, +52, +1, etc.), deja telefono vacío y telefono_extranjero en true. Si no, telefono_extranjero es false.
- id_orden_en_peticion solo si el usuario escribió un id de orden. No uses cédula, teléfono ni id de usuario.
- fecha_orden: si el texto trae día y mes sin año, el año es {anio}. Si no hay fecha de pedido, cadena vacía.
- multi_pedido es true solo si la petición nombra más de un pedido.
- ubicacion_peticion es "cuerpo" si la petición está solo en el cuerpo, "adjunto" si está solo en el PDF, "ambos" si está en los dos.
- Las imágenes adjuntas no se leen. No las uses como fuente.
- No armes un PDF ni resumas el caso en otro formato.
- busqueda solo puede ser "ninguna", "cuenta" u "orden". No es el tema del caso: dice qué buscar.
- ninguna: la persona dice de forma explícita que no tiene cuenta en Rappi, o que no tiene medios de pago registrados en Rappi. También es ninguna si niega haber hecho la compra y pide que investiguen que no fue quien efectuó el cobro o el pedido. No se busca en Lupe; el formulario se llena con ceros.
- cuenta: no dice que no tiene cuenta, reconoce ser usuaria pero no reconoce un cobro concreto y no niega que la compra la hizo otra persona. Sí se busca la cuenta. No se abren pedidos. También es cuenta si el problema es la cuenta o la app, y no un pedido: recuperar la cuenta, no poder ingresar, no poder actualizar métodos de pago, que la app no abre y Rappi Pro.
- orden: hubo un problema con un pedido, o con el repartidor de un pedido. Un cobro no reconocido no es "orden" solo porque traiga monto o fecha.
- Si nombran un pedido y también la app, busqueda es "orden".
- Ejemplos de busqueda:
  - "No tengo cuenta en Rappi." → ninguna
  - "No tengo medios de pago registrados en Rappi." → ninguna
  - "La cuenta bancaria afectada no está vinculada, con mi autorización, a ninguna cuenta de Rappi a mi nombre desde la cual yo haya realizado esta compra." → ninguna
  - "Solicito que por favor investiguen el pedido, la cuenta desde la cual se realizó, la fecha y hora de la transacción y cualquier información que permita verificar que no fui yo quien efectuó la compra." → ninguna, y la causal es "Cobros no reconocidos"
  - "No reconozco un cobro de $32.990 del 4 de octubre." Sin negar que fue quien compró ni pedir verificar que no fue él → cuenta, y la causal es "Cobros no reconocidos"
- resumen: una o dos frases, en español, con lo que pide el usuario. Di siempre "el usuario". No escribas nombres ni apellidos de personas, ni "el cliente", ni "la cliente". No inventes ids, montos ni tiendas.
- valor_peticion: solo el monto que el usuario reclama o pide devolver, tal como lo escribe (ej. 32.990). No inventes montos; si no hay, cadena vacía.
- causal es solo el nombre de la lista para la hoja. Cópialo tal cual, sin cambiar mayúsculas, tildes ni signos. Elige uno:
{chr(10).join(f"  - {nombre}" for nombre in CAUSALES)}

REMITENTE: {remitente}
ASUNTO: {asunto}
ADJUNTOS (sin imágenes): {lista_adjuntos}
CUERPO:
{cuerpo}
PDF:
{texto_pdf or "(sin texto de PDF)"}
"""
        resultado = llamar_json(prompt, system, operacion="extracción petición entes")
        return _normalizar(
            resultado,
            fecha_correo,
            cuerpo,
            adjuntos,
            texto_pdf,
            remitente,
            anio,
        )


def _normalizar(resultado, fecha_correo, cuerpo, adjuntos, texto_pdf, remitente, anio):
    if not isinstance(resultado, dict):
        raise ValueError("La extracción de la petición no devolvió un objeto JSON.")

    datos = {
        "correo_peticionario": _texto(resultado.get("correo_peticionario")).lower(),
        "telefono": _texto(resultado.get("telefono")),
        "fecha_llegada_correo": _texto(fecha_correo),
        "id_orden_en_peticion": _texto(resultado.get("id_orden_en_peticion")),
        "fecha_orden": _fecha_con_anio(resultado.get("fecha_orden"), anio),
        "nombre_comercio": _texto(resultado.get("nombre_comercio")),
        "multi_pedido": _booleano(resultado.get("multi_pedido")),
        "ubicacion_peticion": _texto(resultado.get("ubicacion_peticion")).lower(),
        "busqueda": _busqueda(resultado.get("busqueda")),
        "resumen": _frase(_texto(resultado.get("resumen"))),
        "causal": _causal(resultado.get("causal")),
        "valor_peticion": _monto(resultado.get("valor_peticion")),
        "nombre_peticionario": "",
    }
    correo = datos["correo_peticionario"]
    if correo and "@" not in correo:
        if not datos["telefono"] and correo.isdigit():
            datos["telefono"] = correo
        datos["correo_peticionario"] = ""
        correo = ""

    telefono, extranjero = telefono_colombia(datos["telefono"])
    datos["telefono"] = telefono
    datos["telefono_extranjero"] = extranjero or (
        not telefono and _booleano(resultado.get("telefono_extranjero"))
    )

    preferido = "" if not correo or correo.endswith("@rappi.com") else correo
    # El nombre debe servir para encontrar el reenvío en Gmail: solo sale de
    # sus cabeceras De/From, nunca del PDF ni de imágenes.
    nombre, correo_reenvio = _persona_reenviada(remitente, cuerpo, preferido)
    datos["nombre_peticionario"] = nombre or nombre_desde_reenvio(remitente, cuerpo)
    if not correo or correo.endswith("@rappi.com"):
        datos["correo_peticionario"] = correo_reenvio

    if datos["ubicacion_peticion"] not in UBICACIONES:
        datos["ubicacion_peticion"] = _ubicacion_por_presencia(cuerpo, adjuntos, texto_pdf)
    return datos


def _persona_reenviada(remitente, texto, correo_preferido=""):
    """Nombre y correo del From/De externo. El nombre es el de esa misma línea."""
    candidatos = _candidatos_reenvio(remitente, texto)
    preferido = (correo_preferido or "").strip().lower()
    primero = None
    for candidato in candidatos:
        nombre, correo = parseaddr(str(candidato or ""))
        correo = (correo or "").strip().lower()
        if not correo or "@" not in correo or correo.endswith("@rappi.com"):
            continue
        persona = (_nombre(nombre), correo)
        if preferido and correo == preferido:
            return persona
        if primero is None:
            primero = persona
    if preferido:
        return "", ""
    return primero or ("", "")


def nombre_desde_reenvio(remitente, cuerpo):
    """Nombre citado por Gmail en De/From; acepta «Nombre via Notificaciones Rappi»."""
    for candidato in _candidatos_reenvio(remitente, cuerpo):
        nombre, correo = parseaddr(str(candidato or ""))
        correo = (correo or "").strip().lower()
        nombre = _nombre_remitente_notificado(nombre)
        if nombre and (
            not correo
            or not correo.endswith("@rappi.com")
            or re.search(r"(?i)\s+via\s+", str(candidato or ""))
        ):
            return nombre
    return ""


def _candidatos_reenvio(remitente, texto):
    bloques = re.split(
        r"(?im)^\s*(?:[-\s]*forwarded message[-\s]*|mensaje reenviado)\s*$",
        texto or "",
    )
    bloque = bloques[-1] if len(bloques) > 1 else (texto or "")
    encontrados = re.findall(r"(?im)^\s*(?:from|de):\s*(.+?)\s*$", bloque)
    return list(reversed(encontrados)) if encontrados else [remitente]


def _nombre_remitente_notificado(valor):
    nombre = re.split(r"(?i)\s+via\s+", _texto(valor), maxsplit=1)[0]
    nombre = _nombre(nombre)
    return "" if nombre.casefold() in _NOMBRES_RAPPI else nombre


def _nombre(valor):
    texto = _texto(valor).strip("\"'")
    return texto.title() if texto else ""


def _fecha_con_anio(valor, anio):
    texto = _texto(valor)
    if not texto or _ANIO.search(texto):
        return texto
    if _DIA_MES.fullmatch(texto):
        return f"{texto}/{anio}"
    return f"{texto} {anio}"


def _ubicacion_por_presencia(cuerpo, adjuntos, texto_pdf):
    hay_cuerpo = bool(_texto(cuerpo))
    hay_pdf = bool(_texto(texto_pdf)) or any(
        str(item.get("mime", "")) == "application/pdf" for item in adjuntos
    )
    if hay_cuerpo and hay_pdf:
        return "ambos"
    if hay_pdf:
        return "adjunto"
    return "cuerpo"


def _frase(texto):
    if not texto:
        return ""
    return texto[0].upper() + texto[1:]


def _monto(valor):
    """Monto en pesos solo con dígitos: "$32.990,00" -> "32990". Vacío si no hay."""
    texto = re.sub(r"[^\d.,]", "", _texto(valor))
    if re.search(r"[.,]\d{2}$", texto) and not re.search(r"[.,]\d{3}$", texto):
        texto = texto[:-3]
    digitos = re.sub(r"\D", "", texto)
    return digitos.lstrip("0") or ""


def _texto(valor):
    return " ".join(str(valor or "").split())


def _causal(valor):
    texto = _texto(valor)
    if texto in CAUSALES:
        return texto
    return "Otro"


def _busqueda(valor):
    texto = _texto(valor).lower()
    if texto in BUSQUEDAS:
        return texto
    return "cuenta"


def _booleano(valor):
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in {"1", "true", "si", "sí", "yes"}
