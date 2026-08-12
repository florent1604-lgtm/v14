"""A/B contrôlé du secours displacement — mêmes barres, drapeau basculé.

    python tools/ab_displacement.py

Deux passes successives sur des barres RÉELLES ne se comparent pas : entre
elles le marché a bougé, et l'écart mesuré mélange l'effet du code et celui du
marché. Ici les barres sont chargées UNE fois par actif, puis `build_feats`
est appelé deux fois dessus, drapeau éteint puis allumé. Tout écart est alors
imputable au seul code.

Mission Prime ba74e58a. Lecture seule, aucun ordre.
"""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


def main() -> int:
    import MetaTrader5 as mt5  # noqa: N813

    from titanium.data.mt5_vendor import get_rates, get_rates_cache, mt5_session
    from titanium.features import builder
    from titanium.sizing import marches_ouverts

    with mt5_session():
        catalogue = [s.name for s in (mt5.symbols_get() or []) if s.visible]
    ouverts = marches_ouverts(catalogue)
    print(f"catalogue {len(catalogue)} · chargement des barres…")

    paires: list[tuple[str, object, object]] = []
    with mt5_session():
        for sym in catalogue:
            if not ouverts.get(sym, True):
                continue
            try:
                paires.append((sym, get_rates(sym, "M15", 400),
                               get_rates_cache(sym, "H4", 400)))
            except Exception:  # noqa: BLE001
                continue
    print(f"{len(paires)} actifs cotants\n")

    resultats: dict[bool, dict] = {}
    for drapeau in (False, True):
        builder.DISPLACEMENT_FALLBACK = drapeau
        g5 = quorum = n = 0
        sources: dict[str, int] = {}
        for _sym, ltf, htf in paires:
            f = builder.build_feats(ltf, htf, with_indicators=False)
            if not f.get("data_valid"):
                continue
            side = f.get("setup_side")
            if side not in (-1, 1):
                continue
            n += 1
            support = sum((
                bool(f.get("fair_value")),
                int(f.get("liquidity") or 0) == side,
                int(f.get("ote") or 0) == side,
                int(f.get("candle") or 0) == side,
            ))
            if int(f.get("candle") or 0) == side:
                g5 += 1
            if support >= 2:
                quorum += 1
            src = (f.get("_trace") or {}).get("candle_source") or "aucune"
            sources[src] = sources.get(src, 0) + 1
        resultats[drapeau] = {"n": n, "g5": g5, "quorum": quorum,
                              "sources": sources}
    builder.DISPLACEMENT_FALLBACK = True  # on ne laisse pas le module modifié

    av, ap = resultats[False], resultats[True]
    n = av["n"]
    assert n == ap["n"], "les deux passes doivent voir les mêmes setups"

    print(f"SETUPS DIRECTIONNELS (identiques dans les deux passes) : {n}\n")
    print(f"{'':<26} {'éteint':>10} {'allumé':>10} {'écart':>8}")
    print("-" * 56)
    for cle, nom in (("g5", "G5 candle passé"), ("quorum", "quorum 2 atteint")):
        a, b = av[cle], ap[cle]
        print(f"{nom:<26} {a:>4} {100*a/n:5.1f}% {b:>4} {100*b/n:5.1f}% "
              f"{b-a:+8d}")
    print("\nSOURCE DE G5 (passe allumée)")
    for src, c in sorted(ap["sources"].items(), key=lambda x: -x[1]):
        print(f"  {src:<16} {c:>4}   {100*c/n:5.1f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
