
import json
import numpy as np, pandas as pd
from titanium.data.archive_barres import charger_barres

for sym in ("COFFEE.fs", "COCOA.fs", "SPA35", "IT40"):
    ltf = charger_barres(sym, "M15")
    sec = (ltf.index.tz_convert("UTC").tz_localize(None).astype("datetime64[s]").astype("int64"))
    d = np.diff(sec) == 86400
    interieur = d[:-1] & d[1:]
    n = int(interieur.sum())
    horod = ltf.index[1:-1][interieur]
    res = json.load(open(f"results/rejeu_univers/{sym}.json", encoding="utf-8"))
    coupure = pd.Timestamp(res["coupure"])
    apres = int((horod > coupure).sum()) if n else 0
    print(f"{sym:10} chargees={len(ltf):6} debut={ltf.index[0].date()} coupure={coupure.date()} "
          f"| journalieres={n} ({100*n/len(ltf):.2f}%) zone={horod.min().date()}->{horod.max().date()} "
          f"apres_coupure={apres} | verif n={res['verification']['n']} esp={res['verification']['esperance_r']:+.4f} "
          f"calib n={res['calibration']['n']} esp={res['calibration']['esperance_r']:+.4f}")
