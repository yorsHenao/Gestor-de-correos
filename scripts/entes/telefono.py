"""Teléfonos de Colombia: 10 dígitos sin el indicativo 57."""

import re

_INDICATIVO = re.compile(r"^\s*(?:\+|00)\s*(\d{1,3})")


def telefono_colombia(texto):
    """Devuelve (telefono, extranjero). Si el número no es de Colombia, el teléfono queda vacío."""
    crudo = str(texto or "").strip()
    if not crudo:
        return "", False
    digitos = re.sub(r"\D", "", crudo)
    indicativo = _INDICATIVO.match(crudo)
    if indicativo and not digitos.lstrip("0").startswith("57"):
        return "", True
    digitos = digitos.lstrip("0") if crudo.startswith("00") else digitos
    if len(digitos) == 12 and digitos.startswith("57"):
        digitos = digitos[2:]
    if len(digitos) == 10:
        return digitos, False
    return "", False
