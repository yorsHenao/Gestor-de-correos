"""Login manual de Lupe en el perfil persistente.

Abre Chromium con el perfil de Lupe, espera a que se vea el tablero y cierra solo.
Si la sesión ya estaba vigente, cierra de inmediato.
"""

from config import LUPE_LOGIN_MAX_SEG, LUPE_PROFILE_DIR
from flow_control import DetenerFlujo
from lupe_navegador import SesionLupe, salir_por_lupe
from utils.exceptions import LupeOcupado, LupeSesionRequerida


def iniciar_sesion():
    print("Abriendo Lupe con el perfil persistente...")
    print(f"Si pide login, complételo en la ventana (máximo {LUPE_LOGIN_MAX_SEG} s).")
    with SesionLupe(paso="iniciar_sesion", modo="interactivo") as lupe:
        lupe.pagina()
    print(f"Sesión de Lupe lista. Perfil: {LUPE_PROFILE_DIR}")


if __name__ == "__main__":
    try:
        iniciar_sesion()
    except DetenerFlujo:
        pass
    except (LupeSesionRequerida, LupeOcupado) as error:
        salir_por_lupe(error)
