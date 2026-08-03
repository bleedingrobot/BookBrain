@echo off
start "EPUB Librarian - Backend" "%~dp0..\backend\start-dev.bat"
cd /d "%~dp0"
start "EPUB Librarian - Frontend" cmd /k npm run dev
timeout /t 3 /nobreak >nul
start http://localhost:5173
