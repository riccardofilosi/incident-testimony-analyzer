# Setup automatico: venv Python, dipendenze, frontend
# Idempotente: pu0 essere rilanciato senza danni.
# Uso:
#   .\setup.ps1            # setup completo, build frontend salta
#   .\setup.ps1 -Build     # setup completo + frontend build di produzione

param(
    [switch]$Build = $false,
    [switch]$SkipFfmpeg = $false
)

# Usiamo "Continue" perche' pip/npm spesso scrivono WARNING su stderr che
# PowerShell tratterebbe come errore terminale con "Stop". Controlliamo a mano
# $LASTEXITCODE dopo ogni invocazione native.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Forza UTF-8 in console (per banner ASCII e log Python)
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    chcp 65001 | Out-Null
} catch { }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Section($title) {
    Write-Host ""
    Write-Host "===========================================" -ForegroundColor DarkGray
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host "===========================================" -ForegroundColor DarkGray
}

function Ok($msg)   { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

# -- 1. Verifica Python -------------------------------------------------------
Section "1. Python"
$pythonCmd = $null
foreach ($c in @(
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
)) {
    if (Test-Path $c) { $pythonCmd = $c; break }
}
if (-not $pythonCmd) {
    try { $pythonCmd = (Get-Command python -ErrorAction Stop).Source } catch { $pythonCmd = $null }
}
if (-not $pythonCmd) {
    Fail "Python non trovato. Installa Python 3.13+ da https://python.org/ e rilancia."
    exit 1
}
$pyVer = & $pythonCmd --version 2>&1
Ok "Trovato: $pythonCmd ($pyVer)"

# Estrai versione major.minor per check minimo 3.13
$verMatch = [regex]::Match($pyVer, "(\d+)\.(\d+)")
if ($verMatch.Success) {
    $major = [int]$verMatch.Groups[1].Value
    $minor = [int]$verMatch.Groups[2].Value
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 13)) {
        Warn "Python $major.$minor rilevato. Consigliato 3.13+. Continuo comunque."
    }
}

# -- 2. Crea venv -------------------------------------------------------------
Section "2. Virtual environment"
$venvPath = Join-Path $root ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvPip    = Join-Path $venvPath "Scripts\pip.exe"

# Detection venv corrotto: il python.exe del venv punta a un Python che non esiste
# (succede quando si copia il .venv da un altro PC con username diverso).
# Wrappiamo in try/catch perche' $ErrorActionPreference="Stop" altrimenti uscirebbe.
$venvBroken = $false
if (Test-Path $venvPython) {
    try {
        $null = & $venvPython -c "import sys" 2>&1
        if ($LASTEXITCODE -ne 0) { $venvBroken = $true }
    } catch {
        $venvBroken = $true
    }
    if ($venvBroken) {
        Warn ".venv presente ma corrotto (probabilmente creato su un altro PC). Lo ricreo."
    }
}
if ($venvBroken -and (Test-Path $venvPath)) {
    Remove-Item -Recurse -Force $venvPath -ErrorAction SilentlyContinue
}
if (Test-Path $venvPython) {
    Ok ".venv gia esiste e funziona, salto creazione"
} else {
    Write-Host "  Creazione venv in $venvPath ..." -ForegroundColor DarkGray
    & $pythonCmd -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Fail "Creazione venv fallita. Verifica che Python supporti il modulo 'venv'."
        exit 1
    }
    Ok "venv creato"
}

# -- 3. Dipendenze backend ----------------------------------------------------
Section "3. Dipendenze Python"
Write-Host "  Upgrade pip..." -ForegroundColor DarkGray
& $venvPython -m pip install --upgrade pip --disable-pip-version-check 2>&1 | Out-Null
Write-Host "  Installazione requirements.txt..." -ForegroundColor DarkGray
& $venvPip install -r (Join-Path $root "requirements.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Fail "pip install ha riportato errori. Controlla i log sopra."
    exit 1
}
Ok "Dipendenze Python installate"

# Verifica import critici
$importTest = & $venvPython -c "import fastapi, uvicorn, google.genai, openpyxl, pydub, librosa, soundfile, PyPDF2; print('OK')" 2>&1
if ($importTest -like "*OK*") {
    Ok "Import critici verificati (fastapi, gemini, librosa, pydub)"
} else {
    Warn "Alcuni import falliti: $importTest"
}

