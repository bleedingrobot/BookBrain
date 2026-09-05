@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0unregister-nightly-task.ps1" %*
echo.
pause
