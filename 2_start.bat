@echo off
chcp 65001 >nul
title SAS - running (close this window to stop)
cd /d "%~dp0"
call scripts\find_python.bat
set DATABASE_URL=
set PYTHONUTF8=1
if not exist config.env (echo [X] Run 1_setup.bat first & pause & exit /b 1)
echo.
echo   Open  : https://8.iajaward.org/login  (secure sign-in works over https only)
echo   Close this window to stop.
echo.
"%PY%" serve.py
pause
