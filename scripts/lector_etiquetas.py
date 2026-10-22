from autenticar import autenticar
from googleapiclient.discovery import build

if __name__ == "__main__":
    creds = autenticar()
    servicio = build("gmail", "v1", credentials=creds)
    etiquetas = servicio.users().labels().list(userId="me").execute()
    buscar = ("eliminar", "deuda", "rt")
    print("Etiquetas del buzón (nombre → id):\n")
    for e in sorted(etiquetas["labels"], key=lambda x: x["name"].lower()):
        nombre = e["name"]
        print(f"  {nombre} → {e['id']}")
        if all(fragmento in nombre.lower() for fragmento in buscar):
            print(
                f"    ↳ Copiar en .env: GMAIL_LABEL_ELIMINAR_DEUDA_RTS={e['id']}"
            )