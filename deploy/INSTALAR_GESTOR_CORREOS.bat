@echo off
chcp 65001 >nul
title Instalador - Gestor de Correos
echo ==========================================================
echo        INSTALANDO GESTOR DE CORREOS AUTOMATICAMENTE
echo ==========================================================
echo.

set "SCRIPT_PS1=%~dp0instalar_gestor_correos.ps1"
if not exist "%SCRIPT_PS1%" (
    set "SCRIPT_PS1=%~dp0deploy\\instalar_gestor_correos.ps1"
)

if not exist "%SCRIPT_PS1%" (
    echo [ERROR] No se encontro instalar_gestor_correos.ps1
    echo Asegurate de que la carpeta "deploy" este dentro de la carpeta del proyecto.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_PS1%"
echo.
echo Puedes cerrar esta ventana.
timeout /t 8
