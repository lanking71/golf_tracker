@echo off
rem Start the vision engine WITHOUT a window (normal operation).
rem Press Ctrl+C to stop. Log file: logs\engine.log
cd /d "%~dp0"
call venv\Scripts\activate.bat
python main.py
if errorlevel 1 (
    echo.
    echo The engine stopped with an error. See logs\engine.log
    pause
)
