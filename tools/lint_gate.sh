#!/bin/sh
# Porte de lint UNIQUE du depot. Appelee par .githooks/pre-commit ET par la CI.
#
# Pourquoi ce fichier existe
# --------------------------
# Il y avait deux portes qui ne verifiaient pas la meme chose : le hook lancait
# `ruff check --select E9,F63,F7,F82` sur quatre dossiers et passait toujours ;
# la CI lancait `ruff check .` avec le select strict du pyproject sur tout le
# depot, miroirs de skills vendorises compris, et echouait toujours. Resultat :
# quatre executions CI, quatre echecs, personne ne regardait plus le voyant.
# Une CI rouge en permanence est pire que pas de CI -- elle ne peut plus
# signaler une vraie regression.
#
# Un seul commande, deux appelants : les deux portes ne peuvent plus diverger.
# Elever le niveau se fait ICI, et s'applique alors partout d'un coup.
#
# Ce que cette porte attrape : erreurs de syntaxe (E9), comparaisons et tests
# toujours faux (F63, F7), noms non definis (F82). Autrement dit du code qui
# casse, pas du style. Le reformatage cosmetique du depot est un chantier
# distinct, deliberement hors de cette porte.
set -eu

SELECT="E9,F63,F7,F82"
CIBLES="tradingagents titanium tools tests"

if [ -x ".venv/Scripts/python.exe" ]; then
  RUFF=".venv/Scripts/python.exe -m ruff"
elif [ -x ".venv/bin/python" ]; then
  RUFF=".venv/bin/python -m ruff"
elif command -v ruff >/dev/null 2>&1; then
  RUFF="ruff"
else
  RUFF="python -m ruff"
fi

# shellcheck disable=SC2086
exec $RUFF check --select "$SELECT" $CIBLES "$@"
