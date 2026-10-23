@echo off
cd /d "%~dp0"
net session >nul 2>nul || (echo [X] Run as administrator. & pause & exit /b 1)
schtasks /end /tn "SAS Website" >nul 2>nul
schtasks /delete /f /tn "SAS Website" >nul 2>nul
schtasks /delete /f /tn "SAS Hourly Backup" >nul 2>nul
schtasks /delete /f /tn "SAS Failover Watchdog" >nul 2>nul
echo Stopped and removed the SAS tasks.
pause
