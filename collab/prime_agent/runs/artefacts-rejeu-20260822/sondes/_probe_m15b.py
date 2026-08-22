
import numpy as np, pandas as pd
from titanium.data.archive_barres import charger_barres
sym = "COFFEE.fs"
raw = pd.read_parquet(f"results/barres/M15/{sym}.parquet")
print("brut:", len(raw), "reconstruites:", int(raw["reconstruit"].sum()))
t = raw["time_utc"].to_numpy(); d = np.diff(t)==86400
z = raw.iloc[1:-1][d[:-1] & d[1:]]
print("barres journalieres brutes:", len(z), "dont reconstruites:", int(z["reconstruit"].sum()))
ltf = charger_barres(sym, "M15")
print("chargees:", len(ltf), "premiere:", ltf.index[0])
tt = ltf.index.view("int64")//10**9
dd = np.diff(tt)
import collections
print("top deltas (s):", collections.Counter(dd.tolist()).most_common(6))
print("deltas 86400:", int((dd==86400).sum()))
