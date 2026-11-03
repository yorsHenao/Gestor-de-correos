import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from googleapiclient.discovery import build
from autenticar import autenticar
import storage
from config import CONTROL_CALIDAD, GMAIL_REPLY_TO
from utils.logger import obtener_log

log = obtener_log()


def cuenta_habilitada(nombre, correo_rt=""):
    return f"""<html>
<body>
<p>Señor/a,<br><strong>{nombre}</strong><br>{correo_rt}</p>
<p>Luego de realizar las verificaciones pertinentes en nuestro sistema, evidenciamos que su cuenta al interior de la Aplicación "Soy Rappi" se encuentra habilitada, tal como se ve reflejado a continuación:.</p>
<p><img src="cid:captura_lupe" alt="Cuenta habilitada"></p>
<p>Teniendo en cuenta que Rappi procedió a cumplir con la primera pretensión de su petición, no resulta necesario pronunciarse sobre las demás pretensiones relacionadas en el derecho de petición.</p>

</body>
</html>"""


def cuenta_habilitada_tercero(nombre_peticionario, nombre_rt, correo_rt=""):
    linea_correo = f"<br>{correo_rt}" if correo_rt else ""
    return f"""<html>
<body>
<p>Señor/a,<br><strong>{nombre_peticionario}</strong></p>
<p>Rappitendero:<br><strong>{nombre_rt}</strong>{linea_correo}</p>
<p>Luego de realizar las verificaciones pertinentes en nuestro sistema, evidenciamos que la cuenta del rappitendero al interior de la Aplicación "Rappi" se encuentra habilitada, tal como se ve reflejado a continuación:</p>
<p><img src="cid:captura_lupe" alt="Cuenta habilitada"></p>
<p>Teniendo en cuenta que Rappi procedió a cumplir con la primera pretensión de su petición, no resulta necesario pronunciarse sobre las demás pretensiones relacionadas en el derecho de petición.</p>

</body>
</html>"""


def cuenta_habilitada_rt_rl(nombre_rt, correo_rt, nombre_rl, correo_rl):
    return f"""<html>
<body>
<p>Señores/as,<br><strong>{nombre_rl}</strong><br>{correo_rl}</p>
<p><strong>{nombre_rt}</strong><br>{correo_rt}</p>
<p>Luego de realizar las verificaciones pertinentes en nuestro sistema, evidenciamos que su cuenta al interior de la Aplicación "Soy Rappi" se encuentra habilitada, tal como se ve reflejado en la imagen adjunta.</p>
<p><img src="cid:captura_lupe" alt="Cuenta habilitada"></p>
<p>Teniendo en cuenta que Rappi procedió a cumplir con la primera pretensión de su petición, no resulta necesario pronunciarse sobre las demás pretensiones relacionadas en el derecho de petición.</p>

</body>
</html>"""


def _cuerpo_original(nombre, tratamiento, correo=""):
    linea_correo = f"<br>\n{correo}" if correo else ""
    return f"""<html>
<body>
<p>{tratamiento}<br>
<strong>{nombre}</strong>{linea_correo}</p>
<p>Por medio de la presente nos permitimos dar respuesta al derecho de petición presentado por usted ante Rappi S.A.S.</p>
<p>Se anexa lo enunciado en el archivo adjunto.</p>
<p>Cordialmente,</p>
</body>
</html>"""


def _cuerpo_rt_rl(nombre_rt, correo_rt, nombre_rl, correo_rl):
    return f"""<html>
<body>
<p>Señores/as,<br><strong>{nombre_rl}</strong><br>{correo_rl}</p>
<p><strong>{nombre_rt}</strong><br>{correo_rt}</p>
<p>Por medio de la presente nos permitimos dar respuesta al derecho de petición presentado por usted ante Rappi S.A.S.</p>
<p>Se anexa lo enunciado en el archivo adjunto.</p>
<p>Cordialmente,</p>
</body>
</html>"""


