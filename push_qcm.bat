@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Envoi des questions modifiees vers GitHub ===
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" push_qcm.py %*
) else (
    python push_qcm.py %*
)
echo.
pause
