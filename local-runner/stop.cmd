@echo off
REM Double-click to stop the PayVerify Bot (won't auto-restart until Windows reboot).
cd /d "%USERPROFILE%\PayVerifyBot\local-runner"
docker compose down
echo Bot stopped. Double-click "PayVerify Bot" on your Desktop to start it again.
pause
