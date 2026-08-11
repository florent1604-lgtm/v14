"""Rejeu historique sur l'univers — le levier de rentabilité.

    .venv\\Scripts\\python.exe tools\\backtest.py
    .venv\\Scripts\\python.exe tools\\backtest.py EURUSD GBPUSD --barres 8000
    .venv\\Scripts\\python.exe tools\\backtest.py --journaliser    # alimente l'edge

Produit trois lectures, dans cet ordre d'importance :

1. **Ce que le coût mange.** Chaque actif est rejoué deux fois, avec et sans
   spread. L'écart est le seul chiffre qui dise si une stratégie a une chance.
2. **La stabilité temporelle.** Trois segments consécutifs. Un edge présent sur
   le premier tiers et absent des deux autres est un artefact.
3. **Le classement par actif**, pour orienter la sélectivité.

`--journaliser` verse les trades dans le journal d'edge — c'est ce qui débloque
la mesure par contexte sans attendre des mois de live.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

UNIVERS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
           "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "ETHUSD", "XAUUSD"]


def ligne(r, sans=None) -> str:
    d = r.resume()
    pf = d["profit_factor"]
    base = (f"  {d['symbol']:9} {d['trades']:4} trades  "
            f"esp {d['esperance_r']:+7.4f} R  "
            f"PF {pf if pf is not None else '∞':>6}  "
            f"win {d['winrate'] * 100:4.1f}%  "
            f"coût {d['cout_moyen_r']:.4f} R")
    if sans is not None and sans.n:
        perte = sans.esperance_r - r.esperance_r
        part = (perte / sans.esperance_r * 100) if sans.esperance_r > 0 else 0
        base += f"   | sans coût {sans.esperance_r:+.4f} R  → le spread mange {part:4.0f} %"
    return base


def main() -> None:
    ap = argparse.ArgumentParser(description="Rejeu historique Titanium V14")
    ap.add_argument("symboles", nargs="*", default=None)
    ap.add_argument("--barres", type=int, default=5000, help="barres LTF")
    ap.add_argument("--ltf", default="M15")
    ap.add_argument("--htf", default="H4")
    ap.add_argument("--pas", type=int, default=1)
    ap.add_argument("--journaliser", action="store_true",
                    help="verse les trades dans le journal d'edge")
    ap.add_argument("--segments", type=int, default=3)
    args = ap.parse_args()

    from titanium.backtest import decouper_walk_forward, journaliser, rejouer
    from titanium.data.mt5_vendor import ensure_symbol, get_rates, shutdown

    syms = [s.upper() for s in (args.symboles or UNIVERS)]
    journal = RACINE / "results" / "trades.ndjson"

    print("═" * 100)
    print(f"  REJEU HISTORIQUE — {args.ltf}/{args.htf} · {args.barres} barres · "
          f"{len(syms)} actifs")
    print("═" * 100)

    tous, total_ecrit = [], 0
    t_debut = time.time()

    for sym in syms:
        try:
            spec = ensure_symbol(sym)
            spread = spec.spread * spec.point
            ltf = get_rates(sym, args.ltf, args.barres)
            htf = get_rates(sym, args.htf, max(1500, args.barres // 3))
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym:9} indisponible ({type(exc).__name__})")
            continue

        t0 = time.time()
        avec = rejouer(sym, ltf, htf, spread=spread, pas=args.pas)
        sans = rejouer(sym, ltf, htf, spread=0.0, pas=args.pas,
                       avec_indicateurs=False)
        print(ligne(avec, sans) + f"   [{time.time() - t0:.0f}s]")

        if avec.n:
            tous.append(avec)
            segs = decouper_walk_forward(avec, args.segments)
            detail = "  ".join(
                f"S{i + 1} {s.esperance_r:+.3f}R/{s.n}" for i, s in enumerate(segs))
            stable = all(s.esperance_r > 0 for s in segs)
            print(f"            walk-forward : {detail}   "
                  f"{'STABLE' if stable else 'instable'}")

        if args.journaliser and avec.n:
            total_ecrit += journaliser(avec, journal)

    shutdown()

    # ── Bilan agrégé.
    n = sum(r.n for r in tous)
    if not n:
        print("\n  aucun trade produit.")
        return
    esp = sum(t.pnl_r for r in tous for t in r.trades) / n
    gains = sum(t.pnl_r for r in tous for t in r.trades if t.pnl_r > 0)
    pertes = -sum(t.pnl_r for r in tous for t in r.trades if t.pnl_r < 0)
    pf = gains / pertes if pertes else float("inf")
    gagnants = sum(1 for r in tous for t in r.trades if t.pnl_r > 0)

    print("─" * 100)
    print(f"  ENSEMBLE : {n} trades · espérance {esp:+.4f} R · PF {pf:.3f} · "
          f"winrate {gagnants / n * 100:.1f}% · {time.time() - t_debut:.0f}s")

    porteurs = sorted((r for r in tous if r.esperance_r > 0),
                      key=lambda r: -r.esperance_r)
    print(f"\n  Actifs à espérance positive : {len(porteurs)}/{len(tous)}")
    for r in porteurs[:6]:
        print(f"    {r.symbol:9} {r.esperance_r:+.4f} R sur {r.n} trades")

    if args.journaliser:
        print(f"\n  {total_ecrit} trades versés dans {journal}")
        print("  → relancer le cockpit : l'edge par contexte et les discriminants")
        print("    ont maintenant de quoi travailler.")
    else:
        print("\n  (rien journalisé — ajouter --journaliser pour alimenter l'edge)")


if __name__ == "__main__":
    main()
