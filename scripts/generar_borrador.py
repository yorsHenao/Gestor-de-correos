import io
import json
from datetime import date
import locale

from googleapiclient.http import MediaIoBaseUpload

from autenticar import autenticar, servicio_google
import storage
from pathlib import Path
from config import (
    DOC_TEMPLATE_BLOCKED,
    DOC_TEMPLATE_RT_RL,
    DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS,
    DOC_TEMPLATE_RT_WITHOUT_ANEXOS,
    DOC_TEMPLATE_THIRD_PARTY,
    DRIVE_PDF_FOLDER,
    DRIVE_RESPONSES_FOLDER,
    DRIVE_SCREENSHOTS_FOLDER,
)
from utils.logger import log_caso, obtener_log

log = obtener_log()

locale.setlocale(locale.LC_TIME, "Spanish_Colombia.1252")

CARPETA_RESPUESTAS = DRIVE_RESPONSES_FOLDER
CARPETA_PDF_DRIVE = DRIVE_PDF_FOLDER or DRIVE_RESPONSES_FOLDER
CARPETA_CAPTURAS = DRIVE_SCREENSHOTS_FOLDER or DRIVE_RESPONSES_FOLDER
BASE_DIR = Path(__file__).parent.parent
RUTA_CAUSALES = str(BASE_DIR / "data-json/causales-rt.json")
PLACEHOLDER_FOTO = "{{ foto_prueba }}"


def _id_plantilla(formato, con_foto):
    if formato == "terceros":
        return DOC_TEMPLATE_THIRD_PARTY
    if formato == "rt_rl":
        return DOC_TEMPLATE_RT_RL if con_foto else DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS
    return DOC_TEMPLATE_BLOCKED if con_foto else DOC_TEMPLATE_RT_WITHOUT_ANEXOS


def _reemplazar_texto(docs, doc_id, reemplazos):
    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": placeholder, "matchCase": True},
                "replaceText": valor or "",
            }
        }
        for placeholder, valor in reemplazos
    ]
    if requests:
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()


def _texto_parrafo(parrafo):
    """Texto completo del párrafo (une sus runs; un marcador puede venir partido)."""
    return "".join(
        pe.get("textRun", {}).get("content", "") for pe in parrafo.get("elements", [])
    ).strip()


def _salto_pagina_antes(docs, doc_id, marcadores):
    """Inserta un salto de página antes del primer párrafo cuyo texto sea exactamente un marcador.

    La coincidencia es exacta, no por subcadena: el apartado «Material probatorio»
    menciona «el Anexo 1» dentro de su texto y no es el inicio del anexo.
    """
    if isinstance(marcadores, str):
        marcadores = (marcadores,)
    doc = docs.documents().get(documentId=doc_id).execute()
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" not in element:
            continue
        if _texto_parrafo(element["paragraph"]) not in marcadores:
            continue
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{
                "insertPageBreak": {
                    "location": {"index": element["startIndex"]}
                }
            }]},
        ).execute()
        return
    log.warning("Documento: no se encontró el inicio del anexo; va sin salto de página")


def _insertar_imagen_en_doc(docs, drive, doc_id, foto_bytes):
    img_media = MediaIoBaseUpload(io.BytesIO(foto_bytes), mimetype="image/png")
    temp_file = drive.files().create(
        body={"name": "_temp_foto_prueba", "parents": [CARPETA_CAPTURAS]},
        media_body=img_media,
        fields="id",
    ).execute()
    temp_id = temp_file["id"]

    try:
        doc = docs.documents().get(documentId=doc_id).execute()
        index = None

        for element in doc.get("body", {}).get("content", []):
            if "paragraph" not in element:
                continue
            for pe in element["paragraph"].get("elements", []):
                if "textRun" in pe and PLACEHOLDER_FOTO in pe["textRun"]["content"]:
                    index = pe["startIndex"]
                    break
            if index is not None:
                break

        if index is None:
            log.warning("Documento: %s no está en la plantilla; va sin imagen", PLACEHOLDER_FOTO)
            return

        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": index,
                            "endIndex": index + len(PLACEHOLDER_FOTO),
                        }
                    }
                },
                {
                    "insertInlineImage": {
                        "location": {"index": index},
                        "uri": f"https://drive.google.com/uc?id={temp_id}",
                        "objectSize": {
                            "height": {"magnitude": 228, "unit": "PT"},
                            "width": {"magnitude": 380, "unit": "PT"},
                        },
                    }
                },
            ]},
        ).execute()
        log.debug("Documento: imagen insertada")
    finally:
        drive.files().delete(fileId=temp_id).execute()


def _texto_numerales(numerales):
    if len(numerales) == 1:
        return str(numerales[0])
    numerales_str = ", ".join(str(n) for n in numerales[:-1])
    return f"{numerales_str} , {numerales[-1]}"


