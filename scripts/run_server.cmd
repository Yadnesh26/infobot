@echo off
rem Runs the bot in this window and also keeps a copy of everything it logs in logs\server.log.
rem Started by scripts\start_demo.ps1; you can also double-click it.
title InfoBot server
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONUNBUFFERED=1
rem 127.0.0.1 only: the tunnel is the one thing that should reach the bot, not the rest of your network.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath 'logs\server.log' -Append"
echo.
echo The server stopped. Press any key to close this window.
pause >nul
