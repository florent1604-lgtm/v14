"""Lanceur du tableau de bord temps réel — DEMO MT5, lecture seule.

Port 8096, pour ne pas heurter ``tools/dashboard.py`` (8095, stdlib) qui
reste inchangé. Écoute uniquement sur la boucle locale : ce tableau de bord
montre l'état d'un compte, il n'a rien à faire sur une interface réseau.

Usage :
    .\\.venv\\Scripts\\python.exe tools/dashboard_live.py
    .\\.venv\\Scripts\\python.exe tools/dashboard_live.py --port 8097
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--symbole", default="US500")
    ap.add_argument("--timeframe", default="M15")
    args = ap.parse_args()

    import uvicorn

    from titanium.web import live_engine
    from titanium.web.dashboard_app import app

    moteur = live_engine.get_engine()
    moteur.symbole = args.symbole
    moteur.timeframe = args.timeframe

    print(f"Tableau de bord V14 — http://127.0.0.1:{args.port}")
    print(f"  actif      : {args.symbole} {args.timeframe}")
    print("  mode       : LECTURE SEULE (aucun ordre, aucun armement)")
    print("  bascule    : le passage au réel ne passe pas par cette interface")

    # host figé sur la boucle locale : pas d'option en ligne de commande pour
    # l'ouvrir au réseau, c'est volontaire.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
