# Proyecto E — Gestor Automatizado de Derechos de Petición

Sistema automatizado para la recepción, clasificación con Inteligencia Artificial, consulta en **Lupe**, coordinación operativa en **Slack**, generación de respuestas legales en **Google Docs / PDF** y radicación de casos de usuarios finales en el formulario de **Entes**.

Incluye un orquestador por línea de comandos (`main.py`), un servicio programado con **Dashboard Web** local (`FastAPI` + `APScheduler`) y un **Panel de Control de Escritorio para Windows** (`deploy/`) con arranque automático y bandeja del sistema.

---

## Tabla de Contenidos

1. [Descripción General y Flujo del Ciclo](#-1-descripción-general-y-flujo-del-ciclo)
2. [Arquitectura y Estructura del Proyecto](#-2-arquitectura-y-estructura-del-proyecto)
3. [Requisitos Previos](#-3-requisitos-previos)
4. [Instalación (INSTALAR.bat) y Actualización (ACTUALIZAR.bat)](#-4-instalación-instalarbat-y-actualización-actualizarbat)
5. [Configuración de Google Workspace y Google Drive (Carpetas, Hojas y Plantillas)](#-5-configuración-de-google-workspace-y-google-drive-carpetas-hojas-y-plantillas)
6. [Configuración de Variables de Entorno (`.env`)](#-6-configuración-de-variables-de-entorno-env)
7. [Autenticación Inicial (Google OAuth y Sesión de Lupe)](#-7-autenticación-inicial-google-oauth-y-sesión-de-lupe)
8. [Uso y Comandos (`main.py`)](#-8-uso-y-comandos-mainpy)
9. [Dashboard Web y Controles Operativos](#-9-dashboard-web-y-controles-operativos)
10. [Despliegue e Instalación en Windows (Panel de Escritorio)](#-10-despliegue-e-instalación-en-windows-panel-de-escritorio)
11. [Base de Datos Local, Logs y Códigos de Salida](#-11-base-de-datos-local-logs-y-códigos-de-salida)
12. [Solución de Problemas Frecuentes](#-12-solución-de-problemas-frecuentes)

---

## 🔄 1. Descripción General y Flujo del Ciclo

El sistema ejecuta un ciclo secuencial compuesto por cuatro etapas en orden fijo (**`clasificar → rt → slack → entes`**):

| Orden | Paso | Script Principal | Qué hace |
| :---: | :--- | :--- | :--- |
| **1** | `clasificar` | `scripts/clasificar_correos_gmail.py` | Lee los correos sin leer del buzón de Gmail, extrae el cuerpo y los adjuntos (PDF, Word `.docx` e imágenes mediante OCR local con `rapidocr-onnxruntime`) y los clasifica con IA (`Groq` o `Gemini`) aplicando la etiqueta correspondiente en Gmail (*RT bloqueado*, *Entes*, *Eliminar deudas RTs*, *Facturación*, *Restaurantes*, *Otros* o *Revisión manual*). |
| **2** | `rt` | `scripts/agentes/agente_rts.py lupe` (`scripts/procesar_correos_rt.py`) | Toma los correos sin leer etiquetados como **RT bloqueado** (`GMAIL_LABEL_ID`), descarta falsos positivos, extrae los datos del Rappitendero con IA, evita duplicados del mismo `id_rt` y consulta la cuenta en **Lupe** por orden de prioridad (*ID RT → Cédula → Correo → Teléfono*). Si la cuenta no existe, responde pidiendo datos de registro; si está habilitada, toma captura y responde; si el ID corresponde a otra persona y está habilitada, genera el documento de terceros; y si está bloqueada, registra el caso en la hoja de Google Sheets (`DRIVE_SHEETS_ID`) y lo publica en **Slack** para decisión de OPS. |
| **3** | `slack` | `scripts/agentes/agente_rts.py slack` (`scripts/leer_slack.py`) | Sincroniza cierres manuales hechos por QA en Google Sheets y revisa los hilos de Slack en estado `En Slack - pendiente`. Interpreta con IA la decisión de OPS: si se **mantiene el bloqueo**, mapea la regla con `data-json/mapeo-causales.json`, crea el documento legal en Drive a partir de la plantilla de Google Docs (con o sin imagen de prueba), lo exporta a PDF en Drive y responde en el hilo de Gmail; si se **habilita la cuenta**, verifica en Lupe el estado activo, toma captura de pantalla y envía la respuesta con la evidencia embebida. |
| **4** | `entes` | `scripts/procesar_entes.py` | Procesa la cola de correos sin leer con etiqueta **Entes** (`GMAIL_LABEL_ENTES`). Extrae datos del peticionario y adjuntos con IA, consulta al usuario y sus órdenes en **Lupe**, si no encuentra la cuenta envía igual el formulario con ID Orden e ID Usuario en 0 y el valor que indique el correo (o 0); los casos con teléfono de otro país van a revisión manual desde el clasificador. Imprime el correo/adjuntos a PDF en una carpeta temporal local (máx. 10 archivos y `< 50 MB`) para diligenciar y radicar automáticamente el formulario web de **Entes**, actualizando la hoja de seguimiento (`DRIVE_SHEETS_ENTES_ID`). |

---

## 🗂️ 2. Arquitectura y Estructura del Proyecto

```text
Proyecto_E/
├── main.py                           # Punto de entrada CLI (ciclo, paso, servicio, login, pausar, reanudar)
├── requirements.txt                  # Dependencias de Python
├── .env                              # Variables de entorno (no versionado en Git)
├── app/                              # Orquestador, servicio en segundo plano y servidor web
│   ├── orquestador.py                # Ejecución ordenada de pasos, candados (FileLock), pausa y paradas
│   ├── servicio.py                   # Proceso único que levanta APScheduler + Uvicorn (FastAPI)
│   ├── scheduler.py                  # Programación cada CICLO_HORAS con recuperación de ciclos perdidos
│   ├── registro.py                   # Capa de persistencia SQLite (data/runs.db)
│   ├── ajustes.py                    # Edición en caliente de CONTROL_CALIDAD y REVISION_CLASIFICACION
│   ├── alertas.py                    # Notificaciones por Windows Toast, Gmail y Slack
│   └── dashboard/                    # Aplicación web FastAPI y plantillas Jinja2
│       ├── web.py                    # Rutas del panel web (/ y /registro) y endpoints de acciones POST
│       └── templates/                # Vistas HTML (base.html, inicio.html, registro.html)
├── scripts/                          # Módulos operativos de negocio e integraciones
│   ├── config.py                     # Carga centralizada del .env y valores por defecto
│   ├── autenticar.py                 # Flujo OAuth 2.0 de Google (Gmail, Drive, Docs, Sheets)
│   ├── iniciar_sesion.py             # Inicio de sesión interactivo de Lupe sobre perfil persistente
│   ├── lector_etiquetas.py           # Utilidad para listar etiquetas del buzón de Gmail y sus IDs
│   ├── clasificar_correos_gmail.py   # Paso 1: clasificación automática de correos entrantes
│   ├── procesar_correos_rt.py        # Paso 2: extracción de datos RT, consulta en Lupe y publicación en Slack
│   ├── consultar_lupe.py             # Navegación y extracción de información de RT en Lupe
│   ├── leer_slack.py                 # Paso 3: lectura de decisiones de OPS en Slack y despacho de respuesta
│   ├── generar_borrador.py           # Creación de documento legal en Google Docs y exportación a PDF
│   ├── enviar_correo.py              # Envío directo o creación de borrador en Gmail
│   ├── procesar_entes.py             # Paso 4: flujo completo de casos de clientes finales (Entes)
│   ├── lupe_navegador.py             # Gestor de contexto Chromium persistente y candado entre procesos
│   ├── storage.py                    # Sincronización entre SQLite (data/runs.db) y Google Sheets
│   ├── gmail_cuerpo.py               # Extracción de texto HTML/plano, PDFs, DOCX e imágenes con OCR
│   ├── flow_control.py               # Señales de parada limpia entre casos (logs/detener.flujo)
│   ├── agentes/                      # Agentes de IA (Groq / Gemini con fallback automático)
│   │   ├── agente_clasificador.py    # Clasificación semántica de correos
│   │   ├── agente_extractor_rt.py    # Extracción estructurada de datos del Rappitendero
│   │   ├── agente_rts.py             # Despachador de etapas RTS (lupe | slack)
│   │   ├── agente_slack.py           # Interpretación de respuestas y decisiones de OPS en Slack
│   │   └── agente_entes.py           # Extracción de información y causales para casos de Entes
│   └── entes/                        # Submódulos específicos del flujo de Entes
│       ├── correo.py                 # Lectura de mensajes y extracción de fechas/remitentes
│       ├── formulario.py             # Armado de campos, impresión de correo a PDF y envío en Playwright
│       ├── hoja.py                   # Lectura y escritura en el Google Sheet de Entes
│       ├── imagenes.py               # OCR y análisis de capturas adjuntas en correos de Entes
│       ├── lupe.py                   # Búsqueda de usuarios y órdenes en Lupe para Entes
├── utils/                            # Utilidades transversales
│   ├── config_manager.py             # Validación de variables críticas antes de iniciar pasos o servicio
│   ├── logger.py                     # Formato unificado de logs en consola y en logs/proyecto_e.log
│   └── exceptions.py                 # Excepciones de dominio (LupeSesionRequerida, LupeOcupado, etc.)
├── data-json/                        # Catálogos estáticos y credenciales OAuth locales
│   ├── causales-rt.json              # Textos legales y numerales por causal de bloqueo RT
│   ├── mapeo-causales.json           # Mapeo {"regla_lupe": id_causal}
│   ├── credentials.json              # Credenciales OAuth de cliente Google (no versionado)
│   └── token.json                    # Token OAuth generado tras autorizar (no versionado)
└── deploy/                           # Empaquetado de escritorio para Windows (1 clic, sin permisos de Admin)
    ├── INSTALAR_GESTOR_CORREOS.bat   # Instalador automático (acceso directo + inicio con Windows)
    ├── DESINSTALAR_GESTOR_CORREOS.bat# Desinstalador del arranque automático
    ├── instalar_gestor_correos.ps1   # Script PowerShell de aprovisionamiento
    ├── gestor_correos_control.pyw    # Interfaz gráfica de escritorio (Tkinter) + bandeja del sistema
    ├── gestor_correos.ico            # Ícono de la aplicación
```

> **Nota:** Las carpetas `data/` (base de datos local `runs.db` y candados `.lock`) y `logs/` (`proyecto_e.log`, estado de sesión de Lupe y banderas de control) se crean automáticamente en la primera ejecución y están excluidas en `.gitignore`.

---

## 💻 3. Requisitos Previos

Antes de correr `INSTALAR.bat`, cada persona necesita:

1. **Windows 10 / 11.**
2. **Python 3.13 de 64 bits**, solicitado **por ticket** a Soporte/IT (el instalador no lo instala). Pide que quede con la opción *Add python.exe to PATH* o con el lanzador `py`.
3. **Git**, también por ticket si no lo tienes (`git --version` debe responder).
4. **Acceso de lectura al repositorio** para poder clonarlo y luego actualizar con `git pull`.
5. **Tu propio `credentials.json`**: el JSON del cliente OAuth de tipo *Aplicación de escritorio* descargado de Google Cloud (ver [5.1](#51-proyecto-en-google-cloud-y-credentialsjson)). Guárdalo en `data-json/credentials.json`.
6. **Tus claves de IA**: al menos una de **Groq** (`gsk_...`) o **Google Gemini** (`AIza...` o `AQ....`, según la cuenta de AI Studio). Son personales.
7. **El token del bot de Slack** (`xoxb-...`), que **Jeisembert entrega personalmente** a cada persona. No se comparte por chat ni se guarda en el repositorio.
8. Acceso a **Lupe** (`https://lupe.rappi.com/`) con tu cuenta de Google.

> El canal de Slack y los usuarios que se etiquetan (`SLACK_REVIEW_CHANNEL_*`, `USER_FRAUD`, `USER_DISCIPLINE`) son globales y ya vienen en `.env.example`.

---

## 🚀 4. Instalación (INSTALAR.bat) y Actualización (ACTUALIZAR.bat)

### 4.1. Instalación recomendada

```powershell
git clone https://github.com/yorsHenao/proyecto_E.git
cd proyecto_E
```

Luego haz **doble clic en `INSTALAR.bat`** (en la raíz del proyecto). Solo prepara el entorno de Python. Cada paso muestra `[OK]` o `[FALTA]`. **Se puede correr las veces que quieras:** si `.venv` ya es Python 3.13 no lo recrea; vuelve a instalar dependencias y Chromium. Si algo falla, termina con `INSTALACION INCOMPLETA`.

| # | Paso | Automático / Manual |
| :---: | :--- | :--- |
| 1 | Revisa que existan Git, la carpeta clonada con Git y Python 3.13. Si falta Python, explica cómo pedirlo por ticket y se detiene. | Automático (la solicitud del ticket es manual) |
| 2 | Crea `.venv`, instala `requirements.txt` y el Chromium de Playwright. | Automático |

El `.env`, Google, Drive, Lupe y el acceso directo **no** los hace `INSTALAR.bat`. Hazlos a mano después (secciones 5, 6, 7 y 10):

1. Copia `.env.example` a `.env` y completa Slack, `GMAIL_REPLY_TO` y al menos una clave de IA.
2. Pon `credentials.json` en `data-json/`.
3. Crea en Gmail las etiquetas del programa (nombres en `instalador/etiquetas.json`) y en Drive la carpeta `legal-ops`, las 2 hojas y las 5 plantillas; pon sus IDs en `.env`.
4. Autoriza Google y lista etiquetas: `.venv\Scripts\python.exe scripts\lector_etiquetas.py`.
5. Inicia sesión en Lupe: `.venv\Scripts\python.exe main.py login`.
6. Con el `.env` listo, corre `deploy\INSTALAR_GESTOR_CORREOS.bat` para el icono y el arranque.

En VS Code o Cursor, al abrir el proyecto una terminal nueva queda con `.venv` activo (`.vscode/settings.json`). Fuera del editor, usa siempre `.venv\Scripts\python.exe` (no hace falta `Activate`).

**Etiquetas de Gmail** (nombres en `instalador/etiquetas.json`):

| Variable | Etiqueta |
| :--- | :--- |
| `GMAIL_LABEL_ID` | `RTs-desbloqueo-cuentas` |
| `GMAIL_LABEL_REVISION_MANUAL` | `Revisión-Manual` |
| `GMAIL_LABEL_FACTURACION` | `Revisión-Manual` (la misma de revisión manual) |
| `GMAIL_LABEL_ENTES` | `Entes` |
| `GMAIL_LABEL_OTROS` | `otros` |
| `GMAIL_LABEL_RESTAURANTES` | `Restaurantes` |
| `GMAIL_LABEL_ELIMINAR_DEUDA_RTS` | `Eliminar_deuda_rts` |

> Si ya tenías una etiqueta con otro nombre para un uso parecido, mueve esos correos a la etiqueta nueva desde Gmail.

### 4.2. Cómo leer el diagnóstico

Puedes correrlo cuando quieras; solo lee, no cambia nada:

```powershell
.venv\Scripts\python.exe scripts\diagnostico.py
```

Revisa internet, `.env`, token y permisos de Google, etiquetas de Gmail, carpetas, hojas y encabezados de Drive, plantillas y sus marcadores `{{ }}` / `{% %}`, el bot y el canal de Slack, Groq y Gemini (con una consulta mínima), Chromium y la sesión de Lupe.

- `[OK]`: todo bien.
- `[AVISO]`: funciona, pero conviene revisarlo (por ejemplo, un límite de uso `429` en la IA o encabezados distintos en una hoja).
- `[FALTA]`: hay que arreglarlo. Debajo aparece **cómo**. En las claves de IA distingue clave inválida (`401`), límite de uso (`429`) y modelo inexistente (`404`).

Termina con código `0` si no hay ningún `[FALTA]`.

### 4.3. Actualizar a una versión nueva

Haz **doble clic en `ACTUALIZAR.bat`**. Hace lo siguiente:

1. Se detiene si hay cambios locales en archivos del repositorio (para no perderlos).
2. Apaga el servicio.
3. Respalda `.env`, `token.json`, `credentials.json` y `data/` en `%LOCALAPPDATA%\ProyectoE\respaldos\<fecha>`.
4. Ejecuta `git pull --ff-only`.
5. Reinstala dependencias y Chromium, y agrega a `.env` las variables nuevas de `.env.example` sin tocar las tuyas.
6. Corre el diagnóstico y vuelve a prender el servicio.

### 4.4. Alternativa avanzada: instalación manual


#### 1. Clonar el repositorio

```bash
git clone https://github.com/yorsHenao/proyecto_E.git
cd proyecto_E
```

#### 2. Crear y activar el entorno virtual

En **Windows (PowerShell)**:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

En **Linux / macOS**:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### 3. Instalar las dependencias y el navegador de Playwright

```bash
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

---

## ☁️ 5. Configuración de Google Workspace y Google Drive (Carpetas, Hojas y Plantillas)

Esta sección explica qué crear en Drive y qué IDs poner en `.env`. Todo se hace a mano (o puedes correr `python -m instalador.instalar` si quieres la creación automática).

---

### 5.1. Proyecto en Google Cloud y `credentials.json`

1. En [Google Cloud Console](https://console.cloud.google.com/), crea o selecciona el proyecto y habilita las siguientes **4 APIs**:
   - **Gmail API**
   - **Google Drive API**
   - **Google Docs API**
   - **Google Sheets API**
2. Crea una credencial **OAuth 2.0 Client ID** de tipo **Aplicación de escritorio (Desktop app)**.
3. Descarga el archivo JSON de credenciales y guárdalo en:
   ```text
   data-json/credentials.json
   ```
   *(O indica una ruta distinta mediante la variable `GOOGLE_CREDENTIALS_FILE` en el `.env`).*

---

### 5.2. Estructura Sugerida en Google Drive

Para mantener todo ordenado y fácil de administrar, usa en Google Drive esta estructura:

```text
📁 legal-ops/
├── 📁 01_Plantillas_Docs/                      # Las 5 plantillas (fuente: plantillas/*.docx del repo)
│   ├── 📄 Formato_Respuesta_Derecho_de_Peticion_Rt_bloqueado        → DOC_TEMPLATE_BLOCKED
│   ├── 📄 Formato_Respuesta_Derecho_de_Peticion_rt_sin_anexos       → DOC_TEMPLATE_RT_WITHOUT_ANEXOS
│   ├── 📄 Formato_Respuesta_Derecho_de_Peticion_Rt_RL_bloqueado     → DOC_TEMPLATE_RT_RL
│   ├── 📄 Formato_Respuesta_Derecho_de_Peticion_rt_rl_sin_anexos    → DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS
│   └── 📄 Formato_Respuesta_Derecho_de_Peticion_Terceros           → DOC_TEMPLATE_THIRD_PARTY
│
├── 📁 02_Hojas_de_Control/                     # Contiene los 2 archivos de Google Sheets
│   ├── 📊 Control de Casos RT                             → DRIVE_SHEETS_ID
│   └── 📊 Control de Casos Entes                          → DRIVE_SHEETS_ENTES_ID
│
├── 📁 03_Respuestas_Docs/                      # Carpeta destino de los Google Docs generados → DRIVE_RESPONSES_FOLDER
├── 📁 04_Respuestas_PDF/                       # (Opcional) Carpeta destino de los PDFs finales → DRIVE_PDF_FOLDER
└── 📁 05_Capturas_Temporales/                  # (Opcional) Carpeta de paso para imágenes → DRIVE_SCREENSHOTS_FOLDER
```

---

### 5.3. ¿Qué Carpetas se deben crear en Google Drive y para qué sirve cada una?

El módulo `scripts/generar_borrador.py` utiliza hasta **tres variables de carpeta** en Google Drive. Una es **obligatoria** y dos son **opcionales**:

| Variable en `.env` | ¿Obligatoria? | ¿Para qué se debe crear y qué guarda el sistema allí? | ¿En qué paso se usa? |
| :--- | :---: | :--- | :--- |
| **`DRIVE_RESPONSES_FOLDER`** | **Sí (Crítica)** | **Carpeta de documentos de respuesta (Google Docs):** Cada vez que el bot debe redactar una respuesta formal de derecho de petición para un caso RT, hace una copia (`drive.files().copy`) de la plantilla de Google Docs correspondiente dentro de esta carpeta bajo el nombre:<br>`Legal - Respuesta Derecho de petición - <Nombre del RT o Peticionario>`<br>Allí reemplaza todos los marcadores `{{ ... }}` con los datos reales del caso. Además, si no se configuran las dos carpetas opcionales de abajo, esta misma carpeta recibe también los PDFs exportados y las fotos temporales. | Paso `slack` (cuando OPS mantiene el bloqueo) y Paso `rt` (cuando el ID buscado en Lupe pertenece a un tercero con cuenta habilitada). |
| **`DRIVE_PDF_FOLDER`** | Opcional *(por defecto usa `DRIVE_RESPONSES_FOLDER`)* | **Carpeta de archivos PDF definitivos:** Una vez rellenado el Google Doc del caso, el sistema lo exporta a formato PDF (`application/pdf`) y sube el archivo `<Nombre>.pdf` a esta carpeta. Posteriormente, `scripts/enviar_correo.py` descarga ese PDF desde Drive mediante su `fileId` (`pdf_drive_id`, `pdf_drive_id_rt_rl` o `pdf_drive_id_terceros`) y lo adjunta al correo o borrador de respuesta en el hilo de Gmail. Crearla por separado permite tener los PDFs listos para auditoría sin mezclarlos con los Docs editables. | Paso `slack` y Paso `rt` (al exportar el Doc y al adjuntar el PDF en Gmail). |
| **`DRIVE_SCREENSHOTS_FOLDER`** | Opcional *(por defecto usa `DRIVE_RESPONSES_FOLDER`)* | **Carpeta de paso para inserción de imágenes de prueba:** La API de Google Docs (`insertInlineImage`) exige que una imagen exista en Drive con una URI accesible antes de poder incrustarla dentro de un documento. Cuando OPS adjunta una captura de evidencia en el hilo de Slack, el bot la sube temporalmente a esta carpeta como `_temp_foto_prueba`, la inserta en el lugar del marcador `{{ foto_prueba }}` (con tamaño `380x228 PT` y salto de página previo en el Anexo 1) y **elimina automáticamente el archivo temporal en el bloque `finally`** (`drive.files().delete`). | Paso `slack` (solo en casos con bloqueo mantenido que llevan foto de evidencia). |

> **Nota sobre los archivos del paso `entes`:** El flujo de **Entes** (`scripts/procesar_entes.py` y `scripts/entes/formulario.py`) **no sube carpetas ni PDFs a Google Drive**. Los correos impresos (`correo.pdf`) y sus PDFs adjuntos se arman en un directorio temporal local del sistema (`tempfile.TemporaryDirectory()`), se cargan directamente en el formulario web de Entes mediante Playwright y se limpian automáticamente al terminar la ronda. En Google Drive, Entes únicamente lee y actualiza su hoja de cálculo (`DRIVE_SHEETS_ENTES_ID`).

#### Cómo obtener el ID de una carpeta de Google Drive
Abre la carpeta en tu navegador y copia la cadena alfanumérica que aparece después de `/folders/` en la barra de direcciones:
```text
https://drive.google.com/drive/folders/1aBcDeFgHiJkLmNoPqRsTuVwXyZ012345
                                       └──────────────┬───────────────┘
                                              ID de la carpeta
```

---

### 5.4. ¿Qué Hojas de Cálculo (Google Sheets) se deben crear y para qué sirven?

Debes crear **dos archivos de Google Sheets** en blanco. La primera fila (fila 1) puede tener los títulos de las columnas; el sistema escribe y actualiza filas a partir de los datos.

#### Cómo obtener el ID de un archivo de Google Sheets
Abre la hoja de cálculo en el navegador y copia el valor que está entre `/spreadsheets/d/` y `/edit`:
```text
https://docs.google.com/spreadsheets/d/1XyZ987654321aBcDeFgHiJkLmNoPqRsTuVw/edit#gid=0
                                       └─────────────────┬────────────────┘
                                                 ID de la hoja
```

---

#### 1. Hoja de Control de Casos **RT** (`DRIVE_SHEETS_ID` — **Obligatoria**)

- **Para qué se debe crear:**
  1. **Trazabilidad operativa de cada caso RT:** Cada vez que `scripts/storage.py` guarda o actualiza un caso RT (`guardar_caso`), refleja su estado actual en esta hoja (buscando por el ID de mensaje de Gmail en la columna `G`; si ya existe actualiza la fila `A:G`, y si no existe agrega una fila nueva).
  2. **Cierre manual por parte de QA / Legal (`sincronizar_cierres_qa`):** Antes de iniciar la revisión de correos RT o de Slack, el sistema lee el rango `D:G` de esta hoja. Si una persona cambió manualmente la columna **D (Estado)** a **`Completado`** o **`Caso manual`**, el bot copia ese cierre a su base local (`data/runs.db`) y no vuelve a procesar ni a insistir sobre ese caso.
  3. **Métricas del Dashboard Web:** La pantalla principal del Dashboard (`http://127.0.0.1:8000`) lee la columna `D` (`D2:D`) para mostrar en vivo las tarjetas de conteo de casos RT por estado.

- **Estructura exacta de columnas (`A` a `G`):**

| Columna | Encabezado sugerido | Qué dato contiene | Quién la escribe |
| :---: | :--- | :--- | :--- |
| **A** | `nombre` | Nombre del RT en Lupe (o nombre del peticionario si aún no hay ficha en Lupe). | El bot (`storage.py`) |
| **B** | `correo del peticionario` | Dirección de correo electrónico del peticionario / remitente. | El bot (`storage.py`) |
| **C** | `id_rt` | ID del Rappitendero en Lupe (o el extraído del correo). | El bot (`storage.py`) |
| **D** | `estado` | Estado del caso: `Nuevo`, `En Slack - pendiente`, `Sin cuenta - pendiente`, `Sin cuenta - enviado`, `Completado` o `Caso manual`. | El bot actualiza el estado; **una persona (QA) puede escribir `Completado` o `Caso manual`** para cerrar el caso manualmente. |
| **E** | `fecha` | Fecha de recepción del correo en español (ej. `24 de octubre de 2026`). | El bot (`storage.py`) |
| **F** | `hilo de Slack` | Timestamp (`thread_id`) del mensaje publicado en el canal de Slack de OPS. | El bot (`storage.py`) |
| **G** | `id del mensaje` | `gmail_id` único del mensaje en Gmail. **Actúa como llave primaria de la fila.** | El bot (`storage.py`) |

---

#### 2. Hoja de Control de Casos **Entes** (`DRIVE_SHEETS_ENTES_ID`)

- **Para qué se debe crear:**
  1. **Control de envíos, reintentos y casos pendientes de información:** En el paso `entes` (`scripts/procesar_entes.py` y `scripts/entes/hoja.py`), el bot lee al inicio el rango `F:H` (`indice()`) para saber qué correos ya fueron radicados, cuáles llevan 1, 2 o 3 reintentos por fallo del formulario (`Error de envío - reintento N`) y cuáles ya fueron cerrados por QA.
  2. **Respeto del cierre de QA en la columna `H`:** El bot **únicamente escribe en las columnas `A` a `G`** (`ultima_columna="G"`). La columna **`H` (`completado`)** pertenece exclusivamente a quien revisa (QA): si una persona escribe `completado` en la columna `H`, el bot marca el correo como leído en Gmail y cierra el caso sin volver a tocarlo.
  3. **Métricas del Dashboard Web:** El Dashboard lee la columna `F` (`F2:F`) de esta hoja para mostrar en tiempo real el resumen de casos de Entes por estado.

- **Estructura exacta de columnas (`A` a `H`):**

| Columna | Encabezado (el que crea el instalador) | Qué dato contiene | Quién la escribe |
| :---: | :--- | :--- | :--- |
| **A** | `peticionario` | Nombre del peticionario o entidad que remite el caso. | El bot (`entes/hoja.py`) |
| **B** | `correo` | Correo electrónico del usuario/cliente final (o `-` si no vino en la petición). | El bot (`entes/hoja.py`) |
| **C** | `id usuario` | ID del usuario en Lupe (o `0` si no se encontró cuenta). | El bot (`entes/hoja.py`) |
| **D** | `fecha peticion` | Fecha de la petición (`YYYY-MM-DD`). | El bot (`entes/hoja.py`) |
| **E** | `causal` | Causal clasificada por el agente de IA de Entes (o `Otro`). | El bot (`entes/hoja.py`) |
| **F** | `estado` | Resultado de la gestión: `enviado a Entes`, `revisión manual`, `- sin correo- solicitarlo`, o `Error de envío - reintento 1/2/3`. | El bot (`entes/hoja.py`) |
| **G** | `Id - Gmail` | `mensaje_id` único de Gmail. **Actúa como llave primaria de la fila.** | El bot (`entes/hoja.py`) |
| **H** | `flujo completo` | Cierre manual de revisión: escribir `completado`. | **Exclusivo de QA** (el programa nunca pisa la columna `H`). |

---

### 5.5. ¿Qué Plantillas de Google Docs se deben crear y para qué sirve cada una?

Debes subir o crear **cinco (5) documentos nativos de Google Docs** en tu Google Drive.

> **Importante:** Deben ser documentos en formato **Google Docs nativo** (no archivos `.docx` de Word sin convertir), ya que `scripts/generar_borrador.py` utiliza la API `docs.documents().batchUpdate` para reemplazar texto, insertar saltos de página e incrustar imágenes, y luego `drive.files().export(..., mimeType="application/pdf")` para generar el PDF.

#### Cómo funciona el flujo con las plantillas
1. El sistema **nunca modifica la plantilla original**: hace una copia en `DRIVE_RESPONSES_FOLDER` con el nombre `Legal - Respuesta Derecho de petición - <Nombre>`.
2. Reemplaza todos los marcadores `{{ ... }}` por los valores del expediente y de `data-json/causales-rt.json`.
3. Si el caso lleva foto de prueba (`con_foto=True`), inserta un salto de página justo antes del párrafo cuyo texto sea exactamente `{% if foto_prueba %}`, `Anexo 1` o `{{ foto_prueba }}`, limpia las etiquetas `{% if foto_prueba %}` / `{% endif %}` y reemplaza `{{ foto_prueba }}` por la imagen enviada por OPS en Slack.
4. Exporta el documento final a PDF en `DRIVE_PDF_FOLDER` y guarda su ID para adjuntarlo en Gmail.

#### Cómo obtener el ID de cada plantilla de Google Docs
Abre el documento en Google Docs y copia la cadena que aparece entre `/document/d/` y `/edit`:
```text
https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit
                                   └─────────────────┬─────────────────┘
                                            ID de la plantilla
```

#### Detalle de las 5 Plantillas requeridas

| Variable en `.env` | Caso de negocio en el que se usa (`generar_borrador.py`) | Marcadores exactos que debe incluir el Google Doc |
| :--- | :--- | :--- |
| **`DOC_TEMPLATE_BLOCKED`** | **RT bloqueado (en nombre propio) CON evidencia fotográfica:** Se usa cuando el propio Rappitendero presenta la petición (`formato="bloqueado"`), OPS decide mantener el bloqueo en Slack y adjunta una imagen de prueba (`con_foto=True`). | • `{{ fecha }}`<br>• `{{ lupe.nombre \| title }}`<br>• `{{ lupe.correo \| title }}`<br>• `{{ correo.peticion  }}` *(nota: con dos espacios antes de `}}`)*<br>• `{{causales.descripcion }}` *(sin espacio inicial)*<br>• `{{ causales.numerales }}`<br>• `{% if foto_prueba %}` / `{% endif %}`<br>• `{{ foto_prueba }}` *(donde se incrustará la imagen)* |
| **`DOC_TEMPLATE_RT_WITHOUT_ANEXOS`** | **RT bloqueado (en nombre propio) SIN evidencia fotográfica:** Se usa cuando el propio Rappitendero presenta la petición (`formato="bloqueado"`), OPS decide mantener el bloqueo, pero en el hilo de Slack no se adjuntó imagen o solo hay un adjunto no visual como video (`con_foto=False`). | • `{{ fecha }}`<br>• `{{ lupe.nombre \| title }}`<br>• `{{ lupe.correo \| title }}`<br>• `{{ correo.peticion  }}`<br>• `{{causales.descripcion }}`<br>• `{{ causales.numerales }}` |
| **`DOC_TEMPLATE_RT_RL`** | **RT mediante Representante Legal / Abogado CON evidencia fotográfica:** Se usa cuando la petición viene firmada por apoderado o representante (`tipo_representacion="rt_rl"` / `tipo_caso=3`, `formato="rt_rl"`) y OPS adjunta imagen de prueba en Slack (`con_foto=True`). | Todos los marcadores de `DOC_TEMPLATE_BLOCKED` **más**:<br>• `{{ nombre.rl \| title }}` *(nombre del representante/peticionario)*<br>• `{{ correo.rl \| title }}` *(correo del representante/peticionario)* |
| **`DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS`** | **RT mediante Representante Legal / Abogado SIN evidencia fotográfica:** Se usa cuando la petición viene por apoderado/representante (`formato="rt_rl"`) y OPS mantiene el bloqueo sin adjuntar imagen de prueba (`con_foto=False`). | Todos los marcadores de `DOC_TEMPLATE_RT_WITHOUT_ANEXOS` **más**:<br>• `{{ nombre.rl \| title }}`<br>• `{{ correo.rl \| title }}` |
| **`DOC_TEMPLATE_THIRD_PARTY`** | **Petición presentada por un Tercero no legitimado (`formato="terceros"`):** Se usa cuando quien envía la petición es un tercero sin poder/representación (`tipo_caso=2` o cuando el ID de RT buscado en Lupe pertenece a otra persona distinta al peticionario). | • `{{ fecha }}`<br>• `{{ nombre \| title }}` *(nombre de quien envió el correo)*<br>• `{{ correo \| title }}` *(correo de quien envió la petición)*<br>• `{{ peticion }}`<br>• `{{ id }}` *(ID del RT consultado)* |

> **Nota sobre catálogos locales (`data-json/`):**
> - `data-json/causales-rt.json`: ya viene incluido en el repositorio con la lista de causales (`id`, `descripcion` y `numerales`) que alimentan `{{causales.descripcion }}` y `{{ causales.numerales }}`.
> - `data-json/mapeo-causales.json`: vincula el nombre exacto de la regla de bloqueo de Lupe con el `id` de la causal en `causales-rt.json` (`{"mapeo": {"Regla en Lupe": 1}}`).

---

## ⚙️ 6. Configuración de Variables de Entorno (`.env`)

> Copia `.env.example` a `.env` y complétalo a mano (o con `python -m instalador.instalar`). El `.env` nunca se sube a Git.

Crea un archivo `.env` en la raíz del proyecto (`Proyecto_E/.env`).

> **Importante:** Al arrancar un paso o el servicio, `utils/config_manager.py` valida que existan **`DRIVE_SHEETS_ID`**, **`DRIVE_RESPONSES_FOLDER`**, **`GMAIL_LABEL_ID`**, **`GMAIL_REPLY_TO`**, **`SLACK_REVIEW_CHANNEL_ID`** y al menos una clave de IA (**`GROQ_API_KEY`** o **`GEMINI_API_KEY`**). Si falta cualquiera de ellas, el programa se detiene indicando qué variable falta.

### Plantilla completa de `.env`

```ini
# ============================================================================
# 1. CREDENCIALES DE GOOGLE (Opcional si están en data-json/)
# ============================================================================
GOOGLE_CREDENTIALS_FILE=data-json/credentials.json
GOOGLE_TOKEN_FILE=data-json/token.json

# ============================================================================
# 2. GMAIL: BUZÓN LEGAL Y ETIQUETAS
# (Ejecuta `python scripts/lector_etiquetas.py` para obtener los IDs Label_...)
# ============================================================================
# Correo al que se responde en el hilo de Legal (OBLIGATORIO, sin valor por defecto)
GMAIL_REPLY_TO=legal@tudominio.com

# Etiquetas de clasificación (GMAIL_LABEL_ID es OBLIGATORIO)
GMAIL_LABEL_ID=Label_1
GMAIL_LABEL_ENTES=Label_2
GMAIL_LABEL_REVISION_MANUAL=Label_3
GMAIL_LABEL_ELIMINAR_DEUDA_RTS=Label_4
GMAIL_LABEL_FACTURACION=Label_5
GMAIL_LABEL_RESTAURANTES=Label_6
GMAIL_LABEL_OTROS=Label_7

# Correos adicionales (separados por coma) que siempre irán a revisión manual
# Nota: recepcion.hub@rappi.com ya se envía a revisión manual por defecto
CORREOS_REVISION_MANUAL=

# ============================================================================
# 3. GOOGLE SHEETS Y GOOGLE DRIVE (IDs de hojas, carpetas y plantillas)
# ============================================================================
# Hojas de seguimiento (DRIVE_SHEETS_ID es OBLIGATORIO)
DRIVE_SHEETS_ID=tu_spreadsheet_id_casos_rt
DRIVE_SHEETS_ENTES_ID=tu_spreadsheet_id_casos_entes

# Carpetas en Drive (DRIVE_RESPONSES_FOLDER es OBLIGATORIO)
# Si DRIVE_PDF_FOLDER o DRIVE_SCREENSHOTS_FOLDER se dejan vacíos, usan DRIVE_RESPONSES_FOLDER
DRIVE_RESPONSES_FOLDER=tu_folder_id_respuestas_docs
DRIVE_PDF_FOLDER=tu_folder_id_respuestas_pdf
DRIVE_SCREENSHOTS_FOLDER=tu_folder_id_capturas_temporales

# Plantillas Google Docs para respuestas RT (5 documentos nativos de Google Docs)
DOC_TEMPLATE_BLOCKED=tu_doc_id_bloqueado_con_anexo
DOC_TEMPLATE_RT_WITHOUT_ANEXOS=tu_doc_id_bloqueado_sin_anexo
DOC_TEMPLATE_RT_RL=tu_doc_id_representante_con_anexo
DOC_TEMPLATE_RT_RL_WITHOUT_ANEXOS=tu_doc_id_representante_sin_anexo
DOC_TEMPLATE_THIRD_PARTY=tu_doc_id_terceros

# ============================================================================
# 4. SLACK (Coordinación con OPS)
# ============================================================================
SLACK_BOT_TOKEN=xoxb-tu-token-de-slack
SLACK_REVIEW_CHANNEL_ID=C0123456789
SLACK_REVIEW_CHANNEL_NAME=nombre-canal-revision
# IDs de usuarios/grupos de Slack a mencionar según el tipo de bloqueo
SLACK_REVIEW_USER_FRAUD=U0123456789
SLACK_REVIEW_USER_DISCIPLINE=U9876543210

# ============================================================================
# 5. INTELIGENCIA ARTIFICIAL (Se requiere al menos GROQ_API_KEY o GEMINI_API_KEY)
# ============================================================================
# Proveedor primario: "groq" o "gemini" (si el primario falla, usa el otro como respaldo)
IA_PROVIDER=groq
GROQ_API_KEY=gsk_tu_clave_principal
GROQ_API_KEY2=
GROQ_MODEL=openai/gpt-oss-120b

GEMINI_API_KEY=AIzaSy_tu_clave_gemini
GEMINI_MODEL=gemini-3.8-flash
# Segundos máximos de espera por respuesta de Gemini (vacío = 120)
GEMINI_TIMEOUT_SEG=

# ============================================================================
# 6. LUPE Y FORMULARIO DE ENTES
# ============================================================================
URL_LUPE=https://lupe.rappi.com/
# Ruta del perfil persistente de Chromium (por defecto: %LOCALAPPDATA%\ProyectoE\lupe_profile)
LUPE_PROFILE_DIR=
# Canal del navegador: vacío usa el Chromium de Playwright; "chrome" usa Google Chrome local
LUPE_CHANNEL=
LUPE_MODO=interactivo
LUPE_REINGRESO_SEG=30
LUPE_LOGIN_MAX_SEG=300
LUPE_LOCK_ESPERA_SEG=1800

# Configuración de Entes
URL_FORMULARIO=https://master.d3i85um4nwfu2v.amplifyapp.com/
ENTES_MAX_CORREOS=26
ENTES_TIMEOUT_FORM=120
ENTES_MAX_INTENTOS=3
ENTES_MAX_FALLOS_RONDA=2
ENTES_MAX_IMAGENES=3

# ============================================================================
# 7. SERVICIO AUTOMÁTICO, DASHBOARD, CONTROL DE CALIDAD Y ALERTAS
# ============================================================================
CICLO_HORAS=4
PASO_TIMEOUT_MIN=120
DASHBOARD_PUERTO=8000

# true = crea borrador en Gmail | false = envía el correo directamente
CONTROL_CALIDAD=true
# true = pausa el ciclo tras el paso "clasificar" hasta pulsar Continuar en el Dashboard
REVISION_CLASIFICACION=false

# Canales de alerta separados por coma: toast, gmail, slack
ALERTAS_CANALES=toast,gmail
ALERTAS_EMAIL=
ALERTAS_SLACK_CANAL=
```

> **Nota sobre país en el formulario de Entes:** El formulario web de Entes está configurado por defecto para Colombia (`PAIS = "co"` en `scripts/entes/formulario.py`). Si se despliega para otro país, ajusta esa constante en dicho archivo.

---

## 🔐 7. Autenticación Inicial (Google OAuth y Sesión de Lupe)

Tras `INSTALAR.bat`, autoriza Google e inicia sesión en Lupe. En el editor la terminal ya trae `.venv` activo; fuera del editor usa la ruta completa:

### Paso 1: Autorizar las APIs de Google y obtener los IDs de etiquetas de Gmail

Ejecuta el lector de etiquetas:

```bash
.venv\Scripts\python.exe scripts\lector_etiquetas.py
```

1. La primera vez se abrirá el navegador solicitando autorizar la cuenta de Google para Gmail, Drive, Docs y Sheets.
2. Al aceptar, se guardará automáticamente `data-json/token.json`.
3. En la consola verás el listado de todas las etiquetas del buzón (`nombre → id`). Copia los IDs correspondientes en las variables `GMAIL_LABEL_*` de tu archivo `.env`.

### Paso 2: Iniciar sesión en Lupe (perfil persistente de navegador)

Ejecuta el comando de login de Lupe (o, con el servicio activo, pulsa **Iniciar sesión en Lupe** en el Dashboard):

```bash
.venv\Scripts\python.exe main.py login
```

1. Se abrirá una ventana de Chromium utilizando el perfil persistente (`LUPE_PROFILE_DIR`).
2. Inicia sesión con Google en Lupe (incluyendo 2FA si lo solicita).
3. En cuanto el sistema detecte que el tablero principal de Lupe cargó (y acepte automáticamente los términos y condiciones si aparecen), cerrará el navegador y guardará el estado `vigente` en `logs/lupe_sesion_estado.json`.

---

## 🖥️ 8. Uso y Comandos (`main.py`)

El punto de entrada `main.py` funciona directamente por subcomandos (sin menús interactivos que bloqueen la ejecución):

```bash
# 1. Iniciar el servicio completo (Scheduler cada CICLO_HORAS + Dashboard Web en http://127.0.0.1:8000)
python main.py servicio

# 2. Ejecutar el ciclo completo una sola vez (clasificar → rt → slack → entes)
python main.py ciclo

# 3. Ejecutar un único paso de forma individual
python main.py paso clasificar
python main.py paso rt
python main.py paso slack
python main.py paso entes

# 4. Abrir ventana de navegador para iniciar o renovar sesión en Lupe
python main.py login

# 5. Pausar o reanudar los ciclos programados desde consola
python main.py pausar
python main.py reanudar
```

---

## 🌐 9. Dashboard Web y Controles Operativos

Cuando el servicio está activo (`python main.py servicio`), el panel de control web está disponible localmente en:

👉 **`http://127.0.0.1:8000`** *(o el puerto configurado en `DASHBOARD_PUERTO`)*

### Funcionalidades del Dashboard

- **Estado del servicio y próximo ciclo:** Muestra si el servicio está activo, en pausa o ejecutando un paso en este instante, y calcula la hora exacta del próximo ciclo a partir del último registro en `data/runs.db` (evitando lanzar un ciclo repetido si se reinicia el servicio antes de cumplirse las `CICLO_HORAS`).
- **Ejecución manual:** Permite lanzar el **ciclo completo** o cualquiera de los **4 pasos individuales** (`Clasificar`, `RT`, `Slack`, `Entes`) en segundo plano con un clic.
- **Pausar / Reanudar / Detener paso:**
  - **Pausar:** Impide que arranquen nuevos ciclos programados y solicita al paso en curso que se detenga limpiamente al terminar el caso actual.
  - **Detener paso:** Corta únicamente el paso o ciclo en curso al finalizar el caso actual, **sin** dejar el servicio en pausa para el siguiente turno programado.
- **Modo de respuesta (`CONTROL_CALIDAD`):**
  - **Borrador (`CONTROL_CALIDAD=true`):** Deja las respuestas listas como borradores en Gmail para revisión humana.
  - **Enviar (`CONTROL_CALIDAD=false`):** Envía los correos directamente en el hilo de Gmail.
  - *Por seguridad, este selector solo puede modificarse cuando no hay un ciclo corriendo o cuando el servicio está en pausa.*
- **Confirmación tras clasificar (`REVISION_CLASIFICACION`):**
  - **Confirmar (`true`):** Detiene el ciclo al terminar el paso `clasificar` y muestra el botón **Continuar** en el Dashboard para revisar las etiquetas en Gmail antes de que corran `rt`, `slack` y `entes`. Si `clasificar` falla con error y esta opción está activa, el ciclo se detiene por seguridad.
  - **Continuo (`false`):** Ejecuta los 4 pasos de corrido sin intervención humana.
- **Contadores de Google Sheets en vivo:** Muestra el conteo de casos por estado tanto de la hoja de **RT** como de la hoja de **Entes** (con caché de 5 minutos y botón de refresco inmediato).
- **Estado de Lupe y Alertas:** Indica si la sesión de Lupe está `vigente`, `requiere_login` o `sin_perfil`, y lista las últimas alertas emitidas.
- **Tarjeta «Lupe» y botón «Iniciar sesión»:** La tarjeta está siempre en el inicio. Con la sesión vigente dice **Sesión vigente**; si no, **Desconectado** con el botón **Iniciar sesión**, que hace lo mismo que `python main.py login` sin abrir una terminal. Abre Chromium con el perfil persistente **en el PC donde corre el servicio**, muestra el estado (*Abriendo navegador…*, *Sesión iniciada* o el error) y deja el resultado en el log (paso `login_lupe`). No se lanza dos veces a la vez, y si un ciclo o paso está usando Lupe avisa en lugar de chocar con el candado del perfil.
- **Visor de Registro (`/registro`):** Muestra en tiempo real las últimas 200 líneas de `logs/proyecto_e.log` con auto-refresco cada 10 segundos mientras hay un ciclo en ejecución.

---

## 🪟 10. Despliegue e Instalación en Windows (Panel de Escritorio)

La carpeta **`deploy/`** incluye un instalador portátil para Windows que configura el **Gestor de Correos** con interfaz gráfica de escritorio y arranque automático al iniciar sesión en Windows, **sin requerir permisos de Administrador** y detectando dinámicamente las rutas y el entorno virtual (`.venv`, `venv` o `env`).

### Instalación en 1 clic

1. Corre esto **después** de `INSTALAR.bat` y de tener el `.env` completo (`credentials.json`, IDs de Drive y Gmail).
2. Entra a la carpeta **`deploy/`** y haz **doble clic** sobre:
   ```text
   INSTALAR_GESTOR_CORREOS.bat
   ```
3. El instalador realizará automáticamente:
   - Creación del acceso directo **«Gestor de Correos»** en el Escritorio con su ícono personalizado.
   - Registro del arranque automático en el inicio de Windows del usuario actual (`shell:startup`).
   - Encendido inmediato del servicio en segundo plano (sin ventanas de consola negras) y apertura de la ventana de control.

### Uso del Panel de Escritorio (`gestor_correos_control.pyw`)

- **Semáforo de estado:**
  - 🟢 **SERVICIO PRENDIDO:** El scheduler y el Dashboard Web están activos ejecutando los ciclos automáticos.
  - 🟡 **SERVICIO EN PAUSA:** El Dashboard Web sigue disponible, pero los ciclos automáticos están pausados.
  - 🔴 **SERVICIO APAGADO:** El proceso del servicio está completamente detenido.
- **Botón `🌐 Abrir Panel Web`:** Abre `http://127.0.0.1:8000` en el navegador predeterminado (si el servicio estaba apagado, lo enciende automáticamente antes de abrir el navegador).
- **Bandeja del sistema (System Tray):** Al cerrar la ventana, el programa permanece activo junto al reloj de Windows para abrir el panel, prender/apagar el servicio o salir.

### Desinstalación del arranque automático

Para retirar el arranque automático de Windows y el acceso directo del Escritorio, haz doble clic en:
```text
deploy\DESINSTALAR_GESTOR_CORREOS.bat
```

---

## 🗄️ 11. Base de Datos Local, Logs y Códigos de Salida

### Base de datos SQLite (`data/runs.db`)

Se crea automáticamente en el primer arranque (no requiere migraciones manuales ni importar casos antiguos) y contiene tres tablas:
- `ultimo_ciclo`: Guarda la marca de tiempo (`inicio`) del último ciclo iniciado para respetar el intervalo `CICLO_HORAS` incluso si se reinicia el computador.
- `casos_rt`: Almacena el expediente completo de cada caso RT (`mensaje_id`, `id_rt`, `estado`, `regla_bloqueo`, `thread_slack` y payload JSON), permitiendo detectar duplicados y cruzar decisiones de Slack.
- `hueco_entes`: Registra los casos de Entes cuyo formulario ya salió pero falló la escritura en la hoja, para cerrar solo la fila en la siguiente ronda sin reenviar.

### Archivos de Log y Candados

- `logs/proyecto_e.log`: Registro unificado con formato `fecha | NIVEL | paso | caso | mensaje`.
- `logs/lupe_sesion_estado.json`: Último estado conocido de la sesión de Lupe (`vigente` o `requiere_login`).
- `data/servicio.lock` y `data/ciclo.lock`: Candados `FileLock` que garantizan que nunca corran dos instancias del servicio ni dos ciclos al mismo tiempo en el mismo PC.

### Códigos de salida de los pasos

| Código | Significado | Acción del Orquestador |
| :---: | :--- | :--- |
| `0` | Ejecución exitosa | Marca el paso como `ok` (o `aviso` si el resumen reportó errores puntuales en algún caso). |
| `3` | Servicio ya en ejecución | `python main.py servicio` sale inmediatamente si detecta otro servicio activo (`data/servicio.lock`). |
| `20` | **Lupe requiere iniciar sesión** | Emite alerta `lupe_login` y omite los demás pasos que usan Lupe durante ese ciclo. Quedan en `logs/lupe_pendiente.json` y se reanudan solos (en menos de 30 s) cuando la sesión vuelve a estar vigente, con el botón del dashboard o con `python main.py login`. |
| `21` | **Perfil de Lupe ocupado** | Registra aviso `lupe_ocupado` y reintenta el paso en el siguiente ciclo. |
| `22` | **Sin conexión a Google/IA** | Emite alerta `sin_red` y corta el ciclo. Los correos quedan sin leer y en su cola; se retoman con **Ciclo completo** en el dashboard o en el próximo ciclo automático. |
| `!= 0` | Error en el paso | Emite alerta `paso_error` con las últimas líneas de salida del proceso. |

---

## 🛠️ 12. Solución de Problemas Frecuentes

| Síntoma / Mensaje | Causa | Solución |
| :--- | :--- | :--- |
| **No sé qué está fallando** | — | Corre `.venv\Scripts\python.exe scripts\diagnostico.py` y sigue lo que dice cada `[FALTA]`. |
| **`INSTALAR.bat`: falta Python 3.13** | Python no está instalado o no está en el PATH. | Pídelo por ticket (Python 3.13 de 64 bits con *Add python.exe to PATH*) y vuelve a correr `INSTALAR.bat`. |
| **El instalador dice que el JSON no sirve** | Elegiste un archivo que no es un cliente OAuth (por ejemplo una cuenta de servicio o un token). | Descarga en Google Cloud el JSON de *OAuth client ID* tipo *Aplicación de escritorio* y elígelo de nuevo. |
| **Slack `not_in_channel` o `invalid_auth`** | El bot no está en el canal o el token es incorrecto. | Invita al bot al canal o corrige `SLACK_BOT_TOKEN` en el `.env`. |
| **IA `401` / `429` / `404`** | Clave inválida / límite de uso / modelo inexistente. | `401`: corrige la clave en `.env`. `429`: espera o usa la otra IA. `404`: revisa `GROQ_MODEL` o `GEMINI_MODEL`; las cuentas nuevas de Gemini ya no tienen `gemini-2.5-flash` y deben usar `gemini-3.8-flash`. |
| **IA `413`: «Correo demasiado grande para la IA»** | El correo supera el límite de tokens por minuto del plan gratuito de Groq. No se arregla cambiando la clave. | El programa pasa directo a Gemini. Si pasa seguido, usa `IA_PROVIDER=gemini`. |
| **IA `504`: «La IA no respondió a tiempo»** | Gemini tardó más que `GEMINI_TIMEOUT_SEG` (por defecto 120 s) o está saturado. | Se reintenta solo; si persiste, sube `GEMINI_TIMEOUT_SEG` en el `.env` o prueba otro `GEMINI_MODEL`. |
| **`ACTUALIZAR.bat` se detiene por cambios locales** | Editaste archivos del repositorio. | Descártalos (`git checkout -- .`) o guárdalos aparte y vuelve a correrlo. |
| **`Variables críticas faltantes en el .env`** | Falta alguna de las 5 variables obligatorias o ambas claves de IA. | Verifica en `.env` que estén definidas `DRIVE_SHEETS_ID`, `DRIVE_RESPONSES_FOLDER`, `GMAIL_LABEL_ID`, `GMAIL_REPLY_TO`, `SLACK_REVIEW_CHANNEL_ID` y al menos `GROQ_API_KEY` o `GEMINI_API_KEY`. |
| **Alerta: «Lupe pide iniciar sesión» (código `20`)** | Expiró la sesión de Google dentro del perfil de Chromium de Lupe. | Pulsa **Iniciar sesión en Lupe** en el Dashboard (o ejecuta `python main.py login` en una terminal) y completa el acceso con Google en la ventana que se abre en el PC del servicio. Si el botón avisa que Lupe está en uso, espera a que termine el ciclo o paso. |
| **`Google pide autorizar de nuevo` en pasos automáticos** | El archivo `data-json/token.json` fue revocado o expiró sin poder renovarse en segundo plano. | Ejecuta `python main.py paso clasificar` o `python scripts/lector_etiquetas.py` desde la terminal para completar la autorización OAuth en el navegador. |
| **Alerta: «Sin conexión a Google/IA» (código `22`)** o `Unable to find the server at gmail.googleapis.com` | El PC no tiene internet, o la VPN, el proxy o el firewall bloquean `*.googleapis.com`. | No se perdió nada: los correos siguen pendientes. Corre `.venv\Scripts\python.exe scripts\diagnostico.py`; cuando salga `[OK] Internet`, pulsa **Ciclo completo** en el Dashboard o espera el próximo ciclo. |
| **El navegador de Lupe indica que el perfil está ocupado (código `21`)** | Otro proceso o ventana de Chromium quedó abierto usando `LUPE_PROFILE_DIR`. | Cierra cualquier ventana colgada de Chromium de Lupe o espera a que termine el paso en curso. |
| **No se puede cambiar entre `Borrador` y `Enviar` en el Dashboard** | Hay un ciclo en ejecución actualmente. | Pausa el servicio primero desde el Dashboard (o espera a que termine el ciclo actual) para cambiar el modo de envío. |
