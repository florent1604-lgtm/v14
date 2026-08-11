@echo off
REM ==========================================================================
REM  TITANIUM V14 - arret propre
REM
REM  Arrete les trois services, verifie qu'il ne reste RIEN, puis rend
REM  compte de ce qui est encore ouvert sur le compte.
REM
REM  CE QUE CE SCRIPT NE FAIT PAS : fermer les positions.
REM  Une position ouverte porte son stop et son objectif chez le courtier ;
REM  elle reste protegee bot eteint. Les fermer d'office transformerait un
REM  "j'arrete le bot" en "je solde le portefeuille" - deux gestes tres
REM  differents. Pour les fermer : tools\fermer_positions.py --confirmer
REM
REM  ATTENTION : ASCII pur obligatoire, voir DEMARRER_V14.bat.
REM ==========================================================================

setlocal
cd /d "%~dp0"
title Titanium V14 - arret
color 0E

set "PY=%~dp0.venv\Scripts\python.exe"

echo.
echo  ==========================================================
echo    TITANIUM V14 - ARRET
echo  ==========================================================
echo.

if not exist "%PY%" (
    echo  [ECHEC] venv absent - arret impossible par ce script.
    pause
    exit /b 1
)

REM  ---------------------------------------------------------------------
REM  Etat AVANT, pour savoir ce qu'on arrete - et reperer un doublon qui
REM  aurait tourne a notre insu.
REM  ---------------------------------------------------------------------
echo  Etat avant arret :
"%PY%" tools\etat_services.py
echo.

REM  L'arret passe par arreter_services.py, qui tue l'ARBRE de chaque
REM  processus. Sans cela, le parent-relais meurt et l'enfant survit
REM  ORPHELIN - il continue de trader, et il compte alors comme une
REM  nouvelle racine. Constate le 08/08/2026.
echo  ...  arret en cours
"%PY%" tools\arreter_services.py

REM  Un port encore occupe signale un processus survivant, et le prochain
REM  demarrage echouerait en silence sur "adresse deja utilisee".
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8095 -State Listen -ErrorAction SilentlyContinue; if ($c) { Write-Host ('  [!]   port 8095 encore occupe par le pid ' + $c.OwningProcess) } else { Write-Host '  [ok]  port 8095 libere' }"

echo.
echo  ----------------------------------------------------------
echo   Etat du compte
echo  ----------------------------------------------------------
"%PY%" tools\etat_compte.py

echo.
echo  ----------------------------------------------------------
echo   Arret termine. Redemarrage : DEMARRER_V14.bat
echo  ----------------------------------------------------------
echo.
pause
endlocal
exit /b 0
