@echo off
chcp 65001 >nul
title J.A.R.V.I.S. Web
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python wurde nicht gefunden. Bitte Python 3.11 oder neuer von https://www.python.org installieren
    echo und beim Installieren "Add Python to PATH" anhaken.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [JARVIS] Erster Start: richte Umgebung ein ...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, anthropic, pptx" >nul 2>nul
if errorlevel 1 (
    echo [JARVIS] Installiere Pakete ...
    ".venv\Scripts\python.exe" -m pip install -r requirements-web.txt
    if errorlevel 1 (
        echo Installation fehlgeschlagen. Bitte Fehlermeldung oben pruefen.
        pause
        exit /b 1
    )
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [JARVIS] Die Datei .env wurde angelegt. Bitte deine API-Keys eintragen und speichern.
    notepad ".env"
)

echo [JARVIS] Starte Weboberflaeche - der Browser oeffnet sich gleich ...
".venv\Scripts\python.exe" -m jarvis.web %*
pause
