@echo off
chcp 65001 >nul
title JARVIS
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Python fehlt. Bitte von https://www.python.org installieren ^("Add python.exe to PATH" anhaken^).
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Bitte in der Datei .env deinen GROQ_API_KEY eintragen und speichern.
    notepad ".env"
)
".venv\Scripts\python.exe" -m jarvis
pause
