@echo off
rem Start the vision engine WITH the setup window (camera setup, color tuning, calibration check).
cd /d "%~dp0"
call venv\Scripts\activate.bat
python main.py --ui
if errorlevel 1 (
    echo.
    echo The program stopped with an error. See logs\engine.log
    pause
)
