@echo off
rem Start the Profesia job monitor locally (uses data\config.json, data\jobs.db, data\subscribers.json)
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
if not exist "data\config.json" (
    echo Error: data\config.json not found. Please run:
    echo copy data\config.example.json data\config.json
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
.venv\Scripts\python.exe -m profesia_monitor -v

echo.
echo Monitor stopped.
pause
