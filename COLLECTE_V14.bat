@echo off
REM ==========================================================================
REM  TITANIUM V14 - collecte des donnees de marche
REM
REM  Lance les deux archiveurs dans des fenetres separees :
REM    1. quotes L1 du courtier Axi  (MT5, lecture seule)
REM    2. carnet L2 + transactions Binance  (flux publics, sans cle)
REM
REM  POURQUOI CE FICHIER EXISTE
REM  Six des quinze politiques d'execution classees par la matrice V14
REM  dependent d'un carnet, et leur fidelite plafonne a 0,50 parce que le
REM  simulateur invente ce carnet. Un tick ou un carnet non enregistre ne se
REM  retrouve pas : c'est la seule donnee du projet qui soit irremplacable.
REM
REM  CE QUE CE FICHIER NE FAIT PAS
REM  Il ne passe aucun ordre, n'arme rien, ne demarre ni MT5 ni la boucle de
REM  trading. Les deux collecteurs LISENT.
REM
REM  ATTENTION : ce fichier doit rester en ASCII pur. cmd.exe le lit en page
REM  de code ANSI ; un accent y casse l'analyse de REM et chaque commentaire
REM  finit execute comme une commande.
REM ==========================================================================

setlocal
cd /d "%~dp0"
title Titanium V14 - collecte
color 0E

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0.venv\Scripts\python.exe"

echo.
echo  ==========================================================
echo    TITANIUM V14 - COLLECTE DE DONNEES DE MARCHE
echo  ==========================================================
echo.

if not exist "%PY%" (
    echo  [ECHEC] Interpreteur introuvable : %PY%
    pause
    exit /b 1
)

REM  ---------------------------------------------------------------------
REM  Un collecteur en double ecrit deux fois les memes lignes dans le meme
REM  fichier append-only. Une archive dedoublee ne se repare pas : les
REM  numeros de sequence du carnet deviennent ininterpretables.
REM  ---------------------------------------------------------------------
"%PY%" tools\etat_services.py | findstr /C:"DOUBLON" >nul
if not errorlevel 1 (
    echo  [ARRET] Un collecteur tourne deja en double.
    "%PY%" tools\etat_services.py
    pause
    exit /b 1
)

REM  ---------------------------------------------------------------------
REM  1. Quotes L1 du courtier. Sans MT5 ouvert, il n'y a rien a lire ; on ne
REM     lance pas MT5 pour autant, c'est une decision humaine.
REM  ---------------------------------------------------------------------
tasklist /FI "IMAGENAME eq terminal64.exe" 2>nul | find /I "terminal64.exe" >nul
if errorlevel 1 (
    echo  [saute] MetaTrader 5 n'est pas lance : pas de quotes L1.
    echo          Ouvre le terminal puis relance ce script pour les ajouter.
) else (
    echo  [ok]  MetaTrader 5 est ouvert - archive L1 lancee
    start "V14 quotes L1 Axi" cmd /k ""%PY%" tools\enregistreur_quotes.py --symboles BTCUSD ETHUSD XAUUSD EURUSD US500"
)

REM  ---------------------------------------------------------------------
REM  2. Carnet L2 Binance. Flux publics data-stream.binance.vision : aucune
REM     cle, aucun compte, aucun point d'entree de trading sur cet hote.
REM
REM     Volume mesure le 16/08/2026 : environ 1,4 Go par jour pour deux
REM     symboles en clair, un dixieme apres compaction. Compacter les jours
REM     clos de temps en temps :
REM         .venv\Scripts\python.exe tools\enregistreur_carnet_binance.py
REM             --compacter results\carnet_binance
REM  ---------------------------------------------------------------------
echo  [ok]  archive L2 Binance lancee
start "V14 carnet L2 Binance" cmd /k ""%PY%" tools\enregistreur_carnet_binance.py"

echo.
echo  Deux fenetres sont ouvertes. Les fermer arrete la collecte.
echo  Etat a tout moment :  .venv\Scripts\python.exe tools\etat_services.py
echo.
pause
