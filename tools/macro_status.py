"""Etat du flux macro en ligne de commande — lecture seule, aucun ordre.

Sert a deux choses, et a rien d'autre : verifier qu'une source repond vraiment,
et voir ce que le systeme croit du calendrier AVANT de l'armer. L'option
``--refresh`` declenche une lecture ; c'est la seule action de l'outil, et elle
n'ecrit rien d'autre que le cache du processus.

Usage :
    .venv/Scripts/python tools/macro_status.py --refresh --upcoming 3
    .venv/Scripts/python tools/macro_status.py --json --symbol EURUSD

Code de sortie : 0 si le risque autorise un nouveau risque, 1 sinon. Un script
d'armement peut donc s'en servir comme d'une porte, sans lire de texte.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from titanium.macro import build_feed, get_cache, load_policy, macro_risk
from titanium.macro.telemetry import macro_telemetry


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    analyseur = argparse.ArgumentParser(description="Etat du flux macro de Titanium V14.")
    analyseur.add_argument("--refresh", action="store_true",
                           help="relire la source avant d'afficher")
    analyseur.add_argument("--json", action="store_true",
                           help="sortie machine (le meme bloc que le tableau de bord)")
    analyseur.add_argument("--symbol", default="",
                           help="ne considere que les devises de ce symbole (ex: EURUSD)")
    analyseur.add_argument("--upcoming", type=int, default=0, metavar="JOURS",
                           help="liste les publications a venir sur N jours")
    return analyseur.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    politique = load_policy()
    cache = get_cache()

    if args.refresh:
        reussi = build_feed(policy=politique, cache=cache).refresh_blocking()
        if not reussi:
            print(f"lecture macro en echec : {cache.view().last_error}", file=sys.stderr)
        elif not args.json:
            print("lecture macro : OK")

    bloc = macro_telemetry(
        macro_risk(symbols=args.symbol or None, policy=politique, cache=cache),
        policy=politique,
        view=cache.view(),
    )
    bloc["enabled"] = politique.enabled

    if args.json:
        print(json.dumps(bloc, indent=2, ensure_ascii=False))
    else:
        etat = "actif" if politique.enabled else "ETEINT"
        print(f"flux macro   : {etat} (fournisseur {politique.provider})")
        print(f"etat         : {bloc['label']} [{bloc['severity']}] score {bloc['score_pct']} %")
        print(f"risque neuf  : {'AUTORISE' if bloc['allows_new_risk'] else 'REFUSE'}")
        print(f"fraicheur    : {bloc['freshness_pct']} %  imminence {bloc['imminence_pct']} %")
        if bloc["next_event"]:
            print(f"prochaine    : {bloc['next_event']['title']} "
                  f"a {bloc['next_event']['at']}")
        for motif in bloc["reasons"]:
            print(f"  - {motif}")
        vue = cache.view()
        if vue.last_error:
            print(f"derniere erreur: {vue.last_error}")

    if args.upcoming > 0:
        calendrier = cache.view().calendar
        if calendrier is None:
            print("aucun calendrier en cache : lancer avec --refresh", file=sys.stderr)
        else:
            now = datetime.now(timezone.utc)
            fin = now + timedelta(days=args.upcoming)
            print(f"\npublications jusqu'au {fin.isoformat()[:16]}:")
            for evenement in calendrier.events:
                if now <= evenement.scheduled_at <= fin:
                    print(f"  {evenement.scheduled_at.isoformat()[:16]}  "
                          f"{evenement.currency:4} {evenement.impact.name:6} "
                          f"{evenement.title}")

    return 0 if bloc["allows_new_risk"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
