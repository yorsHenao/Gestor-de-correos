@echo off
chcp 65001 >nul
setlocal
title Actualizar - Proyecto E
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "VPY=%~dp0.venv\Scripts\python.exe"
echo ==========================================================
echo                ACTUALIZAR - PROYECTO E
echo ==========================================================
echo.

where git >nul 2>&1 || (echo [FALTA] Git no esta instalado. & goto fin_error)
git rev-parse --is-inside-work-tree >nul 2>&1 || (echo [FALTA] Esta carpeta no es un clon de git. & goto fin_error)
if not exist "%VPY%" (echo [FALTA] No hay .venv: corre primero INSTALAR.bat. & goto fin_error)

rem --- Cambios locales: no se actualiza encima de ellos ---
set "CAMBIOS="
for /f "delims=" %%L in ('git status --porcelain --untracked-files=no') do set "CAMBIOS=1"
if defined CAMBIOS (
    echo [FALTA] Hay archivos del proyecto modificados en este PC:
    git status --short --untracked-files=no
    echo         Avisa a Jeisembert antes de actualizar. No se cambio nada.
    goto fin_error
)

rem --- 1. Detener el servicio ---
echo         Deteniendo el servicio ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and ($_.CommandLine -like '*main.py*servicio*' -or $_.CommandLine -like '*gestor_correos_control.pyw*') } | ForEach-Object { taskkill.exe /F /T /PID $_.ProcessId 2>$null | Out-Null }"
echo [OK]    Servicio detenido

rem --- 2. Respaldo fuera del repo ---
for /f %%F in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "FECHA=%%F"
set "RESPALDO=%LOCALAPPDATA%\ProyectoE\respaldos\%FECHA%"
mkdir "%RESPALDO%\data-json" >nul 2>&1
if exist ".env" copy /y ".env" "%RESPALDO%\" >nul
if exist "data-json\token.json" copy /y "data-json\token.json" "%RESPALDO%\data-json\" >nul
if exist "data-json\credentials.json" copy /y "data-json\credentials.json" "%RESPALDO%\data-json\" >nul
if exist "data" robocopy "data" "%RESPALDO%\data" /E /NFL /NDL /NJH /NJS /NP >nul
echo [OK]    Respaldo en %RESPALDO%

rem --- 3. Bajar la version nueva ---
git pull --ff-only
if errorlevel 1 (
    echo [FALTA] git pull no se pudo completar ^(ver error arriba^). El codigo sigue en la version anterior.
    set "ERROR_PULL=1"
    goto reiniciar
)
echo [OK]    Codigo actualizado

rem --- 4. Dependencias y variables nuevas ---
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (echo [FALTA] Dependencias: revisa los errores de arriba. & set "ERROR_PULL=1")
"%VPY%" -m playwright install chromium
if errorlevel 1 (echo [FALTA] Chromium de Playwright no se pudo instalar. & set "ERROR_PULL=1")
"%VPY%" -m instalador.instalar --actualizar

rem --- 5. Diagnostico ---
echo.
"%VPY%" scripts\diagnostico.py
if errorlevel 1 set "ERROR_PULL=1"

:reiniciar
echo.
echo         Reiniciando el servicio ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\instalar_gestor_correos.ps1" -Silencioso
if defined ERROR_PULL (
    echo.
    echo [AVISO] La actualizacion termino con puntos en [FALTA]. Revisalos arriba.
    goto fin_error
)
echo.
echo [OK]    Proyecto E actualizado.
pause
exit /b 0

:fin_error
pause
exit /b 1
