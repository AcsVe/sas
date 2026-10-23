@echo off
chcp 65001 >nul
title SAS - Turn the sas system ON
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
schtasks /change /tn "SAS Website" /enable >nul 2>nul || (echo [X] The startup task is missing - run 3_autostart.bat as administrator & pause & exit /b 1)
schtasks /run /tn "SAS Website" >nul
echo.
echo   8.iajaward.org (sas system) is ON again (starts with Windows as before).
echo.
pause
