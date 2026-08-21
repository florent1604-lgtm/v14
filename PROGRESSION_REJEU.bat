@echo off
REM Barre de progression du rejeu de l'univers. Lecture seule : n'ecrit rien,
REM ne lance aucun lot, ne touche a aucun service. Ctrl-C pour quitter.
cd /d "%~dp0"
title Progression du rejeu V14
.venv\Scripts\python.exe tools\rejeu_progression.py --suivre --intervalle 60
pause