def _cuerpo_no_encontrado(nombre, correo, dato):
    return f"""<html>
<body>
<p>Estimado/a,<br><strong>{nombre}</strong><br>{correo}</p>
<p>Rappi se encuentra realizando las verificaciones correspondientes respecto de su petición, sin embargo, con el correo o los datos aportados en el escrito de petición - {dato} - no se encuentra una cuenta activa en la Aplicación "Soy Rappi".</p>
<p>De conformidad con lo anterior, le solicitamos la siguiente información:</p>
<ul>
<li>Nombre de registro en la aplicación Soy Rappi</li>
<li>Celular de registro en la aplicación Soy Rappi.</li>
<li>Correo de registro en la aplicación Soy Rappi.</li>
</ul>
<p>Lo anterior para poder brindarle una respuesta de fondo a su solicitud.</p>
<p>Cordialmente,</p>
</body>
</html>"""


def _modo_borrador(crear_borrador=False):
    return crear_borrador or CONTROL_CALIDAD


def enviar_correo_no_encontrado(ruta_caso, dato_busqueda):
    informacion = storage.leer_caso(ruta_caso)

    asunto = informacion["correo"].get("asunto", "Derecho de petición")
    message_id = informacion["correo"]["message_id"]
    thread_id = informacion["correo"]["thread_id_gmail"]
    nombre = informacion["correo"].get("nombre_peticionario") or informacion["correo"].get("remitente_nombre", "")
    correo = informacion["correo"].get("correo_peticionario") or ""

    creds = autenticar()
    servicio = build("gmail", "v1", credentials=creds)

    msg = _construir_mensaje(
        asunto,
        _cuerpo_no_encontrado(nombre, correo, dato_busqueda),
        message_id
    )
    entregar = _crear_borrador if _modo_borrador() else _enviar
    enviado = entregar(servicio, msg, thread_id)
    return enviado


def _construir_mensaje(asunto, cuerpo, message_id, pdf_bytes=None, pdf_nombre=None):
    mensaje = MIMEMultipart()
    mensaje["to"] = GMAIL_REPLY_TO
    mensaje["subject"] = "Re: " + asunto
    mensaje["In-Reply-To"] = message_id
    mensaje["References"] = message_id
    mensaje.attach(MIMEText(cuerpo, "html", "utf-8"))
    if pdf_bytes:
        adjunto = MIMEApplication(pdf_bytes, _subtype="pdf")
        adjunto.add_header("Content-Disposition", "attachment", filename=pdf_nombre or "respuesta.pdf")
        mensaje.attach(adjunto)
    return mensaje


def _construir_mensaje_habilitado(asunto, html_cuerpo, message_id, foto_bytes):
    mensaje = MIMEMultipart("related")
    mensaje["to"] = GMAIL_REPLY_TO
    mensaje["subject"] = "Re: " + asunto
    mensaje["In-Reply-To"] = message_id
    mensaje["References"] = message_id

    alternativa = MIMEMultipart("alternative")
    alternativa.attach(MIMEText(html_cuerpo, "html", "utf-8"))
    mensaje.attach(alternativa)

    imagen = MIMEImage(foto_bytes, _subtype="png")
    imagen.add_header("Content-ID", "<captura_lupe>")
    imagen.add_header("Content-Disposition", "inline", filename="captura_cuenta_habilitada.png")
    mensaje.attach(imagen)
    return mensaje


