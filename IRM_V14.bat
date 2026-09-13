@echo off
REM ==========================================================================
REM  IRM V14 - le flux vivant de la chaine, organe par organe.
REM
REM  Lecture seule : cette page lit les journaux que la boucle ecrit deja.
REM  Elle ne rejoue jamais la chaine et ne prend jamais le verrou MT5 - c'est
REM  ce qui permet de l'ouvrir pendant que la boucle armee tourne.
REM
REM  Ce fichier doit rester en ASCII pur. cmd.exe le lit en page de code ANSI ;
REM  un accent dans un REM casse l'analyse et la ligne finit executee.
REM ==========================================================================

setlocal
cd /d "%~dp0"
title Titanium V14 - IRM

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo  [ECHEC] Interpreteur introuvable : %PY%
    echo.
    pause
    exit /b 1
)

echo.
echo   ==========================================================
echo     IRM V14 - flux vivant     http://localhost:8099
echo   ==========================================================
echo     lecture seule - aucun ordre, aucun LLM, aucun verrou MT5
echo.

start "" http://localhost:8099
"%PY%" -X utf8 tools\irm.py

endlocal
