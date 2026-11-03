<#
.SYNOPSIS
  Instalador automatico de "Gestor de Correos" (sin permisos de Administrador).
  Detecta automaticamente .venv, venv, env o cualquier entorno virtual en la carpeta del proyecto.
#>
param(
    [switch]$Desinstalar,
    [switch]$Silencioso
)

$ErrorActionPreference = "Stop"
$deployDir = $PSScriptRoot
$raiz = Split-Path -Parent $deployDir

$escritorio = [Environment]::GetFolderPath("Desktop")
$inicioWin  = [Environment]::GetFolderPath("Startup")

$lnkEscritorio = Join-Path $escritorio "Gestor de Correos.lnk"
$lnkInicio     = Join-Path $inicioWin  "Gestor de Correos.lnk"

$tareaAntigua = "Proyecto E - servicio"
if (Get-ScheduledTask -TaskName $tareaAntigua -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $tareaAntigua -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $tareaAntigua -Confirm:$false -ErrorAction SilentlyContinue
}

if ($Desinstalar) {
    foreach ($rutaLnk in @($lnkEscritorio, $lnkInicio)) {
        if (Test-Path $rutaLnk) {
            Remove-Item $rutaLnk -Force
            Write-Host "[OK] Eliminado acceso directo: $rutaLnk" -ForegroundColor Yellow
        }
    }
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^pythonw?\\.exe$' -and (
            $_.CommandLine -like '*main.py*servicio*' -or
            $_.CommandLine -like '*gestor_correos_control.pyw*'
        )
    } | ForEach-Object {
        & taskkill.exe /F /T /PID $_.ProcessId 2>$null | Out-Null
    }
    Write-Host "[OK] Gestor de Correos desinstalado y detenido correctamente." -ForegroundColor Green
    exit 0
}

# Buscar entorno virtual en .venv, venv, env o cualquier subcarpeta con Scripts\python.exe
$python = $null
$pythonw = $null

foreach ($carpetaVenv in @(".venv", "venv", "env")) {
    $candPy  = Join-Path $raiz "$carpetaVenv\\Scripts\\python.exe"
    $candPyw = Join-Path $raiz "$carpetaVenv\\Scripts\\pythonw.exe"
    if (Test-Path $candPy) {
        $python = $candPy
        if (Test-Path $candPyw) { $pythonw = $candPyw } else { $pythonw = $candPy }
        break
    }
}

if (-not $python) {
    $encontrado = Get-ChildItem -Path $raiz -Filter "python.exe" -Recurse -Depth 3 -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*\\Scripts\\python.exe" } | Select-Object -First 1
    if ($encontrado) {
        $python = $encontrado.FullName
        $posiblePyw = Join-Path $encontrado.DirectoryName "pythonw.exe"
        if (Test-Path $posiblePyw) { $pythonw = $posiblePyw } else { $pythonw = $python }
    }
}

if (-not $python) {
    $cmdPy  = Get-Command python.exe  -ErrorAction SilentlyContinue
    $cmdPyw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmdPy)  { $python  = $cmdPy.Source }
    if ($cmdPyw) { $pythonw = $cmdPyw.Source } elseif ($python) { $pythonw = $python }
}

if (-not $pythonw) {
    throw "No se encontro python.exe / pythonw.exe en .venv, venv ni en el sistema."
}

Write-Host "[OK] Python detectado: $python" -ForegroundColor Cyan

$scriptControl = Join-Path $deployDir "gestor_correos_control.pyw"
$iconoPath     = Join-Path $deployDir "gestor_correos.ico"

if (-not (Test-Path $scriptControl)) {
    throw "No se encontro $scriptControl"
}

if ($python -and (Test-Path $python)) {
    Write-Host "Verificando componentes visuales y del servicio..." -ForegroundColor Cyan
    & $python -m pip install --quiet --disable-pip-version-check pystray pillow apscheduler fastapi uvicorn jinja2 filelock 2>$null
}

$wsh = New-Object -ComObject WScript.Shell
$q = [char]34

# 1. Acceso directo en el Escritorio ("Gestor de Correos")
$scDesktop = $wsh.CreateShortcut($lnkEscritorio)
$scDesktop.TargetPath = $pythonw
$scDesktop.Arguments = "${q}${scriptControl}${q}"
$scDesktop.WorkingDirectory = $raiz
$scDesktop.Description = "Abrir panel de control del Gestor de Correos"
if (Test-Path $iconoPath) {
    $scDesktop.IconLocation = "$iconoPath,0"
}
$scDesktop.Save()
Write-Host "[OK] Icono creado en el Escritorio: $lnkEscritorio" -ForegroundColor Green

# 2. Acceso directo en el Inicio de Windows (shell:startup)
$scStartup = $wsh.CreateShortcut($lnkInicio)
$scStartup.TargetPath = $pythonw
$scStartup.Arguments = "${q}${scriptControl}${q} --auto-start"
$scStartup.WorkingDirectory = $raiz
$scStartup.Description = "Iniciar automaticamente el Gestor de Correos al encender el PC"
if (Test-Path $iconoPath) {
    $scStartup.IconLocation = "$iconoPath,0"
}
$scStartup.Save()
Write-Host "[OK] Auto-inicio configurado en Windows (sin permisos de Admin): $lnkInicio" -ForegroundColor Green

# 3. Limpiar banderas previas y reiniciar la ventana y el servicio
foreach ($flag in @("logs\\servicio.pausado", "logs\\servicio.apagado_manual", "logs\\detener.flujo")) {
    $rutaFlag = Join-Path $raiz $flag
    if (Test-Path $rutaFlag) {
        Remove-Item $rutaFlag -Force -ErrorAction SilentlyContinue
    }
}

Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\\.exe$' -and $_.CommandLine -like '*gestor_correos_control.pyw*'
} | ForEach-Object {
    & taskkill.exe /F /T /PID $_.ProcessId 2>$null | Out-Null
}

$mainPy = Join-Path $raiz "main.py"
Start-Process -FilePath $python -WindowStyle Hidden -ArgumentList "-u ${q}${mainPy}${q} servicio" -WorkingDirectory $raiz

if ($Silencioso) {
    Start-Process -FilePath $pythonw -ArgumentList "${q}${scriptControl}${q} --auto-start" -WorkingDirectory $raiz
} else {
    Start-Process -FilePath $pythonw -ArgumentList "${q}${scriptControl}${q}" -WorkingDirectory $raiz
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  GESTOR DE CORREOS INSTALADO Y PRENDIDO CORRECTAMENTE!   " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "1. En el Escritorio tienes el icono 'Gestor de Correos'."
Write-Host "2. Al encender el PC, el servicio arrancara solo."
Write-Host "3. Panel web disponible en: http://127.0.0.1:8000"
