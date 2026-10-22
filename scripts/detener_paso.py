"""Registra parada suave desde otra consola: python scripts/detener_paso.py"""
from flow_control import solicitar_detener

if __name__ == "__main__":
    solicitar_detener()
    print("Parada registrada. El paso en curso termina su caso y se detiene.")
