@echo off
setlocal enableextensions
title PayVerify Bot -- Native Windows installer

echo.
echo =====================================================================
echo   PayVerify Bot -- Native Windows installer (no Docker)
echo =====================================================================
echo   This runs the bot directly on your Windows machine so the Chromium
echo   browser can OPEN VISIBLY on your desktop. That is what lets you
echo   approve 2FA / solve captcha for Google when logging into GPay.
echo.
echo   It will:
echo     - Install Python 3.11 (via winget) if you do not have it
echo     - Install MongoDB Community Server 7 (via winget) if missing
echo     - Create a virtual environment in %%USERPROFILE%%\PayVerifyBot-Native
echo     - Install backend requirements + Playwright Chromium
echo     - Ask you for your API keys once
echo     - Start the bot with a visible browser and open the dashboard
echo =====================================================================
echo.

REM Elevate if not admin (winget installs may need it)
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-native.ps1"
pause
