@echo off
setlocal enabledelayedexpansion
title PayVerify Bot Installer

REM ============================================================
REM Self-bootstrapping installer.
REM Only file you need to download. Fetches everything else.
REM ============================================================

REM --- Elevate to admin ---
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator permissions...
    powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

cls
echo.
echo ============================================================
echo    PayVerify Bot -- one-click Windows installer
echo ============================================================
echo.

REM --- Cache the repo choice in the same folder as this file ---
set "REPO_FILE=%~dp0repo.txt"
if exist "%REPO_FILE%" (
    set /p REPO=<"%REPO_FILE%"
    echo Using GitHub repo: !REPO!
) else (
    echo First-time only: which GitHub repo has your bot code?
    echo   Format: username/repo-name
    echo   Example: itbar/payverify-bot
    echo.
    set /p REPO="Enter repo: "
    if "!REPO!"=="" (
        echo No repo entered. Exiting.
        pause
        exit /b 1
    )
    echo !REPO!>"%REPO_FILE%"
)

REM --- Download the real installer PS script ---
set "PS_URL=https://raw.githubusercontent.com/!REPO!/main/local-runner/install.ps1"
set "PS_PATH=%TEMP%\payverify_install.ps1"

echo.
echo Downloading installer from GitHub...
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri '!PS_URL!' -OutFile '!PS_PATH!' } catch { Write-Host $_.Exception.Message; exit 1 }"

if not exist "!PS_PATH!" (
    echo.
    echo ERROR: Could not download the installer.
    echo   Tried: !PS_URL!
    echo.
    echo Fixes to try:
    echo   1. Check your internet connection
    echo   2. Delete "!REPO_FILE!" and run this file again to re-enter your repo
    echo   3. Make sure your repo is PUBLIC ^(private repos need extra auth^)
    echo   4. Make sure your default branch is called "main" ^(not "master"^)
    echo.
    pause
    exit /b 1
)

REM --- Run it with the repo passed in ---
powershell -NoProfile -ExecutionPolicy Bypass -File "!PS_PATH!" -Repo "!REPO!"
pause