# -- 4. ffmpeg ----------------------------------------------------------------
Section "4. ffmpeg"
if ($SkipFfmpeg) {
    Warn "Skip ffmpeg richiesto da flag -SkipFfmpeg"
} else {
    $ffmpegOk = $false
    try {
        $null = Get-Command ffmpeg -ErrorAction Stop
        $ffmpegOk = $true
    } catch {
        # cerca anche nei path winget
        $candidate = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ChildItem -Path $_.FullName -Directory -ErrorAction SilentlyContinue } |
            ForEach-Object { Join-Path $_.FullName "bin\ffmpeg.exe" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($candidate) {
            Ok "ffmpeg installato in $candidate (riavvia la shell per averlo in PATH)"
            $ffmpegOk = $true
        }
    }
    if ($ffmpegOk) {
        Ok "ffmpeg presente"
    } else {
        Warn "ffmpeg non trovato. Pydub fallira la pulizia audio (la pipeline funziona comunque, ma con audio meno pulito)."
        $resp = Read-Host "  Vuoi installarlo con winget? [s/N]"
        if ($resp -match "^[sS]") {
            Write-Host "  Installazione ffmpeg via winget (puo richiedere 2-3 min)..." -ForegroundColor DarkGray
            winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent
            if ($LASTEXITCODE -eq 0) {
                Ok "ffmpeg installato. Riavvia la shell (o usa AVVIA.bat che lo trova automaticamente)."
            } else {
                Warn "winget ha riportato errori. Installa manualmente da https://ffmpeg.org/"
            }
        } else {
            Warn "Salto installazione ffmpeg"
        }
    }
}

# -- 5. Node + dipendenze frontend -------------------------------------------
Section "5. Frontend (Node + npm)"
$nodeCmd = $null
foreach ($c in @(
    "C:\Program Files\nodejs\node.exe",
    "C:\Program Files (x86)\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe"
)) {
    if (Test-Path $c) { $nodeCmd = $c; break }
}
if (-not $nodeCmd) {
    try { $nodeCmd = (Get-Command node -ErrorAction Stop).Source } catch { $nodeCmd = $null }
}
if (-not $nodeCmd) {
    Fail "Node.js non trovato. Installa da https://nodejs.org/ e rilancia."
    exit 1
}
Ok "Trovato: $nodeCmd ($(& $nodeCmd --version))"

$frontendDir = Join-Path $root "frontend"
$nodeModules = Join-Path $frontendDir "node_modules"

# Detection node_modules incompleti: cartella presente ma autoprefixer mancante
# (succede con zip parziali o npm install interrotto).
$nodeBroken = $false
if (Test-Path $nodeModules) {
    $autopfx = Join-Path $nodeModules "autoprefixer\package.json"
    $vitepkg = Join-Path $nodeModules "vite\package.json"
    if (-not (Test-Path $autopfx) -or -not (Test-Path $vitepkg)) {
        $nodeBroken = $true
        Warn "node_modules presente ma incompleto (manca autoprefixer o vite). Lo ricreo."
    }
}
if ($nodeBroken) {
    Remove-Item -Recurse -Force $nodeModules -ErrorAction SilentlyContinue
    Remove-Item -Force (Join-Path $frontendDir "package-lock.json") -ErrorAction SilentlyContinue
}
if (Test-Path $nodeModules) {
    Ok "node_modules gia esiste e completo, salto npm install"
} else {
    Write-Host "  npm install in frontend/ (puo richiedere 1-3 min)..." -ForegroundColor DarkGray
    Push-Location $frontendDir
    npm install --silent
    $npmExit = $LASTEXITCODE
    Pop-Location
    if ($npmExit -ne 0) {
        Fail "npm install fallito (exit $npmExit). Verifica connessione internet o antivirus."
        exit 1
    }
    # Verifica post-install: autoprefixer DEVE esistere
    if (-not (Test-Path (Join-Path $nodeModules "autoprefixer\package.json"))) {
        Fail "npm install ha completato ma autoprefixer manca. Riprova o controlla npm proxy/cache."
        exit 1
    }
    Ok "Dipendenze frontend installate"
}

if ($Build) {
    Write-Host "  npm run build in frontend/ ..." -ForegroundColor DarkGray
    Push-Location $frontendDir
    npm run build
    $buildExit = $LASTEXITCODE
    Pop-Location
    if ($buildExit -ne 0) {
        Warn "Build frontend fallita (exit $buildExit). Il dev server (npm run dev) dovrebbe comunque funzionare."
    } else {
        Ok "Build di produzione completata in frontend/dist/"
    }
} else {
    Write-Host "  (skip build di produzione - usa -Build per generare dist/)" -ForegroundColor DarkGray
}

# -- 6. Verifica finale -------------------------------------------------------
Section "Setup completato"
Write-Host ""
Write-Host "  Prossimi passi:" -ForegroundColor Green
Write-Host "    1. Doppio-click su AVVIA.bat (apre BE + FE + browser in 1 finestra)" -ForegroundColor White
Write-Host "    2. Vai in Impostazioni e inserisci la tua API key Gemini" -ForegroundColor White
Write-Host "    3. Carica un evento dalla sezione 'Nuovo evento'" -ForegroundColor White
Write-Host ""
Write-Host "  In caso di problemi, leggi README.md sezione Troubleshooting." -ForegroundColor DarkGray
Write-Host ""
