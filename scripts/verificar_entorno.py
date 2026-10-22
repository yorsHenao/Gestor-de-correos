#!/usr/bin/env python3
"""Comprueba scripts e imports de los pasos y del servicio (sin ejecutar Gmail/Lupe/Slack)."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS)

PRUEBAS = [
    ("login Lupe", "iniciar_sesion"),
    ("clasificar", "clasificar_correos_gmail"),
    ("rt + slack", "agentes.agente_rts"),
    ("slack", "leer_slack"),
    ("entes", "procesar_entes"),
    ("orquestador", "app.orquestador"),
    ("scheduler", "app.scheduler"),
    ("dashboard", "app.dashboard.web"),
    ("config", None),
]


def main():
    ok = 0
    fail = 0
    print("Verificacion de entorno (imports)\n")
    from app.orquestador import PASOS

    for nombre, paso in PASOS.items():
        ruta = os.path.join(SCRIPTS, paso["script"])
        if not os.path.isfile(ruta):
            print(f"  FAIL paso {nombre}: no existe {ruta}")
            fail += 1
        else:
            print(f"  OK  paso {nombre} ({paso['script']})")
            ok += 1
    for etiqueta, modulo in PRUEBAS:
        try:
            if modulo is None:
                from utils.config_manager import validar_criticos

                validar_criticos()
            else:
                __import__(modulo)
            print(f"  OK  {etiqueta}")
            ok += 1
        except Exception as e:
            print(f"  FAIL {etiqueta}: {e}")
            fail += 1
    print(f"\nResultado: {ok} OK, {fail} FAIL")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
