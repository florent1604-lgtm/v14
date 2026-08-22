
import json
import numpy as np, pandas as pd
from titanium.data.archive_barres import charger_barres

for sym in ("COFFEE.fs", "COCOA.fs", "SPA35", "IT40"):
    ltf = charger_barres(sym, "M15")
    t = ltf.index.asi8 // 10**9
    d = np.diff(t) == 86400
    interieur = d[:-1] & d[1:]
    n = int(interieur.sum())
    res = json.load(open(f"results/rejeu_univers/{sym}.json", encoding="utf-8"))
    coupure = pd.Timestamp(res["coupure"])
    horod = ltf.index[1:-1][interieur]
    apres = int((horod > coupure).sum()) if n else 0
    print(f"{sym:10} chargees={len(ltf):6} debut={ltf.index[0].date()} coupure={coupure.date()} "
          f"| journalieres={n} zone={None if not n else (horod.min().date(), horod.max().date())} "
          f"apres_coupure={apres} | verif n={res['verification']['n']} esp={res['verification']['esperance_r']:+.4f}")
