@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Envoi des questions modifiees vers GitHub ===
echo.
python push_qcm.py %*
echo.
pause
