
import glob, os, json
import pandas as pd, numpy as np
out = {}
for tf, pas in (("H4", 14400), ("H1", 3600), ("M15", 900), ("D1", 86400), ("M5",300), ("M1",60)):
    files = sorted(glob.glob(f"results/barres/{tf}/*.parquet"))
    touches = []
    tot_jour = 0; tot = 0
    for f in files:
        df = pd.read_parquet(f, columns=["time_utc","open","high","low","close"])
        t = df["time_utc"].to_numpy()
        d = np.diff(t)
        # barre "journaliere" = ecart exact de 86400 s avec la precedente, alors que le TF est < D1
        n_jour = int((d == 86400).sum()) if pas < 86400 else 0
        tot += len(df); tot_jour += n_jour
        inval = int(((df.low<=0)|(df.high<df.low)|(df.open<=0)|(df.close<=0)).sum())
        if n_jour > 0 or inval:
            derniere = pd.to_datetime(t[1:][d == 86400].max(), unit="s", utc=True).isoformat() if n_jour else None
            touches.append((os.path.basename(f)[:-8], n_jour, round(100*n_jour/len(df),2), derniere, inval))
    out[tf] = {"n_fichiers": len(files), "symboles_touches": len(touches), "barres_journalieres": tot_jour,
               "barres_totales": tot, "detail": sorted(touches, key=lambda x:-x[1])[:8],
               "invalides": sum(x[4] for x in touches)}
print(json.dumps(out, indent=1))
