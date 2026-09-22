@echo off
REM Avvia backend e frontend. Detection robusta: rileva venv/node_modules MANCANTI
REM o ROTTI (es. creati su un altro PC con path diverso) e li ricostruisce.
cd /d "%~dp0webapp"

REM ====================================================================
REM  Detection venv e node_modules
REM ====================================================================
set NEED_SETUP=0

REM (1) venv mancante
if not exist ".venv\Scripts\python.exe" (
    set NEED_SETUP=1
    echo [check] .venv mancante - serve setup
    goto :runsetup
)

REM (2) venv presente ma rotto: il python.exe del venv punta a un Python
REM che non esiste piu' (succede quando il venv viene zippato da un PC
REM e scompattato su un altro con username diverso).
".venv\Scripts\python.exe" -c "import sys; sys.exit(0)" >nul 2>&1
if errorlevel 1 (
    set NEED_SETUP=1
    echo [check] .venv corrotto - serve ricostruzione
    goto :runsetup
)

REM (3) node_modules mancante
if not exist "frontend\node_modules" (
    set NEED_SETUP=1
    echo [check] frontend\node_modules mancante - serve setup
    goto :runsetup
)

REM (4) node_modules presente ma incompleto: manca autoprefixer (succede
REM con zip corrotti o npm install interrotto).
if not exist "frontend\node_modules\autoprefixer\package.json" (
    set NEED_SETUP=1
    echo [check] node_modules incompleto - serve reinstallazione
    goto :runsetup
)

REM (5) Tutto a posto, salta setup
goto :launch

:runsetup
echo.
echo ============================================================
echo   INSTALLAZIONE DIPENDENZE
echo ============================================================
echo   Operazione una tantum. Richiede 2-5 minuti.
echo.
echo   REQUISITI sul sistema:
echo     - Python 3.13+   (scarica da https://python.org/)
echo     - Node.js 20+    (scarica da https://nodejs.org/)
echo.
echo   Se mancano, il setup ti dira' esattamente cosa fare.
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0webapp\setup.ps1"
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  SETUP FALLITO
    echo ============================================================
    echo  Leggi i messaggi sopra per capire cosa e' andato storto.
    echo  Errori piu' comuni:
    echo    - Python non installato: https://www.python.org/downloads/
    echo    - Node.js non installato: https://nodejs.org/
    echo    - Antivirus che blocca npm: aggiungi la cartella ad esclusioni
    echo    - Connessione internet assente: serve per scaricare pacchetti
    echo.
    echo  Premi un tasto per chiudere.
    pause >nul
    exit /b 1
)
echo.
echo Setup completato. Avvio del sistema...
echo.

:launch
REM ====================================================================
REM  Avvio normale: backend + frontend in una finestra PowerShell
REM ====================================================================
start "" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0webapp\start.ps1"
exit
