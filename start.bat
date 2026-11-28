@echo off
rem Double-click to run the attendance app for the class demo (phones over HTTPS).
rem   start.bat          phone check-in
rem   start.bat kiosk    door camera
rem Everything it does is in scripts\demo.py. Press Ctrl+C to stop.
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
    echo No venv found here: set the project up first, see README "Quick start".
    pause
    exit /b 1
)
venv\Scripts\python.exe scripts\demo.py %*
pause
