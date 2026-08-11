"""Vérifie qu'OmniRoute peut réellement servir les analystes de V14.

    python tools/omniroute_check.py

Pourquoi une vérification plutôt qu'un simple branchement
---------------------------------------------------------
Une passerelle qui répond « 200 OK » n'est pas une passerelle qui marche.
V13 l'a appris à ses dépens : `/models` renvoyait 200 avec un crédit à zéro,
et le premier vrai run s'est écrasé sur `credit_balance_exhausted`. Depuis,
la règle est de tester une génération réelle, jamais un point de santé.

Le graphe de délibération dépend en plus de l'**appel d'outils**. Beaucoup
de fournisseurs gratuits annoncent la compatibilité OpenAI sans l'implémenter
correctement : la réponse arrive, mais `tool_calls` est vide ou malformé, et
le graphe part en boucle silencieuse au lieu d'échouer franchement. C'est le
mode de panne le plus coûteux, donc il est testé explicitement.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
BASE = "http://localhost:20128/v1"
DELAI = 90


def _appel(chemin: str, charge: dict | None = None, delai: int = DELAI):
    url = f"{BASE}{chemin}"
    donnees = json.dumps(charge).encode() if charge is not None else None
    req = urllib.request.Request(
        url, data=donnees,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer omniroute"},
        method="POST" if donnees else "GET")
    with urllib.request.urlopen(req, timeout=delai) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main() -> int:
    print(f"passerelle : {BASE}\n")

    # ── 1. Joignable ?
    try:
        modeles = _appel("/models", delai=15)
    except urllib.error.URLError as exc:
        print(f"✗ injoignable : {exc.reason}")
        print("\n  OmniRoute n'est pas démarré. Dans un autre terminal :")
        print("    npm install -g omniroute")
        print("    omniroute")
        return 1
    except Exception as exc:                       # noqa: BLE001
        print(f"✗ réponse illisible : {exc}")
        return 1

    liste = [m.get("id", "?") for m in (modeles.get("data") or [])]
    print(f"✓ joignable — {len(liste)} modèles annoncés")
    for m in liste[:8]:
        print(f"    {m}")
    if len(liste) > 8:
        print(f"    … et {len(liste) - 8} autres")
    if not liste:
        print("✗ aucun modèle : la passerelle tourne mais ne route rien")
        return 1

    modele = liste[0]

    # ── 2. Génération réelle. Un `/models` à 200 ne prouve rien.
    print(f"\ngénération réelle sur « {modele} »…")
    t0 = time.time()
    try:
        r = _appel("/chat/completions", {
            "model": modele,
            "messages": [{"role": "user",
                          "content": "Réponds exactement : PONG"}],
            "max_tokens": 16,
        })
    except Exception as exc:                       # noqa: BLE001
        print(f"✗ génération refusée : {str(exc)[:200]}")
        print("  → la passerelle répond mais ne produit rien d'exploitable")
        return 1

    texte = ((r.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    print(f"✓ réponse en {time.time() - t0:.1f} s : {texte.strip()[:60]!r}")

    # ── 3. Appel d'outils. C'est ce dont le graphe dépend vraiment.
    print("\nappel d'outils (dont dépend le graphe de délibération)…")
    outil = {
        "type": "function",
        "function": {
            "name": "get_prix",
            "description": "Renvoie le dernier prix d'un actif",
            "parameters": {
                "type": "object",
                "properties": {"symbole": {"type": "string"}},
                "required": ["symbole"],
            },
        },
    }
    try:
        r = _appel("/chat/completions", {
            "model": modele,
            "messages": [{"role": "user", "content": "Quel est le prix de XAUUSD ?"}],
            "tools": [outil],
            "tool_choice": "auto",
            "max_tokens": 128,
        })
        msg = (r.get("choices") or [{}])[0].get("message") or {}
        appels = msg.get("tool_calls") or []
    except Exception as exc:                       # noqa: BLE001
        print(f"✗ appel d'outils refusé : {str(exc)[:160]}")
        appels = None

    if appels:
        nom = (appels[0].get("function") or {}).get("name")
        print(f"✓ outil appelé : {nom}")
        verdict_outils = True
    else:
        print("✗ aucun `tool_calls` en retour.")
        print("  Le modèle répond mais n'appelle pas d'outil. Le graphe")
        print("  boucherait en silence plutôt que d'échouer franchement —")
        print("  c'est le mode de panne le plus coûteux à diagnostiquer.")
        verdict_outils = False

    # ── Verdict
    print("\n" + "─" * 62)
    if verdict_outils:
        print("VERDICT : exploitable pour les analystes de V14.")
        print("\nÀ ajouter dans .env :")
        print("  TRADINGAGENTS_LLM_PROVIDER=openai")
        print(f"  TRADINGAGENTS_LLM_BACKEND_URL={BASE}")
        print(f"  TRADINGAGENTS_QUICK_THINK_LLM={modele}")
        print(f"  TRADINGAGENTS_DEEP_THINK_LLM={modele}")
    else:
        print("VERDICT : NE PAS brancher le graphe dessus en l'état.")
        print("Choisis dans le tableau de bord OmniRoute un modèle qui")
        print("supporte l'appel d'outils, puis relance cette vérification.")
    print("─" * 62)

    print("\nRappel : le décideur reste déterministe. Un LLM ne peut ni")
    print("ouvrir ni fermer une position — quel que soit le fournisseur")
    print("derrière la passerelle. C'est la règle qui rend ce branchement")
    print("acceptable.")
    return 0 if verdict_outils else 1


if __name__ == "__main__":
    raise SystemExit(main())
