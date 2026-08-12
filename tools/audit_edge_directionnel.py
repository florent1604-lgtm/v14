"""Audit independant de l'edge directionnel : le signal, ou la tendance ?

POURQUOI
--------
`tools/edge_directionnel.py` (Hermes, 12/08/2026) compare chaque entree V14 a
des instants aleatoires du meme actif, meme heure, meme quartile de
volatilite -- mais avec des **cotes alternes 50/50**. Sur un actif qui monte,
un panier long/short equilibre reste proche de 50 % pendant qu'un signal
majoritairement long profite de la derive. Un ecart favorable peut donc
mesurer une **exposition a la tendance**, pas une qualite de timing.

CE QUE CET AUDIT AJOUTE
-----------------------
Le meme test, decoupe en trois lectures :

* `delta_tous`      : la mesure d'Hermes, reproduite ;
* `delta_meme_cote` : le signal contre les seuls controles allant dans le MEME
                      sens. La derive est alors dans les deux termes et
                      s'annule. C'est le test du TIMING.
* `derive`          : P(+1R avant -1R) d'un tirage long contre un tirage short.
                      50 % = pas de derive ; 60 % = l'actif monte sur la
                      periode et un panier long gagne sans aucun signal.

Si `delta_meme_cote` s'effondre alors que `delta_tous` est large, l'edge
annonce est une prime de tendance. Lecture seule, aucun ordre, aucun seuil.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

from edge_directionnel import (  # noqa: E402
    _atr,
    barrier_outcome,
    matched_random_controls,
    stable_seed,
    volatility_buckets,
)


def _delta(rows: list[dict], filtre) -> dict:
    """Delta apparie signal - controles retenus par `filtre`."""
    ecarts = []
    for row in rows:
        signal = row["signal"]
        controls = [o for o in row["controls"] if o.outcome and filtre(o, signal)]
        if not signal.outcome or not controls:
            continue
        p_signal = 1.0 if signal.outcome == 1 else 0.0
        p_control = sum(o.outcome == 1 for o in controls) / len(controls)
        ecarts.append(p_signal - p_control)
    if not ecarts:
        return {"paires": 0, "delta_pts": None}
    return {"paires": len(ecarts), "delta_pts": 100.0 * statistics.fmean(ecarts)}


def _derive(rows: list[dict]) -> dict:
    """P(+1R avant -1R) des controles, separee par sens. 50/50 = pas de derive."""
    longs = [o for r in rows for o in r["controls"] if o.outcome and o.side > 0]
    shorts = [o for r in rows for o in r["controls"] if o.outcome and o.side < 0]
    def p(xs):
        return 100.0 * sum(o.outcome == 1 for o in xs) / len(xs) if xs else None
    return {"p_long_pct": p(longs), "p_short_pct": p(shorts),
            "n_long": len(longs), "n_short": len(shorts)}


def analyser(symboles: list[str], *, bars: int, step: int, draws: int,
             horizon: int) -> dict:
    from titanium.backtest import rejouer
    from titanium.data.mt5_vendor import ensure_symbol, get_rates, shutdown
    from titanium.edge import asset_class_of

    par_classe: dict[str, list[dict]] = {}
    echecs = []
    try:
        for symbole in symboles:
            try:
                spec = ensure_symbol(symbole)
                frame = get_rates(symbole, "M15", bars)
                htf = get_rates(symbole, "H4", max(1500, bars // 3))
                resultat = rejouer(symbole, frame, htf,
                                   spread=float(spec.spread) * float(spec.point),
                                   pas=step, avec_indicateurs=False)
                seaux, atr = volatility_buckets(frame), _atr(frame)
                positions = {ts: i for i, ts in enumerate(frame.index)}
                occupees = {positions.get(pd.Timestamp(t.bar_entree))
                            for t in resultat.trades}
                occupees.discard(None)
                classe = asset_class_of(symbole) or "inconnue"
                for k, trade in enumerate(resultat.trades):
                    pos = positions.get(pd.Timestamp(trade.bar_entree))
                    if pos is None or int(seaux.iloc[pos]) < 0:
                        continue
                    signal = barrier_outcome(
                        frame, pos, side=trade.side, entry=trade.prix_entree,
                        r_unit=trade.r_unit, horizon=horizon,
                        vol_bucket=int(seaux.iloc[pos]))
                    controles = matched_random_controls(
                        frame, signal_index=frame.index[pos],
                        signal_hour=frame.index[pos].hour,
                        signal_vol_bucket=int(seaux.iloc[pos]),
                        vol_bucket=seaux, r_unit=trade.r_unit, draws=draws,
                        seed=stable_seed(symbole, k), horizon=horizon,
                        excluded_positions=occupees, atr=atr)
                    if controles:
                        par_classe.setdefault(classe, []).append(
                            {"signal": signal, "controls": controles,
                             "symbol": symbole})
            except Exception as exc:  # un actif ne bloque pas l'audit
                echecs.append({"symbole": symbole,
                               "erreur": f"{type(exc).__name__}: {exc}"})
    finally:
        shutdown()

    rapport = {"echecs": echecs, "classes": {}}
    for classe, rows in sorted(par_classe.items()):
        longs = sum(1 for r in rows if r["signal"].side > 0)
        rapport["classes"][classe] = {
            "n_signaux": len(rows),
            "part_longue_pct": round(100.0 * longs / len(rows), 1) if rows else None,
            "delta_tous": _delta(rows, lambda o, s: True),
            "delta_meme_cote": _delta(rows, lambda o, s: o.side == s.side),
            "delta_cote_oppose": _delta(rows, lambda o, s: o.side != s.side),
            "derive": _derive(rows),
        }
    return rapport


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symboles", nargs="*", default=None)
    ap.add_argument("--bars", type=int, default=4000)
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--draws", type=int, default=20)
    ap.add_argument("--horizon", type=int, default=200)
    ap.add_argument("--output", default="results/audit_edge_directionnel.json")
    args = ap.parse_args(argv)
    rapport = analyser(args.symboles or ["UKOIL", "BRENT.fs", "XAUUSD", "XAGUSD"],
                       bars=args.bars, step=args.step, draws=args.draws,
                       horizon=args.horizon)
    cible = Path(args.output)
    if not cible.is_absolute():
        cible = RACINE / cible
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")

    print(f"{'classe':10} {'n':>5} {'long%':>6} {'tous':>8} {'meme cote':>10} "
          f"{'oppose':>8} {'derive L/S':>14}")
    for classe, d in rapport["classes"].items():
        der = d["derive"]
        def f(x):
            return "   n/a" if x is None else f"{x:+6.2f}"
        print(f"{classe:10} {d['n_signaux']:>5} {d['part_longue_pct']:>6} "
              f"{f(d['delta_tous']['delta_pts']):>8} "
              f"{f(d['delta_meme_cote']['delta_pts']):>10} "
              f"{f(d['delta_cote_oppose']['delta_pts']):>8} "
              f"{der['p_long_pct']:>6.1f}/{der['p_short_pct']:.1f}")
    print()
    print("delta en POINTS de pourcentage. 'meme cote' est le test du timing :")
    print("la derive de la periode y figure des deux cotes et s'annule.")
    return 0 if rapport["classes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