def enviar_cuenta_habilitada(ruta_caso, foto_bytes, crear_borrador=False):
    informacion = storage.leer_caso(ruta_caso)
    asunto = informacion["correo"].get("asunto", "Derecho de petición")
    message_id = informacion["correo"]["message_id"]
    thread_id = informacion["correo"]["thread_id_gmail"]
    tipo_caso = informacion.get("tipo_caso", 1)
    nombre_rt = informacion["lupe"]["nombre"].title()
    remitente_nombre = (
        informacion["correo"].get("nombre_peticionario") or nombre_rt
    ).title()
    remitente_email = informacion["correo"].get("correo_peticionario") or ""
    correo_rt = informacion.get("lupe", {}).get("correo") or informacion["correo"].get("correo_rt", "")

    servicio = build("gmail", "v1", credentials=autenticar())
    usar_borrador = _modo_borrador(crear_borrador)
    entregar = _crear_borrador if usar_borrador else _enviar

    if tipo_caso == 3:
        html_cuerpo = cuenta_habilitada_rt_rl(
            nombre_rt, correo_rt, remitente_nombre, remitente_email
        )
    elif tipo_caso == 2:
        html_cuerpo = cuenta_habilitada_tercero(
            remitente_nombre, nombre_rt, correo_rt
        )
    else:
        html_cuerpo = cuenta_habilitada(nombre_rt, remitente_email)

    msg = _construir_mensaje_habilitado(asunto, html_cuerpo, message_id, foto_bytes)
    return entregar(servicio, msg, thread_id)


def _enviar(servicio, mensaje, thread_id, quiet=False):
    try:
        raw = base64.urlsafe_b64encode(mensaje.as_bytes()).decode()
        servicio.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id}
        ).execute()
        if not quiet:
            log.debug("Correo enviado a %s", mensaje["to"])
        return True
    except Exception as e:
        log.error("Correo: error enviando a %s: %s", mensaje.get("to", "(desconocido)"), e)
        return False


def _crear_borrador(servicio, mensaje, thread_id):
    try:
        raw = base64.urlsafe_b64encode(mensaje.as_bytes()).decode()
        borrador = servicio.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()
        log.debug("Borrador creado en Gmail: %s", borrador["id"])
        return True
    except Exception as e:
        log.error("Correo: no se pudo crear el borrador: %s", e)
        return False


def _descargar_pdf(drive, file_id):
    return drive.files().get_media(fileId=file_id).execute()


def enviar_correo(ruta_caso, crear_borrador=False):
    informacion = storage.leer_caso(ruta_caso)

    tipo_caso = informacion.get("tipo_caso", 1)
    asunto = informacion["correo"].get("asunto", "Derecho de petición")
    message_id = informacion["correo"]["message_id"]
    thread_id = informacion["correo"]["thread_id_gmail"]
    nombre_rt = informacion["lupe"]["nombre"].title()
    nombre_peticionario = (
        informacion["correo"].get("nombre_peticionario")
        or informacion["correo"].get("remitente_nombre")
        or nombre_rt
    ).title()
    remitente_nombre = nombre_peticionario
    remitente_email = informacion["correo"].get("correo_peticionario") or ""

    creds = autenticar()
    servicio = build("gmail", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    entregar = _crear_borrador if _modo_borrador(crear_borrador) else _enviar
    nombre_titulo = nombre_rt if tipo_caso == 1 else nombre_peticionario
    nombre_pdf = f"Legal - Respuesta Derecho de petición - {nombre_titulo}.pdf"

    if tipo_caso == 3 and informacion.get("pdf_drive_id_rt_rl"):
        pdf_bytes = _descargar_pdf(drive, informacion["pdf_drive_id_rt_rl"])
        cuerpo = _cuerpo_rt_rl(
            nombre_rt,
            informacion.get("lupe", {}).get("correo") or informacion["correo"].get("correo_rt", ""),
            remitente_nombre,
            remitente_email,
        )
    elif informacion.get("pdf_drive_id_terceros"):
        pdf_bytes = _descargar_pdf(drive, informacion["pdf_drive_id_terceros"])
        cuerpo = _cuerpo_original(remitente_nombre, "Señor/a")
    elif informacion.get("pdf_drive_id"):
        pdf_bytes = _descargar_pdf(drive, informacion["pdf_drive_id"])
        cuerpo = _cuerpo_original(nombre_rt, "Señor/a", remitente_email)
    else:
        log.error("Correo: falta el PDF de respuesta (tipo_caso=%s)", tipo_caso)
        return False

    msg = _construir_mensaje(asunto, cuerpo, message_id, pdf_bytes, nombre_pdf)
    return entregar(servicio, msg, thread_id)
