@echo off
chcp 65001 >nul
setlocal
title Instalador - Proyecto E
cd /d "%~dp0"
set "PYTHONUTF8=1"
echo ==========================================================
echo                 INSTALADOR - PROYECTO E
echo  Se puede volver a correr: solo completa lo que falte.
echo ==========================================================
echo.

rem --- 1. Git y clon del repositorio ---
where git >nul 2>&1
if errorlevel 1 (
    echo [FALTA] Git no esta instalado.
    echo         Pide por ticket a Mesa de Ayuda: "Git for Windows". Luego vuelve a correr INSTALAR.bat.
    goto fallo
)
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [FALTA] Esta carpeta no es un clon de git del proyecto.
    echo         Descarga el proyecto con "git clone" ^(ver README^) para poder actualizar con ACTUALIZAR.bat.
    goto fallo
)
echo [OK]    Git y clon del repositorio

rem --- 2. Python 3.13 (no se instala aqui) ---
set "PY="
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [FALTA] Python 3.13 no esta instalado.
    echo         Pide por ticket a Mesa de Ayuda: "Python 3.13 de 64 bits, con la opcion Add python.exe to PATH".
    echo         Cuando lo tengas, vuelve a correr INSTALAR.bat.
    goto fallo
)
echo [OK]    Python 3.13

rem --- 3. Entorno virtual .venv ---
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" (
    "%VPY%" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,13) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [FALTA] La carpeta .venv existe pero no es de Python 3.13.
        echo         Cierra el programa, borra la carpeta .venv a mano y vuelve a correr INSTALAR.bat.
        goto fallo
    )
) else (
    echo         Creando el entorno virtual .venv ...
    %PY% -m venv .venv
    if not exist "%VPY%" (
        echo [FALTA] No se pudo crear .venv. Revisa el error de arriba.
        goto fallo
    )
)
echo [OK]    Entorno virtual .venv

rem --- 4. Dependencias ---
echo         Instalando dependencias ^(puede tardar unos minutos la primera vez^) ...
"%VPY%" -m pip install --disable-pip-version-check --upgrade pip >nul 2>&1
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [FALTA] Dependencias: pip mostro errores arriba. Revisa la conexion o el proxy y vuelve a correr INSTALAR.bat.
    goto fallo
)
echo [OK]    Dependencias de requirements.txt

"%VPY%" -m playwright install chromium
if errorlevel 1 (
    echo [FALTA] Chromium de Playwright no se pudo descargar. Revisa la conexion y vuelve a correr INSTALAR.bat.
    goto fallo
)
echo [OK]    Chromium de Playwright

rem --- 5. Configuracion: .env, Google, etiquetas, Drive, Lupe y diagnostico ---
"%VPY%" -m instalador.instalar
if errorlevel 1 goto fallo

rem --- 6. Accesos directos y servicio ---
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\instalar_gestor_correos.ps1" -Silencioso
if errorlevel 1 (
    echo [FALTA] No se pudieron crear los accesos directos ni arrancar el servicio. Revisa el error de arriba.
    goto fallo
)
echo.
echo [OK]    Proyecto E instalado. Abre el icono "Gestor de Correos" del Escritorio.
pause
exit /b 0

:fallo
echo.
echo ==========================================================
echo  INSTALACION INCOMPLETA. Corrige lo marcado en [FALTA] y
echo  vuelve a correr INSTALAR.bat.
echo ==========================================================
pause
exit /b 1
