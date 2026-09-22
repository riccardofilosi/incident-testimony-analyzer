# Launcher unificato (backend + frontend in una sola finestra)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Forza UTF-8 in console (per i caratteri # # . del banner analisi_testimonianze)
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding  = [System.Text.Encoding]::UTF8
    $OutputEncoding           = [System.Text.Encoding]::UTF8
    chcp 65001 | Out-Null
} catch { }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# --- Aggiungi ffmpeg al PATH se installato via winget ma non ancora propagato ---
$ffmpegFromWinget = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*" -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Get-ChildItem -Path $_.FullName -Directory -ErrorAction SilentlyContinue } |
    ForEach-Object { Join-Path $_.FullName "bin" } |
    Where-Object { Test-Path (Join-Path $_ "ffmpeg.exe") } |
    Select-Object -First 1
if ($ffmpegFromWinget) {
    if (($env:PATH -split ';') -notcontains $ffmpegFromWinget) {
        $env:PATH = "$ffmpegFromWinget;$env:PATH"
    }
}

# --- Risolvi Python: prima il venv, poi fallback sistema ---
$python = $null
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    foreach ($c in @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )) {
        if (Test-Path $c) { $python = $c; break }
    }
    if (-not $python) {
        try { $python = (Get-Command python -ErrorAction Stop).Source } catch { $python = $null }
    }
}
if (-not $python) {
    Write-Host "[ERRORE] Python non trovato. Installa da https://python.org/ o lancia setup.ps1 per creare il venv." -ForegroundColor Red
    Read-Host "Premi Invio per chiudere"; exit 1
}

# --- Risolvi Node ---
$node = $null
foreach ($c in @(
    "C:\Program Files\nodejs\node.exe",
    "C:\Program Files (x86)\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe"
)) {
    if (Test-Path $c) { $node = $c; break }
}
if (-not $node) {
    try { $node = (Get-Command node -ErrorAction Stop).Source } catch { $node = $null }
}
if (-not $node) {
    Write-Host "[ERRORE] Node.js non trovato. Installa da https://nodejs.org/" -ForegroundColor Red
    Read-Host "Premi Invio per chiudere"; exit 1
}

$viteBin = Join-Path $root "frontend\node_modules\vite\bin\vite.js"
if (-not (Test-Path $viteBin)) {
    Write-Host "[ERRORE] frontend\node_modules\vite mancante. Esegui 'npm install' in frontend/" -ForegroundColor Red
    Read-Host "Premi Invio per chiudere"; exit 1
}

Write-Host ""
Write-Host "====================================================" -ForegroundColor DarkGray
Write-Host "  Acqua Riva . Reportistica eventi critici" -ForegroundColor Green
Write-Host "====================================================" -ForegroundColor DarkGray
Write-Host "  Python  : $python" -ForegroundColor DarkGray
Write-Host "  Node    : $node" -ForegroundColor DarkGray
Write-Host "  Backend : http://localhost:8000" -ForegroundColor DarkGray
Write-Host "  Frontend: http://localhost:5173" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Premi Ctrl+C (o chiudi questa finestra) per fermare tutto." -ForegroundColor Yellow
Write-Host ""

# --- Avvia backend come Job ---
$backendJob = Start-Job -Name "app-backend" -ScriptBlock {
    param($py, $cwd, $pathExt)
    Set-Location $cwd
    $env:PATH = "$pathExt;$env:PATH"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    & $py -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 2>&1
} -ArgumentList $python, $root, $ffmpegFromWinget

# --- Avvia frontend come Job ---
$frontendJob = Start-Job -Name "app-frontend" -ScriptBlock {
    param($nd, $vite, $cwd)
    Set-Location $cwd
    & $nd $vite 2>&1
} -ArgumentList $node, $viteBin, (Join-Path $root "frontend")

# --- Apri browser dopo che Vite e pronto ---
$browserOpened = $false

# --- Cleanup all'uscita ---
$cleanup = {
    Write-Host ""
    Write-Host "[App] Arresto in corso..." -ForegroundColor Yellow
    foreach ($j in Get-Job -Name "app-*" -ErrorAction SilentlyContinue) {
        Stop-Job -Job $j -ErrorAction SilentlyContinue
        Remove-Job -Job $j -Force -ErrorAction SilentlyContinue
    }
    # Kill processi figli orfani (uvicorn, node)
    Get-Process -Name python, node -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $script:python -or $_.Path -eq $script:node } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "[App] Fermato." -ForegroundColor Green
}
Register-EngineEvent PowerShell.Exiting -Action $cleanup | Out-Null

try {
    while ($true) {
        # Stream output di entrambi i job con prefisso colorato
        $beOut = Receive-Job -Job $backendJob -ErrorAction SilentlyContinue
        if ($beOut) {
            foreach ($line in $beOut) {
                Write-Host "[BE] " -ForegroundColor Cyan -NoNewline
                Write-Host $line
            }
        }
        $feOut = Receive-Job -Job $frontendJob -ErrorAction SilentlyContinue
        if ($feOut) {
            foreach ($line in $feOut) {
                Write-Host "[FE] " -ForegroundColor Magenta -NoNewline
                Write-Host $line
                if (-not $browserOpened -and ($line -match "Local:\s+http" -or $line -match "ready in")) {
                    Start-Sleep -Milliseconds 400
                    Start-Process "http://localhost:5173?replay"
                    $browserOpened = $true
                }
            }
        }
        if ($backendJob.State -ne "Running" -and $frontendJob.State -ne "Running") {
            Write-Host "[App] Entrambi i processi sono terminati." -ForegroundColor Yellow
            break
        }
        Start-Sleep -Milliseconds 250
    }
} finally {
    & $cleanup
}
