#!/usr/bin/env bash
# Lance Prime Agent (PrimeIntellect-ai/prime-agent) depuis la racine V14.
#
# Trois choses que ce script garantit et qu'un `prime-agent` nu ne garantit pas :
#   1. le repertoire courant est la racine V14 (AGENTS.md, CLAUDE.md, .prime/ et
#      .agents/skills sont decouverts a partir de la);
#   2. la cle Gemini est lue depuis .env sans jamais etre recopiee sur disque
#      (V14 la stocke sous GOOGLE_API_KEY, Prime Agent attend GEMINI_API_KEY);
#   3. le kernel IPython pointe sur son venv Windows -- le bootstrap automatique
#      de la 0.7.1 cherche `kernel-venv/bin/python`, chemin POSIX qui n'existe
#      pas sous Windows, et echoue.
#
# Usage : ./tools/prime_agent_v14.sh [arguments prime-agent]

set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$racine"

if ! command -v prime-agent >/dev/null 2>&1; then
  echo "prime-agent introuvable. Installation :" >&2
  echo "  npm install -g <tarball>  (voir docs/PRIME_AGENT.md)" >&2
  exit 1
fi

# Lit une valeur du .env. Ne l'affiche jamais, ne l'ecrit nulle part.
lire_env() {
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env | head -n1 | tr -d '\r' \
    | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'
}

if [ -z "${GEMINI_API_KEY:-}" ]; then
  cle="$(lire_env GOOGLE_API_KEY)"
  if [ -n "$cle" ]; then
    export GEMINI_API_KEY="$cle"
  else
    echo "Avertissement : aucune cle Gemini trouvee (.env GOOGLE_API_KEY vide)." >&2
    echo "Utiliser /login dans l'interface pour choisir un fournisseur." >&2
  fi
fi

# Claude : la cle du .env est valide mais son solde est a zero (verifie le
# 09/08/2026 : 400 invalid_request_error "credit balance is too low"). Elle est
# quand meme exportee pour que la bascule soit immediate des qu'il y a du
# credit :  PRIME_V14.bat --provider anthropic --model claude-sonnet-5
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  cle="$(lire_env ANTHROPIC_API_KEY)"
  [ -n "$cle" ] && export ANTHROPIC_API_KEY="$cle"
fi

# Abonnement Claude Pro/Max : une fois `/login` fait, auth.json contient une
# entree "anthropic" de type oauth, prioritaire sur toute variable
# d'environnement. Dans ce cas Claude devient le modele par defaut de V14.
# Surcharges : passer --provider/--model, ou PRIME_V14_CLAUDE_MODEL.
choix_explicite=0
for argument in "$@"; do
  case "$argument" in
    --provider|--provider=*|--model|--model=*) choix_explicite=1 ;;
  esac
done

# Le fournisseur est passe EXPLICITEMENT. Laisse a lui-meme, Prime Agent
# bascule en silence sur un autre fournisseur credite quand le quota gratuit
# Gemini renvoie 429 : on recoit alors une erreur Anthropic incomprehensible au
# lieu du vrai "rate limit". Constate le 09/08/2026.
if [ "$choix_explicite" -eq 0 ]; then
  claude_connecte=0
  auth_json="$HOME/.prime/agent/auth.json"
  if [ -f "$auth_json" ]; then
    if command -v cygpath >/dev/null 2>&1; then
      auth_json="$(cygpath -w "$auth_json")"
    fi
    node tools/prime_auth_probe.mjs "$auth_json" 2>/dev/null && claude_connecte=1
  fi

  if [ "$claude_connecte" -eq 1 ]; then
    # claude-opus-5, comme .prime/agent/settings.json. Un drapeau explicite
    # passe au binaire ecrase toujours le reglage projet : les deux doivent
    # nommer le meme modele, sinon le fichier ne sert plus a rien.
    set -- --provider anthropic \
           --model "${PRIME_V14_CLAUDE_MODEL:-claude-opus-5}" "$@"
  else
    echo "Abonnement Claude non detecte : repli sur Gemini." >&2
    echo "Pour retrouver Opus 5 : /login dans l'interface, puis relancer." >&2
    set -- --provider google \
           --model "${PRIME_V14_GEMINI_MODEL:-gemini-2.5-flash}" "$@"
  fi
fi

# Kernel IPython : on ne verifie pas qu'un interpreteur existe, on verifie qu'il
# sait importer rlm. Un venv de kernel peut etre reconstruit a vide par une
# tentative de bootstrap ratee, et l'agent perd alors son unique outil en
# silence. Constate le 09/08/2026.
kernel_valide() {
  [ -n "$1" ] && [ -f "$1" ] && "$1" -c "import rlm" >/dev/null 2>&1
}

kernel_retenu=""
for candidat in \
  "${PRIME_AGENT_KERNEL_PYTHON:-}" \
  "$HOME/.prime/agent/kernel-win/Scripts/python.exe" \
  "$HOME/.prime/agent/kernel-venv/Scripts/python.exe"
do
  if kernel_valide "$candidat"; then
    kernel_retenu="$candidat"
    break
  fi
done

if [ -n "$kernel_retenu" ]; then
  if command -v cygpath >/dev/null 2>&1; then
    kernel_retenu="$(cygpath -w "$kernel_retenu")"
  fi
  export PRIME_AGENT_KERNEL_PYTHON="$kernel_retenu"
else
  echo "Attention : aucun kernel IPython valide (import rlm impossible)." >&2
  echo "L'agent demarrera sans son unique outil. Voir docs/PRIME_AGENT.md." >&2
fi

# Sous mintty (la fenetre Git Bash), node ne recoit pas de console Windows :
# l'interface s'affiche mais aucune touche n'arrive, la fenetre parait figee.
# winpty interpose une vraie console. Inutile en mode -p/--print (pas de TUI),
# inutile aussi depuis cmd.exe ou PowerShell (console native deja presente).
interactif=1
for argument in "$@"; do
  case "$argument" in
    -p|--print) interactif=0 ;;
  esac
done

# --cwd est indispensable : le kernel et les outils heritent du repertoire du
# DEMON, pas de celui du client. Sans lui, un comptage de fichiers dans
# titanium/ renvoie 0 au lieu de 42, sans la moindre erreur. Une reponse fausse
# et silencieuse est pire qu'un echec. Constate le 09/08/2026.
racine_windows="$racine"
if command -v cygpath >/dev/null 2>&1; then
  racine_windows="$(cygpath -w "$racine")"
fi
set -- --cwd "$racine_windows" "$@"

if [ "$interactif" -eq 1 ] && [ -t 0 ] && [ -n "${MSYSTEM:-}" ] \
   && command -v winpty >/dev/null 2>&1; then
  exec winpty prime-agent "$@"
fi

exec prime-agent "$@"
