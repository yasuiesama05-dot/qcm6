@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Utilise le venv du projet s'il existe (Flask y est installé),
rem sinon le Python du système.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" editor.py %*
) else (
    python editor.py %*
)

pause
