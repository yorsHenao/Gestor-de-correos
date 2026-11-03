@echo off
chcp 65001 >nul
title Desinstalador - Gestor de Correos
echo ==========================================================
echo        DESINSTALANDO AUTO-INICIO GESTOR DE CORREOS
echo ==========================================================
echo.

set "SCRIPT_PS1=%~dp0instalar_gestor_correos.ps1"
if not exist "%SCRIPT_PS1%" (
    set "SCRIPT_PS1=%~dp0deploy\\instalar_gestor_correos.ps1"
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_PS1%" -Desinstalar
echo.
pause
