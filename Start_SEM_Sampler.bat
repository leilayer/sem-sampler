@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto deps

echo [SEM Sampler] First run: setting up (needs internet, about 1 minute)...
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Python 3 was not found on this computer.
    echo Please install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during installation, then run this file again.
    pause
    exit /b 1
)

:deps
if exist ".venv\deps_v2.ok" goto run
echo [SEM Sampler] Installing / updating packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install pillow numpy -q
if errorlevel 1 (
    echo Installing packages failed. Check the internet connection and run this file again.
    pause
    exit /b 1
)
echo ok> ".venv\deps_v2.ok"

:run
start "" ".venv\Scripts\pythonw.exe" sem_sampler_app.py
