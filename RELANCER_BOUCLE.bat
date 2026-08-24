@echo off
REM ==========================================================================
REM  TITANIUM V14 - relance de la SEULE boucle de trading
REM
REM  DEMARRER_V14.bat lance les trois services. Quand le tableau de bord et
REM  les analystes tournent deja, les relancer en cree des doublons. Ce
REM  fichier ne relance que la boucle, ce qu'il faut apres un correctif du
REM  chemin de decision.
REM
REM  ATTENTION : ASCII pur. cmd.exe lit ce fichier en page de code ANSI ;
REM  un accent casse l'analyse des REM.
REM
REM  ARMEE : le mode d'execution est celui de DEMARRER_V14.bat. Il n'ajoute
REM  aucune autorisation ; il restaure l'etat dans lequel la boucle tournait.
REM ==========================================================================
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo  [ECHEC] Interpreteur introuvable : %PY%
    exit /b 1
)

REM find.exe est appele par son chemin complet : un `find` Unix present dans
REM le PATH (Git) prendrait la main et ferait echouer le controle a tort.
tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | "%SystemRoot%\System32\find.exe" /I "terminal64.exe" >nul
if errorlevel 1 (
    echo  [ECHEC] MetaTrader 5 n'est pas lance. Ouvre le terminal et connecte-toi.
    exit /b 1
)

echo  Relance de la boucle V14...
start "V14 - BOUCLE ARMEE" cmd /k ""%PY%" -X utf8 tools\live_demo.py --armer"
endlocal
