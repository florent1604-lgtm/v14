
import numpy as np, pandas as pd
for sym, tf in (("COFFEE.fs","M15"), ("DJ30.fs","H4"), ("EURUSD","H4")):
    raw = pd.read_parquet(f"results/barres/{tf}/{sym}.parquet")
    t = raw["time_utc"].to_numpy()
    print(sym, tf, "lignes", len(t), "monotone:", bool(np.all(np.diff(t) > 0)),
          "doublons:", int(len(t) - len(np.unique(t))))
    ts = np.sort(np.unique(t)); d = np.diff(ts)
    inte = (d[:-1]==86400) & (d[1:]==86400)
    print("   apres tri: journalieres(interieur)", int(inte.sum()),
          "| zone", (pd.to_datetime(ts[1:-1][inte].min(), unit="s", utc=True).date(),
                     pd.to_datetime(ts[1:-1][inte].max(), unit="s", utc=True).date()) if inte.any() else None)
