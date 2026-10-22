
"""punto de entrada sin menú.

    python main.py ciclo                          clasificar → rt → slack → entes, una vez
    python main.py paso clasificar|rt|slack|entes un solo paso
    python main.py servicio                       scheduler cada CICLO_HORAS + dashboard
    python main.py login                          login de Lupe con ventana (Sprint 1)
    python main.py pausar | reanudar              detiene o retoma los ciclos programados
"""

import argparse
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
for ruta in (str(RAIZ), str(RAIZ / "scripts")):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

from utils.logger import setup_logging

def _login():
    script = RAIZ / "scripts" / "iniciar_sesion.py"
    return subprocess.call([sys.executable, str(script)], cwd=str(script.parent))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="main.py", description="Proyecto E sin menú")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("ciclo", help="Corre el ciclo completo una vez")
    paso = sub.add_parser("paso", help="Corre un solo paso")
    paso.add_argument("nombre", choices=["clasificar", "rt", "slack", "entes"])
    sub.add_parser("servicio", help="Scheduler y dashboard")
    sub.add_parser("login", help="Login de Lupe con ventana")
    sub.add_parser("pausar", help="Pausa los ciclos programados")
    sub.add_parser("reanudar", help="Reanuda los ciclos programados")
    args = parser.parse_args(argv)

    log = setup_logging()

    if args.comando == "login":
        return _login()

    from app import orquestador

    if args.comando in ("paso", "servicio"):
        from utils.config_manager import validar_criticos

        try:
            validar_criticos()
        except Exception as error:
            log.error("Configuración inválida, revise .env: %s", error, extra={"paso": "main"})
            return 1

    if args.comando == "ciclo":
        estado = orquestador.ejecutar_ciclo(origen="manual")
        return 0 if estado in ("ok", "aviso") else 1
    if args.comando == "paso":
        resultado = orquestador.ejecutar_paso(args.nombre, origen="manual")
        return 0 if resultado["estado"] in ("ok", "aviso") else 1
    if args.comando == "pausar":
        orquestador.pausar()
        return 0
    if args.comando == "reanudar":
        orquestador.reanudar()
        return 0
    if args.comando == "servicio":
        from app.servicio import iniciar_servicio

        return iniciar_servicio()
    return 2


if __name__ == "__main__":
    sys.exit(main())
