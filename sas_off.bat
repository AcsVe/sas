@echo off
chcp 65001 >nul
title SAS - Turn the sas system OFF
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
REM Stops 8.iajaward.org only - the other systems on this PC keep running.
schtasks /end /tn "SAS Website" >nul 2>nul
schtasks /change /tn "SAS Website" /disable >nul 2>nul
echo.
echo   8.iajaward.org (sas system) is OFF (also after restarting the PC).
echo   Devices keep ringing from their saved schedule.
echo   To turn it back on: sas_on.bat (as administrator)
echo.
pause
