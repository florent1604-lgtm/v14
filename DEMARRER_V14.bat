@echo off
REM ==========================================================================
REM  TITANIUM V14 - demarrage complet
REM
REM  Lance les trois services dans des fenetres separees :
REM    1. tableau de bord   2. analystes LLM   3. boucle de trading (armee)
REM
REM  Ils sont INDEPENDANTS par conception : si les analystes tombent, la
REM  boucle continue avec une conviction neutre ; si le tableau de bord
REM  tombe, le trading n'en sait rien.
REM
REM  ATTENTION : ce fichier doit rester en ASCII pur. cmd.exe le lit en page
REM  de code ANSI ; un accent ou un tiret long y casse l'analyse de REM et
REM  chaque commentaire finit execute comme une commande.
REM ==========================================================================

setlocal
cd /d "%~dp0"
title Titanium V14 - demarrage
color 0B

REM Python redirige vers un fichier reprend sinon CP1252 sous Windows et peut
REM mourir sur les cadres/fleches avant le premier tour. Ces variables sont
REM heritees par les trois services. Chaque script a aussi son propre filet.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY=%~dp0.venv\Scripts\python.exe"

echo.
echo  ==========================================================
echo    TITANIUM V14
echo  ==========================================================
echo.

if not exist "%PY%" (
    echo  [ECHEC] Interpreteur introuvable :
    echo          %PY%
    echo.
    echo  L'environnement virtuel n'est pas installe. Depuis ce dossier :
    echo      python -m venv .venv
    echo      .venv\Scripts\python.exe -m pip install -e ".[mt5]"
    echo.
    pause
    exit /b 1
)

REM  find.exe est appele par son chemin complet : un `find` Unix present dans
REM  le PATH (Git) prendrait la main et ferait echouer ce controle a tort.
REM  Le lanceur restait alors bloque sur le pause ci-dessous sans rien demarrer.
tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | "%SystemRoot%\System32\find.exe" /I "terminal64.exe" >nul
if errorlevel 1 (
    echo  [ECHEC] MetaTrader 5 n'est pas lance.
    echo          Ouvre le terminal, connecte-toi, puis relance ce script.
    echo.
    pause
    exit /b 1
)
echo  [ok]  MetaTrader 5 est ouvert

REM  ---------------------------------------------------------------------
REM  Deja en cours ? Verification par RACINE de processus.
REM
REM  Le python.exe du venv est un LANCEUR-RELAIS : il demarre un second
REM  processus qui fait le travail. Chaque service apparait donc en DEUX
REM  processus. Compter les processus voyait un doublon la ou il n'y en
REM  avait pas, et masquait les vrais - le 08/08/2026, TROIS boucles
REM  armees tournaient et le comptage naif en annoncait deux.
REM
REM  Deux boucles armees sur le meme compte peuvent envoyer le meme ordre :
REM  l'idempotence par barre est PAR PROCESSUS, elle ne protege pas d'un
REM  second bot.
REM  ---------------------------------------------------------------------
"%PY%" tools\etat_services.py >nul 2>&1
if not errorlevel 1 (
    echo.
    echo  [ARRET] Les services tournent deja.
    "%PY%" tools\etat_services.py
    echo.
    echo          Lance ARRETER_V14.bat avant de redemarrer.
    echo.
    pause
    exit /b 1
)

REM  Un service partiellement lance laisserait un doublon a la relance :
REM  on nettoie ce qui traine avant de repartir.
"%PY%" tools\arreter_services.py

REM  Le mur refuserait de toute facon chaque ordre, mais autant le dire ici
REM  plutot que de laisser la boucle tourner une heure en croyant trader.
REM  Le 08/08/2026, MT5 s'etait reconnecte seul au compte REEL pendant la
REM  nuit : la boucle a demarre, s'est dite ARMEE, et n'a rien envoye.
echo  ...  verification du compte
"%PY%" tools\verifier_compte.py
if errorlevel 2 (
    echo.
    echo  [ARRET] Ce n'est pas le compte demo attendu.
    echo          MetaTrader : Fichier ^> Connexion au compte ^> 10055401
    echo.
    echo          Le bot ne passerait aucun ordre : le mur demo/reel
    echo          refuse tout compte autre que celui-la.
    echo.
    pause
    exit /b 1
)
if errorlevel 1 (
    echo  [ECHEC] Compte illisible. MetaTrader est-il connecte ?
    pause
    exit /b 1
)

if not exist "%~dp0results" mkdir "%~dp0results"

echo.
echo  ----------------------------------------------------------
echo   Lancement
echo  ----------------------------------------------------------

start "V14 - tableau de bord" cmd /k ""%PY%" -X utf8 tools\dashboard.py"
echo  [1/3] tableau de bord      http://localhost:8095

REM  Le tableau de bord precharge ses modules et indexe le code : on lui
REM  laisse une avance pour qu'il ne se batte pas avec la boucle pour le
REM  verrou MT5 au demarrage.
timeout /t 6 /nobreak >nul

start "V14 - analystes LLM" cmd /k ""%PY%" -X utf8 tools\analystes.py"
echo  [2/3] analystes LLM        avis hors du chemin critique

timeout /t 3 /nobreak >nul

start "V14 - BOUCLE ARMEE" cmd /k ""%PY%" -X utf8 tools\live_demo.py --armer"
echo  [3/3] BOUCLE ARMEE         ordres reels sur le compte demo

REM  Verification finale : une seule instance de chaque, sinon on le dit.
timeout /t 12 /nobreak >nul
echo.
echo  ----------------------------------------------------------
"%PY%" tools\etat_services.py
echo  ----------------------------------------------------------
echo.
echo   Tableau de bord : http://localhost:8095
echo   Arret propre    : ARRETER_V14.bat
echo.

start "" http://localhost:8095

endlocal
exit /b 0
