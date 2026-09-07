@echo off
REM Double-click this (or right-click > Run as administrator) to set up the
REM BookBrain nightly Scheduled Task. Runs at 2am by default; to pick another
REM hour, run from a terminal:  register-nightly-task.bat 3
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register-nightly-task.ps1" %*
echo.
pause
