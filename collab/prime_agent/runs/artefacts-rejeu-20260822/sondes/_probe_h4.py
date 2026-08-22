
import glob, os, json
import pandas as pd
p = "results/barres/H4/DJ30.fs.parquet"
df = pd.read_parquet(p)
print(df.columns.tolist(), len(df))
print(df.head(3).to_string())
idx = pd.to_datetime(df["time_utc"], unit="s", utc=True) if "time_utc" in df.columns else df.index
d = df.assign(ts=idx)
bad = d[(d["low"]<=0)|(d["high"]<d["low"])|(d["open"]<=0)|(d["close"]<=0)]
print("OHLC invalides:", len(bad))
print(bad[["ts","open","high","low","close","tick_volume"]].to_string() if len(bad) else "")
