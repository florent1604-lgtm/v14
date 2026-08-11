@echo off
title V14 Collab Terminal
cd /d "%~dp0"
echo.
echo   V14 Collab Terminal - Collaboration multi-LLM
echo   ============================================
echo.
.venv\Scripts\python.exe tools\collab_terminal\server.py %*
pause
