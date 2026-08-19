
import json, pandas as pd, pyarrow.parquet as pq
base = r"C:\Users\flore\Desktop\V14\results\barres"
f = pq.ParquetFile(base + r"\M15\EURUSD.parquet")
print("metadonnees:", {k.decode(): v.decode() for k,v in f.schema_arrow.metadata.items()})
df = f.read().to_pandas()
print(df.dtypes.to_dict())
ts = pd.to_datetime(df["time_utc"], unit="s", utc=True)
df = df.assign(ts=ts)
d = df.ts.diff()
s = pd.to_datetime(pd.Series(df.ts[d > pd.Timedelta("6h")].values), utc=True)
tab = pd.crosstab(s.dt.month, s.dt.strftime("%H:%M"))
print(tab.to_string())
print("\ndecalages distincts:", sorted(df.decalage_s.unique()))
meta = json.load(open(base + r"\_metadonnees.json"))
print(json.dumps(meta["timeframes"]["H4"], indent=1)[:600])
