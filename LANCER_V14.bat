@echo off
REM Ouvre l'interface terminal de Titanium V14.
REM MT5 est le vendeur prioritaire : le terminal MetaTrader doit etre ouvert
REM pour que les instruments courtier (EURUSD, XAUUSD, US500...) soient servis
REM par le flux courtier. Sinon tout bascule sur yfinance, sans erreur.

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

echo.
echo   ============================================
echo     TITANIUM V14
echo   ============================================
echo     donnees   : MT5 prioritaire, yfinance en secours
echo     execution : voir TITANIUM_EXEC_ENABLED dans .env
echo     sorties   : V14\results\
echo     suivi     : http://localhost:8095  (SUIVI_V14.bat)
echo.

".venv\Scripts\tradingagents.exe"

echo.
pause
