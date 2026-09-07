@echo off
REM Restore epub_librarian.db from a Drive backup.
REM
REM STOP THE BOOKBRAIN BACKEND FIRST (close its window / kill stray uvicorn).
REM Then double-click this for an interactive picker, or from a terminal:
REM   restore-backup.bat --list
REM   restore-backup.bat --latest
REM   restore-backup.bat --date 2026-09-07
REM
REM The current database is renamed to epub_librarian.db.pre-restore-<time>
REM before anything is replaced, so a wrong restore is itself undoable.

setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m app.jobs.restore %*
echo.
pause
