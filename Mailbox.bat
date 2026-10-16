@echo off
title Mailbox
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m mailbox_app.server %*
) else (
  python -m mailbox_app.server %*
)
echo.
echo Mailbox stopped. Press any key to close.
pause >nul
