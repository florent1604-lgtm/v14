@echo off
REM Ouvre Prime Agent dans la racine V14 (harnais de developpement, pas de trading).
REM
REM TROIS REGLES APPRISES A LA DURE LE 09/08/2026 :
REM
REM 1. prime-agent est lance DIRECTEMENT depuis cmd.exe, jamais a travers Git Bash.
REM    Passer l'interface par "bash -c" depuis une console Windows lui retire son
REM    terminal : l'ecran s'affiche mais aucune touche n'arrive, la fenetre parait
REM    figee. Git Bash reste requis, mais comme shell interne de l'agent seulement
REM    (reglage "shellPath" dans .prime\agent\settings.json).
REM
REM 2. Ce fichier doit rester en ASCII pur. Un caractere accentue ou un tiret
REM    cadratin casse le parseur de cmd.exe ligne par ligne et des commandes
REM    s'executent tronquees.
REM
REM 3. Le fournisseur et le modele sont passes EXPLICITEMENT. Laisse a lui-meme,
REM    Prime Agent bascule en silence sur un autre fournisseur credite quand le
REM    quota gratuit Gemini renvoie 429 -- on recoit alors une erreur Anthropic
REM    incomprehensible au lieu du vrai "rate limit". Mieux vaut un echec lisible.
REM
REM Detail de l'installation et des garde-fous : docs\PRIME_AGENT.md

setlocal
chcp 65001 >nul
cd /d "%~dp0"

where prime-agent >nul 2>&1
if errorlevel 1 (
  echo.
  echo   prime-agent introuvable dans le PATH.
  echo   Voir docs\PRIME_AGENT.md, section installation.
  echo.
  pause
  exit /b 1
)

set "GIT_BASH=C:\Program Files\Git\bin\bash.exe"
if not exist "%GIT_BASH%" (
  echo.
  echo   Git Bash introuvable : %GIT_BASH%
  echo   Prime Agent en a besoin comme shell interne.
  echo   Installer Git for Windows, ou renseigner "shellPath"
  echo   dans .prime\agent\settings.json
  echo.
  pause
  exit /b 1
)

REM --- Kernel IPython. Le bootstrap automatique de la 0.7.1 est casse sous
REM --- Windows (il cherche kernel-venv\bin\python), donc on designe nous-memes
REM --- l'interpreteur. On ne se contente PAS de verifier qu'il existe : un venv
REM --- de kernel peut etre reconstruit a vide par une tentative de bootstrap
REM --- ratee, et l'agent perd alors son unique outil en silence. Le seul test
REM --- qui vaut est "sait-il importer rlm ?". Constate le 09/08/2026.
set "KERNEL_OK="
if not defined PRIME_AGENT_KERNEL_PYTHON goto :kernel_candidats
"%PRIME_AGENT_KERNEL_PYTHON%" -c "import rlm" >nul 2>&1
if not errorlevel 1 set "KERNEL_OK=1"

:kernel_candidats
if defined KERNEL_OK goto :kernel_fin
set "PRIME_AGENT_KERNEL_PYTHON=%USERPROFILE%\.prime\agent\kernel-win\Scripts\python.exe"
"%PRIME_AGENT_KERNEL_PYTHON%" -c "import rlm" >nul 2>&1
if not errorlevel 1 set "KERNEL_OK=1"
if defined KERNEL_OK goto :kernel_fin
set "PRIME_AGENT_KERNEL_PYTHON=%USERPROFILE%\.prime\agent\kernel-venv\Scripts\python.exe"
"%PRIME_AGENT_KERNEL_PYTHON%" -c "import rlm" >nul 2>&1
if not errorlevel 1 set "KERNEL_OK=1"

:kernel_fin
if not defined KERNEL_OK (
  echo.
  echo   ATTENTION : aucun kernel IPython valide trouve.
  echo   L'agent demarrera mais perdra son unique outil.
  echo   Reparation : docs\PRIME_AGENT.md, section "Un bug Windows".
  echo.
)

REM --- Cles lues dans .env, jamais recopiees ailleurs sur disque.
REM --- Les valeurs du .env doivent rester NON entourees de guillemets : toute
REM --- tentative de les retirer ici introduit un guillemet impair sur la ligne
REM --- et cmd tronque la valeur en silence.
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"GOOGLE_API_KEY=" ".env"`) do set "GEMINI_API_KEY=%%B"
  for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"ANTHROPIC_API_KEY=" ".env"`) do set "ANTHROPIC_API_KEY=%%B"
)

REM --- Fournisseur : Claude si l'abonnement Pro/Max est connecte, sinon Gemini.
REM --- Une simple cle API dans auth.json ne declenche PAS la bascule.
if not defined PRIME_V14_CLAUDE_MODEL set "PRIME_V14_CLAUDE_MODEL=claude-sonnet-5"
if not defined PRIME_V14_GEMINI_MODEL set "PRIME_V14_GEMINI_MODEL=gemini-2.5-flash"
set "PRIME_ARGS=--provider google --model %PRIME_V14_GEMINI_MODEL%"
set "MODELE_AFFICHE=%PRIME_V14_GEMINI_MODEL%, cle du .env"
node "tools\prime_auth_probe.mjs" "%USERPROFILE%\.prime\agent\auth.json" >nul 2>&1
if not errorlevel 1 set "PRIME_ARGS=--provider anthropic --model %PRIME_V14_CLAUDE_MODEL%"
if not errorlevel 1 set "MODELE_AFFICHE=%PRIME_V14_CLAUDE_MODEL%, abonnement Claude Pro/Max"

REM --- Un choix explicite de l'utilisateur l'emporte sur tout ce qui precede.
echo %* | findstr /c:"--provider" >nul 2>&1
if not errorlevel 1 (
  set "PRIME_ARGS="
  set "MODELE_AFFICHE=choix explicite via arguments"
)
echo %* | findstr /c:"--model" >nul 2>&1
if not errorlevel 1 (
  set "PRIME_ARGS="
  set "MODELE_AFFICHE=choix explicite via arguments"
)

echo.
echo   ============================================
echo     PRIME AGENT - V14
echo   ============================================
echo     racine    : %CD%
echo     contexte  : AGENTS.md + CLAUDE.md + .prime\agent\skills
echo     modele    : %MODELE_AFFICHE%
echo     regle     : PAPER/DEMO, aucun ordre reel, .env en lecture seule
echo.

REM --- --cwd est indispensable : le kernel et les outils heritent du repertoire
REM --- du DEMON, pas de celui du client. Sans lui, un comptage de fichiers dans
REM --- titanium/ renvoie 0 au lieu de 42, sans la moindre erreur. Une reponse
REM --- fausse et silencieuse est pire qu'un echec. Constate le 09/08/2026.
prime-agent --cwd "%CD%" %PRIME_ARGS% %*
set "PRIME_EXIT=%ERRORLEVEL%"

echo.
if not "%PRIME_EXIT%"=="0" echo   Prime Agent termine avec le code %PRIME_EXIT%.
pause
exit /b %PRIME_EXIT%
