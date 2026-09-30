@echo off
REM Double-click to open the PayVerify Bot dashboard.
REM Starts Docker + the bot if they aren't already running.
title PayVerify Bot

cd /d "%USERPROFILE%\PayVerifyBot\local-runner"

REM Make sure Docker is up
docker info >nul 2>&1
if %errorLevel% neq 0 (
    echo Starting Docker Desktop...
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    :waitdocker
    timeout /t 3 /nobreak >nul
    docker info >nul 2>&1
    if %errorLevel% neq 0 goto waitdocker
)

REM Start the containers if not running
docker compose up -d >nul 2>&1

REM Open the dashboard
start http://localhost:3000
