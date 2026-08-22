
import glob, os, json
import pandas as pd, numpy as np
res = {}
for tf in ("H4","H1","M15"):
    files = sorted(glob.glob(f"results/barres/{tf}/*.parquet"))
    touches = {}
    tot = 0; totbar = 0
    for f in files:
        sym = os.path.basename(f)[:-8]
        t = pd.read_parquet(f, columns=["time_utc"])["time_utc"].to_numpy()
        totbar += len(t)
        d = np.diff(t) == 86400
        # interieur d'une serie journaliere : delta precedent ET suivant = 24h
        interieur = d[:-1] & d[1:]
        n = int(interieur.sum())
        if n >= 20:
            idx = np.flatnonzero(interieur)
            fin = pd.to_datetime(t[idx.max()+1], unit="s", utc=True).date().isoformat()
            deb = pd.to_datetime(t[idx.min()], unit="s", utc=True).date().isoformat()
            touches[sym] = {"n": n, "pct": round(100*n/len(t),2), "de": deb, "a": fin, "total": len(t)}
            tot += n
    res[tf] = {"symboles_touches": len(touches), "barres_journalieres": tot, "barres_totales": totbar,
               "top": dict(sorted(touches.items(), key=lambda kv:-kv[1]["n"])[:6]),
               "DJ30.fs": touches.get("DJ30.fs")}
print(json.dumps(res, indent=1))
