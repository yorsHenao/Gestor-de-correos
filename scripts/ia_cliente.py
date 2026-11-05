import json
import time

from google import genai
from google.genai import types
from groq import Groq

from config import GEMINI_API_KEY, GEMINI_MODEL, GROQ_API_KEY, GROQ_API_KEY2, GROQ_MODEL, IA_PROVIDER
from utils.logger import obtener_log, segundos_reintento

log = obtener_log()


_GEMINI_MODEL = GEMINI_MODEL
_GROQ_MODEL = GROQ_MODEL
_ESPERAS_LIMITE = (1, 2, 4)
_TOPE_ESPERA = 20
_cliente_gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
_cliente_groq = Groq(api_key=GROQ_API_KEY2 or GROQ_API_KEY) if (GROQ_API_KEY2 or GROQ_API_KEY) else None


def _parsear_json(texto):
    texto = texto.strip().replace("```json", "").replace("```", "")
    return json.loads(texto)


def _llamar_gemini(prompt, system):
    if _cliente_gemini is None:
        raise RuntimeError("GEMINI_API_KEY no está configurada")
    respuesta = _cliente_gemini.models.generate_content(
        model=_GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return _parsear_json(respuesta.text)


def _llamar_groq(prompt, system):
    if _cliente_groq is None:
        raise RuntimeError("GROQ_API_KEY no está configurada")
    respuesta = _cliente_groq.chat.completions.create(
        model=_GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return _parsear_json(respuesta.choices[0].message.content)


def _codigo_limite(error):
    codigo = getattr(error, "status_code", None) or getattr(error, "code", None)
    if codigo in (429, 503):
        return codigo
    texto = str(error)
    if "429" in texto or "rate_limit" in texto.lower():
        return 429
    if "503" in texto or "UNAVAILABLE" in texto:
        return 503
    return 0


def _espera_limite(error, intento):
    segundos = segundos_reintento(error)
    if segundos is None:
        return _ESPERAS_LIMITE[min(intento, len(_ESPERAS_LIMITE) - 1)]
    return segundos


def llamar_json(prompt, system, operacion="IA", anunciar=True):
    proveedores = ["gemini", "groq"] if IA_PROVIDER == "gemini" else ["groq", "gemini"]
    errores = []

    for indice, proveedor in enumerate(proveedores):
        anunciado = False
        for intento in range(len(_ESPERAS_LIMITE) + 1):
            try:
                if anunciar and not anunciado:
                    log.debug("IA para %s: %s", operacion, proveedor.title())
                    anunciado = True
                if proveedor == "gemini":
                    return _llamar_gemini(prompt, system)
                return _llamar_groq(prompt, system)
            except Exception as error:
                if not _codigo_limite(error) or intento == len(_ESPERAS_LIMITE):
                    errores.append(f"{proveedor}: {error}")
                    break
                espera = _espera_limite(error, intento)
                if espera > _TOPE_ESPERA:
                    errores.append(f"{proveedor}: {error}")
                    break
                log.info("IA %s limitada; reintento en %.1f s", proveedor.title(), espera)
                time.sleep(espera)
        if indice < len(proveedores) - 1 and errores:
            siguiente = proveedores[indice + 1].title()
            log.warning(
                "IA %s falló (%s); se usa %s como respaldo",
                proveedor.title(),
                errores[-1].split(": ", 1)[-1][:200],
                siguiente,
            )

    raise RuntimeError("Todos los proveedores de IA fallaron: " + " | ".join(errores))


def llamar_gemini_vision(contenido, instruccion, mime_type="application/pdf"):
    if _cliente_gemini is None:
        raise RuntimeError("GEMINI_API_KEY no está configurada")
    for intento in range(2):
        try:
            respuesta = _cliente_gemini.models.generate_content(
                model=_GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=contenido, mime_type=mime_type),
                    instruccion,
                ],
            )
            return respuesta.text.strip()
        except Exception as error:
            if not _codigo_limite(error) or intento:
                raise
            espera = _espera_limite(error, intento)
            if espera > _TOPE_ESPERA:
                raise
            log.info("IA Gemini Vision limitada; reintento en %.1f s", espera)
            time.sleep(espera)
