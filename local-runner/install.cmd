@echo off
REM =====================================================================
REM PayVerify Bot — One-click Windows installer
REM Just double-click this file. It handles everything.
REM =====================================================================
title PayVerify Bot Installer

REM Elevate to admin if not already
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator permissions...
    powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

REM Run the PowerShell installer with execution policy bypassed
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
