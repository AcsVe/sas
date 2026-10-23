@echo off
chcp 65001 >nul
title SAS - First time setup
cd /d "%~dp0"
set PYTHONHOME=
set PYTHONPATH=
set DATABASE_URL=
set PYTHONUTF8=1
echo =====================================================
echo   SAS (8.iajaward.org) - setup on this PC
echo =====================================================
where python >nul 2>nul || (echo [X] Python is not installed. Install Python 3.12 64-bit from python.org with "Add python.exe to PATH". & pause & exit /b 1)
if not exist venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv venv || (echo [X] Could not create venv & pause & exit /b 1)
)
call scripts\find_python.bat
echo Installing packages...
"%PY%" -m pip install -q --disable-pip-version-check --timeout 60 -r requirements.txt || (echo [X] pip install failed - check the internet & pause & exit /b 1)

if not exist config.env (
  "%PY%" ops\setup_local.py || (pause & exit /b 1)
) else (
  echo config.env already exists - keeping it. To change settings run: venv\Scripts\python ops\setup_local.py
)

echo.
echo Creating the database, the platform super admin and the first organization...
"%PY%" ops\first_org.py || (echo [X] Database setup failed & pause & exit /b 1)

echo.

echo.
echo Connecting 8.iajaward.org to this PC (Cloudflare)...
"%PY%" ops\cf.py setup

echo.
echo =====================================================
echo   Setup finished.
echo   Next: 3_autostart.bat (right-click - Run as administrator)
echo         to start it with Windows + hourly backup + watchdog
echo   Test now: 2_start.bat then open https://8.iajaward.org/login
echo =====================================================
pause
