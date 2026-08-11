@echo off
title V14 Collab Worker
cd /d "%~dp0"
echo.
echo   V14 Collab Worker - Notifications inter-agents (0 token)
echo   ========================================================
echo.
.venv\Scripts\python.exe tools\collab_terminal\worker.py --interval 300 %*
pause
