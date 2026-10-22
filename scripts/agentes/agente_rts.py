"""Entrada de los pasos RT y Slack."""

import sys

from flow_control import DetenerFlujo
from utils.exceptions import LupeOcupado, LupeSesionRequerida


class AgenteRTS:
    """Despacha las etapas de entrada del flujo RTS."""

    def ejecutar_etapa(self, etapa: str):
        """Ejecuta lupe (cola RT) o slack (decisiones de OPS)."""
        if etapa == "lupe":
            from procesar_correos_rt import procesar_correos_rt

            return procesar_correos_rt()
        if etapa == "slack":
            from leer_slack import revisar_slack

            return revisar_slack()
        raise ValueError(f"Etapa RTS no reconocida: {etapa}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python agente_rts.py [lupe|slack]")
    try:
        resultado = AgenteRTS().ejecutar_etapa(sys.argv[1])
    except DetenerFlujo:
        pass
    except (LupeSesionRequerida, LupeOcupado) as error:
        from lupe_navegador import salir_por_lupe

        salir_por_lupe(error)
    else:
        if isinstance(resultado, int) and resultado:
            sys.exit(1)
