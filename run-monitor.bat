@echo off
rem Start the Profesia job monitor locally (uses data\configs\config.yaml, data\jobs.db, data\subscribers.json)
cd /d "%~dp0"

rem require the virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo Error: Virtual environment not found. Please run:
    echo python -m venv .venv
    echo .venv\Scripts\pip.exe install -r requirements.txt
    pause
    exit /b 1
)

rem require the config
if not exist "data\configs\config.yaml" (
    echo Error: data\configs\config.yaml not found. Please run:
    echo copy data\configs\config.example.yaml data\configs\config.yaml
    echo and fill in your searches, Telegram token and model settings.
    pause
    exit /b 1
)

rem refuse to run next to the Docker monitor: both share data\ and would notify every offer twice
for /f %%i in ('docker compose ps -q --status running monitor 2^>nul') do (
    echo Error: the Docker monitor container is running.
    echo Stop it first with: docker compose stop monitor
    pause
    exit /b 1
)

echo ========================================
echo          Profesia Job Monitor
echo ========================================
echo.
echo Monitor is running. Press Ctrl+C to stop.
echo.

rem -v shows verdict log lines
.venv\Scripts\python.exe -m offer_monitor -v --log monitor.log

echo.
echo Monitor stopped.
pause
