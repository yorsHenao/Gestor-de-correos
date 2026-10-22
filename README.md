# Proyecto E

Gestor de correos de derecho de petición. Clasifica Gmail, consulta Lupe, pide la decisión en Slack y responde. Los casos de clientes finales van al formulario de Entes.

El ciclo sigue este orden: clasificar, RT, Slack, Entes. Clasificar solo lee Gmail. RT es el paso del repartidor con la cuenta bloqueada, y abre Lupe. Entes abre el formulario del cliente final, también con Lupe. Slack usa el canal del `.env`.

## Requisitos

- Python 3.13. En algunos sistemas el comando es `python3`.
- Una cuenta de Google Cloud con Gmail, Drive, Documentos y Hojas de cálculo activados.
- El bot de Slack de este entorno. En esta entrega el token, el canal y los dos usuarios ya vienen en el `.env`.
- Una clave de Groq o de Gemini. Hace falta al menos una. La de Groq se saca en [console.groq.com/keys](https://console.groq.com/keys). La de Gemini, en [Google AI Studio](https://aistudio.google.com/apikey).

## Paso a paso

Hay que seguir este orden.

1. Instalar Python 3.13 y crear el entorno.

   Windows:

   ```
   py -3.13 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   Mac y Linux:

   ```
   python3 -m venv .venv
   source .venv/bin/activate
   python3 -m pip install -r requirements.txt
   ```

2. Activar Gmail, Drive, Documentos y Hojas de cálculo, y guardar el archivo de credenciales en `data-json/credentials.json`. El recorrido, pantalla por pantalla, está en [Configuración de las APIs de Google](#configuración-de-las-apis-de-google).
3. Crear al menos una clave de inteligencia artificial y pegarla en el `.env`. Sin comillas y sin espacios. Las claves de Slack que ya están en el archivo se dejan igual. Los modelos no hay que elegirlos: si `GROQ_MODEL` y `GEMINI_MODEL` quedan vacías, el programa usa los que ya trae.

   Groq: entra a [console.groq.com/keys](https://console.groq.com/keys), inicia sesión y pulsa **Create API Key**. Copia la clave en ese momento, porque después no se vuelve a mostrar completa. En el `.env` agrega `GROQ_API_KEY=` y pega la clave a continuación.

   Gemini: entra a [aistudio.google.com/apikey](https://aistudio.google.com/apikey) con una cuenta de Google y pulsa **Create API key**. Si pide un proyecto, elige uno. Copia la clave y en el `.env` agrega `GEMINI_API_KEY=` seguida de esa clave.

   Si las dos están llenas y `IA_PROVIDER` se deja vacía, el programa empieza por Groq. Si Groq falla, usa Gemini.
4. La carpeta ya trae un `.env` con las claves de Slack. Completarlo con las líneas de `.env.example`, en el mismo orden del ejemplo: hojas y carpetas, etiquetas de Gmail, cartas, correo de respuesta, Slack, claves y, al final, lo que puede quedar vacío. Completar el archivo que ya está. Si se copia el ejemplo encima, se borran las claves de Slack.
5. Crear en Gmail una etiqueta por cada categoría de abajo. Con el entorno activo, correr `python scripts/lector_etiquetas.py`. Sale el nombre de cada etiqueta y, después de la flecha, su id. Ese id es un código. Copiar solo el código y pegarlo después del `=` de la variable, en el grupo de etiquetas del `.env`.

   - RT bloqueado, en `GMAIL_LABEL_ID`. Correo de un repartidor con la cuenta bloqueada.
   - Revisión manual, en `GMAIL_LABEL_REVISION_MANUAL`. Correo que debe ver una persona.
   - Entes, en `GMAIL_LABEL_ENTES`. Reclamo de un cliente final.
   - Otros, en `GMAIL_LABEL_OTROS`.
   - Facturación, en `GMAIL_LABEL_FACTURACION`.
   - Restaurantes, en `GMAIL_LABEL_RESTAURANTES`.
   - Eliminar deuda del repartidor, en `GMAIL_LABEL_ELIMINAR_DEUDA_RTS`.
6. Crear las carpetas de Drive y dos hojas vacías. Sus id van en el grupo de hojas y carpetas del `.env`. El id es el código largo de la dirección del navegador. La primera fila lleva los títulos de abajo, en ese orden. El programa escribe desde la segunda.

   Hoja RT (`DRIVE_SHEETS_ID`): A nombre, B correo del peticionario, C id_rt, D estado, E fecha, F hilo de Slack, G id del mensaje. G identifica la fila. Quien revisa puede escribir en D **Completado** o **Caso manual**. El programa toma ese cierre al empezar la siguiente ronda.

   Hoja Entes (`DRIVE_SHEETS_ENTES_ID`): A peticionario, B correo, C id de usuario, D fecha, E causal, F estado, G id del mensaje, H completado. G identifica la fila. H la escribe quien revisa, y el programa la deja como está.

   La carpeta de respuestas es `DRIVE_RESPONSES_FOLDER`. `DRIVE_PDF_FOLDER` y `DRIVE_SCREENSHOTS_FOLDER` pueden quedar vacías: el programa usa la carpeta de respuestas.
7. Subir cada archivo de `formatos/` a Drive y abrirlo como Documento de Google. No modificar las llaves que ya trae el documento. El id de cada archivo va en el grupo de cartas del `.env`, en su `DOC_TEMPLATE_*`. La carpeta de las cartas no se configura.
8. `python main.py login`. Se abre Lupe en este computador. La persona entra con Google y espera a ver la pantalla principal. El perfil queda fuera del proyecto, en `%LOCALAPPDATA%\ProyectoE\lupe_profile`, salvo que `LUPE_PROFILE_DIR` diga otra carpeta. Si Google bloquea el navegador, poner `LUPE_CHANNEL=chrome` y volver a correr el login.
9. `python main.py servicio`. Abre la página del programa en http://127.0.0.1:8000, salvo que `DASHBOARD_PUERTO` sea otro. El ciclo se repite cada `CICLO_HORAS`. Si esa línea está vacía, son 4 horas.

La primera vez que corre, el programa crea `data/runs.db` en este computador. Ahí guarda los casos de RT que siguen abiertos, los de Entes que quedaron sin cerrar y la hora del último ciclo. Con eso retoma el trabajo si se detiene.

## Configuración de las APIs de Google

El programa usa cuatro APIs de Google. Los permisos (scopes) no se activan en Cloud con un interruptor: ya están escritos en el código, en `scripts/autenticar.py`. En Cloud solo hay que declararlos para que el primer ingreso desde el navegador pueda pedirlos.

### 1. Entrar a Google Cloud

Entra a [console.cloud.google.com](https://console.cloud.google.com/) con la cuenta de Google que va a usar el programa. Arriba elige el proyecto, o crea uno si todavía no existe.

### 2. Habilitar las APIs

Menú **APIs y servicios → Biblioteca**. Busca cada una, ábrela y pulsa **Habilitar**:

- Gmail API
- Google Drive API
- Google Docs API
- Google Sheets API

Para verificar, entra a **APIs y servicios → APIs y servicios habilitados**: deben aparecer las cuatro.

### 3. Configurar Google Auth Platform (pantalla de consentimiento)

Google renombró esta sección. Según la versión de la consola aparece como **Pantalla de consentimiento de OAuth** o como **Google Auth Platform**. Si no ves el primer nombre, busca el segundo.

Si es un proyecto nuevo, aparece el mensaje "Aún no se configuró Google Auth Platform". Pulsa **Comenzar** y completa el asistente de 4 pasos:

1. **Información de la aplicación**: nombre de la app (por ejemplo "Gestor RTS") y correo de asistencia.
2. **Público**: elige **Externo** si la cuenta no es de un Google Workspace de la empresa. (En un Workspace aparece también **Interno**, que no exige usuarios de prueba.)
3. **Información de contacto**: un correo donde Google pueda avisar de cambios en el proyecto.
4. **Finalizar**: acepta la política de datos de usuario y pulsa **Crear**.

Al terminar, la configuración queda repartida en las pestañas del menú lateral.

### 4. Agregar el usuario de prueba

En el menú lateral entra a **Público**. En **Usuarios de prueba**, agrega el correo de Gmail que va a clasificar los mensajes. Mientras la aplicación esté en estado **Prueba**, solo esas personas pueden aceptar los permisos.

### 5. Agregar los scopes

En el menú lateral entra a **Acceso a los datos** y agrega estos seis (los que pide el programa):

- `https://www.googleapis.com/auth/gmail.readonly`: leer correos
- `https://www.googleapis.com/auth/gmail.send`: enviar correos
- `https://www.googleapis.com/auth/gmail.modify`: cambiar etiquetas y marcar como leído
- `https://www.googleapis.com/auth/drive`: carpetas y archivos
- `https://www.googleapis.com/auth/documents`: las cartas
- `https://www.googleapis.com/auth/spreadsheets`: las hojas

No hace falta publicar ni verificar la aplicación si el correo está como usuario de prueba.

### 6. Crear el cliente OAuth y descargar las credenciales

1. En el menú lateral entra a **Clientes**. Si aparece "Aún no configuraste ningún cliente de OAuth", es lo esperado.
2. Pulsa **Crear cliente**.
3. En **Tipo de aplicación**, elige **Aplicación de escritorio**.
4. Ponle un nombre (por ejemplo "Gestor RTS escritorio"). Es solo una etiqueta interna.
5. Pulsa **Crear**.
6. En la ventana que aparece, descarga el JSON en ese momento. En las consolas nuevas el secreto del cliente solo se muestra completo al crearlo; si cierras la ventana sin descargarlo, hay que crear un cliente nuevo o generar un secreto adicional.
7. Google lo descarga con un nombre tipo `client_secret_xxxx.json`. Renómbralo a `credentials.json` y guárdalo en `data-json/credentials.json`.

Ese archivo no se pega en el `.env`: `GOOGLE_CREDENTIALS_FILE` se deja vacía.

`credentials.json` es un secreto. Ya está en el `.gitignore`, junto con `token.json`, para que no se suba. Cada persona genera el suyo siguiendo esta guía.

### 7. Primer ingreso

La primera vez que el programa llama a Google, se abre el navegador. Entra con la cuenta que agregaste como usuario de prueba y acepta los permisos. El programa crea `data-json/token.json`.

Si Google avisa que la aplicación no está verificada, pulsa **Configuración avanzada** y continúa. Es normal mientras la aplicación está en estado Prueba.

## Dashboard

Es la página del programa en este computador: http://127.0.0.1:8000. Arriba muestra cuándo es el próximo ciclo y si la entrada a Lupe sigue vigente. Desde ahí se puede lanzar el ciclo, pausarlo o reanudarlo, y también correr un solo paso: clasificar, RT, Slack o Entes. Abajo se ve cuántos casos de RT y de Entes hay en cada estado.

**Borrador** deja la respuesta en Gmail, sin enviarla. **Enviar** la manda. Ese cambio se hace en pausa, o cuando no hay un ciclo corriendo. `CONTROL_CALIDAD` guarda la elección: `true` es borrador y `false` es envío.

**Confirmar** detiene el ciclo después de clasificar, hasta pulsar **Continuar**. Así se pueden revisar las etiquetas antes de seguir. **Continuo**, o dejar `REVISION_CLASIFICACION` vacía, sigue solo. Si clasificar termina con error y la confirmación está activa, el ciclo se detiene.

## Lo que ya viene con el proyecto

**Archivos**

- `data-json/causales-rt.json`. Trae el texto de cada motivo por el que se niega la petición. El programa lo copia en la carta para explicar por qué no se levanta el bloqueo, sin redactarlo cada vez.
- `data-json/mapeo-causales.json`. Une el motivo que muestra Lupe con el texto de esa carta. Así la respuesta usa la razón correcta. Si Lupe trae un motivo que no está en este archivo, el caso lo revisa una persona.
- `formatos/`. Son las cinco cartas modelo. El programa las llena con el nombre, la fecha y el motivo, en vez de escribir cada respuesta a mano.

**Formulario de Entes**

Es la página donde se registran los casos de clientes finales. Viene preparado para Colombia: el país está escrito como `co` en `scripts/entes/formulario.py`. Si se usa en otro país, se cambia ese valor ahí. La dirección de la página es `URL_FORMULARIO`. Si queda vacía, se usa la que viaja con el proyecto.

**Correos que revisa una persona**

Al clasificar, `recepcion.hub@rappi.com` siempre queda para que lo vea una persona. `CORREOS_REVISION_MANUAL` suma otros correos, separados por coma. Si queda vacía, solo entra ese. Esto no cambia el formulario de Entes.

**Direcciones**

`URL_LUPE`, si está vacía, usa la dirección de Lupe que viaja con el proyecto.

**Inteligencia artificial**

`IA_PROVIDER` vacío empieza por Groq. Si Groq falla, se usa Gemini. Puede escribirse `groq` o `gemini`. Los modelos no hay que buscarlos: si `GROQ_MODEL` y `GEMINI_MODEL` se dejan vacíos, quedan `openai/gpt-oss-120b` y `gemini-2.5-flash`.

**Hay que llenarlos**

Sin estos, el programa no arranca. Van en los grupos del `.env` con el mismo nombre.

- `DRIVE_SHEETS_ID` y `DRIVE_RESPONSES_FOLDER`: la hoja de RT y la carpeta de respuestas.
- `GMAIL_LABEL_ID`: la etiqueta de cuenta bloqueada.
- `GMAIL_REPLY_TO`: el correo al que se responde.
- `SLACK_REVIEW_CHANNEL_ID`: el canal de Slack.
- Una clave de Groq o de Gemini.

## Estructura

- `main.py`: entrar a Lupe, abrir la página, correr el ciclo o un solo paso.
- `app/`: el ciclo, el servicio y la página.
- `scripts/`: clasificar, RT, Slack, Entes, Lupe y las cartas.
- `data-json/`: los textos de los motivos y el archivo que los une con Lupe. También van aquí las credenciales, que no vienen en la copia.
- `formatos/`: las cinco cartas para subir a Drive.

© 2026 Jeisembert Yors Castiblanco Henao. Todos los derechos reservados.
