#!/usr/bin/env python3
"""
Borra un mensaje del canal de revisión.

Antes de correrlo, escribe en TS el timestamp del mensaje.
En el enlace va después de la p; el punto se coloca antes de los últimos seis dígitos.
"""

import sys
from pathlib import Path
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

from config import SLACK_BOT_TOKEN, SLACK_REVIEW_CHANNEL_ID

TS = ""

def main():
    if not TS.strip():
        print("Escribe en TS el timestamp del mensaje y vuelve a correr el script.")
        sys.exit(1)
    client = WebClient(token=SLACK_BOT_TOKEN)
    
    try:
        response = client.chat_delete(
            channel=SLACK_REVIEW_CHANNEL_ID,
            ts=TS
        )
        if response["ok"]:
            print("[OK] Mensaje eliminado correctamente")
            print(f"   Canal: {SLACK_REVIEW_CHANNEL_ID}")
            print(f"   TS: {TS}")
        else:
            print(f"[ERROR] Error: {response.get('error', 'desconocido')}")
    except SlackApiError as e:
        print(f"[ERROR] Error de API Slack: {e.response['error']}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Error inesperado: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()