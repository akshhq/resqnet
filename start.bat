@echo off
setlocal enabledelayedexpansion

:: ─────────────────────────────────────────────────────────────────────────────
:: ResQNet — start.bat
:: Usage:  start.bat             interactive simulator
::         start.bat --demo      scripted demo
::         start.bat --no-sim    backend + dashboard only
:: ─────────────────────────────────────────────────────────────────────────────

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "DEMO_MODE=false"
set "NO_SIM=false"
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--demo"   set "DEMO_MODE=true"
if /i "%~1"=="--no-sim" set "NO_SIM=true"
shift
goto :parse_args
:args_done

echo [resqnet] Root: %ROOT%

:: ── Find Python ───────────────────────────────────────────────────────────────
set "PYTHON="
where python  >nul 2>&1 && set "PYTHON=python"
where python3 >nul 2>&1 && if "!PYTHON!"=="" set "PYTHON=python3"
if "!PYTHON!"=="" (
    echo [resqnet] ERROR: Python not found in PATH.
    pause & exit /b 1
)
echo [resqnet] Python: !PYTHON!

:: ── Check packages ────────────────────────────────────────────────────────────
!PYTHON! -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [resqnet] ERROR: fastapi or uvicorn not installed.
    echo [resqnet] Run:  pip install -r "%ROOT%\backend\requirements.txt"
    pause & exit /b 1
)

:: ── Check dashboard folder ────────────────────────────────────────────────────
if not exist "%ROOT%\Trial_Dashboard\index.html" (
    echo [resqnet] ERROR: Could not find Trial_Dashboard\index.html
    pause & exit /b 1
)

:: ── Backend ───────────────────────────────────────────────────────────────────
echo [resqnet] Starting backend  ^> http://127.0.0.1:8000
start "ResQNet Backend" cmd /k "cd /d "%ROOT%\backend" & uvicorn app.main:app --host 0.0.0.0 --port 8000"

echo [resqnet] Waiting for backend to start...
timeout /t 4 /nobreak >nul

:: ── Dashboard ─────────────────────────────────────────────────────────────────
echo [resqnet] Starting dashboard ^> http://localhost:5500
start "ResQNet Dashboard" cmd /k "cd /d "%ROOT%\Trial_Dashboard" & !PYTHON! -m http.server 5500"

:: Wait a moment then open in default browser
timeout /t 2 /nobreak >nul
echo [resqnet] Opening dashboard in browser...
start "" "http://localhost:5500"

:: ── Simulator ─────────────────────────────────────────────────────────────────
if "%NO_SIM%"=="true" (
    echo [resqnet] Simulator skipped.
    echo [resqnet] To start manually: cd simulator ^&^& python simulator.py
    goto :done
)

if not exist "%ROOT%\simulator\simulator.py" (
    echo [resqnet] WARNING: simulator\simulator.py not found. Skipping.
    goto :done
)

cd /d "%ROOT%\simulator"

if "%DEMO_MODE%"=="true" (
    echo [resqnet] Simulator: DEMO mode
    echo.
    !PYTHON! simulator.py --demo
) else (
    echo [resqnet] Simulator: interactive
    echo [resqnet] Controls: p=panic  r=reset  0-3=mode  t=turn  q=quit
    echo.
    !PYTHON! simulator.py
)

:done
echo.
echo [resqnet] ResQNet is running.
echo [resqnet]   Dashboard ^> http://localhost:5500
echo [resqnet]   Backend   ^> http://127.0.0.1:8000
echo [resqnet] Close the Backend and Dashboard windows to stop.
pause