
import json
import pandas as pd, numpy as np
from titanium.data.archive_barres import charger_barres

for sym in ("COFFEE.fs", "COCOA.fs", "SPA35", "IT40"):
    ltf = charger_barres(sym, "M15")
    t = ltf.index.view("int64") // 10**9
    d = np.diff(t) == 86400
    interieur = d[:-1] & d[1:]
    n = int(interieur.sum())
    res = json.load(open(f"results/rejeu_univers/{sym}.json", encoding="utf-8"))
    coupure = pd.Timestamp(res["coupure"])
    idx = np.flatnonzero(interieur)
    fin_zone = ltf.index[idx.max()+1] if n else None
    apres = int((ltf.index[1:-1][interieur] > coupure).sum()) if n else 0
    print(f"{sym:10} M15 chargees={len(ltf):6} debut={ltf.index[0].date()} "
          f"coupure={coupure.date()} | barres journalieres dans la fenetre={n} "
          f"fin_zone={None if fin_zone is None else fin_zone.date()} "
          f"apres_coupure={apres} | artefact n={res['global']['n']} "
          f"verif n={res['verification']['n']} esp_verif={res['verification']['esperance_r']:+.4f}")