def _datos_causal(informacion):
    causal_elegida = informacion.get("slack", {}).get("causal_elegida")
    with open(RUTA_CAUSALES, encoding="utf-8") as f:
        causales = json.load(f)
    causal = next((c for c in causales["causales"] if c["id"] == causal_elegida), None)
    if not causal:
        return "", ""
    return causal["descripcion"], _texto_numerales(causal["numerales"])


def _nombre_peticionario(informacion, nombre_rt):
    correo = informacion.get("correo", {})
    return (
        correo.get("nombre_peticionario")
        or correo.get("remitente_nombre")
        or nombre_rt
    ).title()


def _nombre_titulo(informacion, nombre_rt):
    """Tipo 1: nombre de Lupe. Tipos 2 y 3: quien envió el Gmail."""
    if informacion.get("tipo_caso", 1) == 1:
        return nombre_rt
    return _nombre_peticionario(informacion, nombre_rt)


def generar_borrador(ruta_caso, formato="bloqueado", foto_bytes=None):
    informacion = storage.leer_caso(ruta_caso)

    creds = autenticar()
    drive = servicio_google("drive", "v3", creds)
    docs = servicio_google("docs", "v1", creds)

    nombre_rt = informacion["lupe"]["nombre"].title()
    nombre_peticionario = _nombre_peticionario(informacion, nombre_rt)
    nombre_archivo = f"Legal - Respuesta Derecho de petición - {_nombre_titulo(informacion, nombre_rt)}"
    con_foto = bool(foto_bytes)
    plantilla_id = _id_plantilla(formato, con_foto)
    if not plantilla_id:
        raise ValueError(f"Falta ID de plantilla Docs para formato={formato} con_foto={con_foto}")

    copia = drive.files().copy(
        fileId=plantilla_id,
        body={"name": nombre_archivo, "parents": [CARPETA_RESPUESTAS]},
    ).execute()
    doc_id = copia["id"]

    fecha = date.today().strftime("%d de %B de %Y")
    correo = informacion["correo"]
    correo_rt = informacion.get("lupe", {}).get("correo") or correo.get("correo_rt", "")
    correo_peticionario = correo.get("correo_peticionario") or ""

    if formato == "terceros":
        reemplazos = [
            ("{{ fecha }}", fecha),
            ("{{ nombre | title }}", nombre_peticionario),
            ("{{ correo | title }}", correo_peticionario),
            ("{{ peticion }}", correo.get("peticion", "")),
            ("{{ id }}", informacion.get("lupe", {}).get("id") or correo.get("id_rt", "")),
        ]
        _reemplazar_texto(docs, doc_id, reemplazos)
        informacion["doc_id_terceros"] = doc_id
    else:
        descripcion, numerales = _datos_causal(informacion)
        reemplazos = [
            ("{{ fecha }}", fecha),
            ("{{ lupe.nombre | title }}", nombre_rt),
            ("{{ lupe.correo | title }}", correo_rt),
            ("{{ correo.peticion  }}", correo.get("peticion", "")),
            ("{{causales.descripcion }}", descripcion),
            ("{{ causales.numerales }}", numerales),
        ]
        if formato == "rt_rl":
            reemplazos += [
                ("{{ nombre.rl | title }}", nombre_peticionario),
                ("{{ correo.rl | title }}", correo_peticionario),
            ]
        _reemplazar_texto(docs, doc_id, reemplazos)

        if con_foto:
            _salto_pagina_antes(
                docs,
                doc_id,
                ("{% if foto_prueba %}", "Anexo 1", PLACEHOLDER_FOTO),
            )
            _reemplazar_texto(docs, doc_id, [
                ("{% if foto_prueba %}", ""),
                ("{% endif %}", ""),
            ])
            _insertar_imagen_en_doc(docs, drive, doc_id, foto_bytes)

        if formato == "rt_rl":
            informacion["doc_id_rt_rl"] = doc_id
        else:
            informacion["doc_id"] = doc_id

    pdf_bytes = drive.files().export(
        fileId=doc_id,
        mimeType="application/pdf",
    ).execute()

    pdf_media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")
    pdf_file = drive.files().create(
        body={"name": f"{nombre_archivo}.pdf", "parents": [CARPETA_PDF_DRIVE]},
        media_body=pdf_media,
        fields="id",
    ).execute()
    pdf_drive_id = pdf_file["id"]

    if formato == "terceros":
        informacion["pdf_drive_id_terceros"] = pdf_drive_id
    elif formato == "rt_rl":
        informacion["pdf_drive_id_rt_rl"] = pdf_drive_id
    else:
        informacion["pdf_drive_id"] = pdf_drive_id

    storage.guardar_caso(ruta_caso, informacion)

    log_caso(log, ruta_caso).info("Documento generado: %s.pdf (%s)", nombre_archivo, formato)
