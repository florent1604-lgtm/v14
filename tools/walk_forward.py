r"""Pipeline walk-forward glissant par actif pour Titanium V14.

Exemples :

    .venv\Scripts\python.exe tools\walk_forward.py EURUSD XAUUSD
    .venv\Scripts\python.exe tools\walk_forward.py EURUSD --barres 8000 --pas 3

Le pipeline ne place aucun ordre et ne modifie aucun paramètre de stratégie.
Il rejoue la configuration fixe avec le spread courtier, puis écrit un rapport
JSON par actif dans ``results/walk_forward``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

UNIVERS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
    "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "ETHUSD", "XAUUSD",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symboles", nargs="*", default=None)
    parser.add_argument("--barres", type=int, default=12000)
    parser.add_argument("--ltf", default="M15")
    parser.add_argument("--htf", default="H4")
    parser.add_argument("--pas", type=int, default=1,
                        help="pas du rejeu historique en barres")
    parser.add_argument("--is-trades", type=int, default=60)
    parser.add_argument("--oos-trades", type=int, default=20)
    parser.add_argument("--step", type=int, default=20,
                        help="decalage entre deux fenetres, en trades")
    parser.add_argument("--output", default="results/walk_forward")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sortie = Path(args.output)
    if not sortie.is_absolute():
        sortie = RACINE / sortie

    from titanium.analysis.walk_forward import analyser_resultat, ecrire_rapport
    from titanium.backtest import rejouer
    from titanium.data.mt5_vendor import ensure_symbol, get_rates, shutdown

    symboles = [s.upper() for s in (args.symboles or UNIVERS)]
    echecs = 0
    try:
        for symbole in symboles:
            try:
                spec = ensure_symbol(symbole)
                spread = float(spec.spread) * float(spec.point)
                ltf = get_rates(symbole, args.ltf, args.barres)
                htf = get_rates(symbole, args.htf, max(1500, args.barres // 3))
                resultat = rejouer(
                    symbole, ltf, htf, spread=spread, pas=args.pas,
                    avec_indicateurs=False,
                )
                rapport = analyser_resultat(
                    resultat,
                    is_trades=args.is_trades,
                    oos_trades=args.oos_trades,
                    step=args.step,
                )
                cible = ecrire_rapport(rapport, sortie)
                resume = rapport["summary"]
                print(
                    f"{symbole}: {rapport['available_trades']} trades, "
                    f"{resume['windows']} fenetre(s), "
                    f"alerte={resume['alert'] or 'aucune'} -> {cible}"
                )
            except Exception as exc:  # un actif ne bloque jamais le pipeline
                echecs += 1
                print(f"{symbole}: ECHEC {type(exc).__name__}: {exc}")
    finally:
        shutdown()
    return 1 if echecs == len(symboles) else 0


if __name__ == "__main__":
    raise SystemExit(main())
