@echo off
REM Tableau de bord de controle Titanium V14 : http://localhost:8095
REM
REM Lecture seule cote decision : aucune route ne passe d'ordre, ne deplace un
REM stop, ni n'appelle un LLM. Le balayage lit MT5 et calcule les features —
REM c'est le seul appel couteux, et il est manuel.
REM
REM Le terminal MetaTrader doit etre ouvert pour que le compte, les positions
REM et le balayage remontent des donnees.

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

start "" http://localhost:8095
".venv\Scripts\python.exe" tools\dashboard.py

pause
