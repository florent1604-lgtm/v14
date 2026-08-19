
import sys
from pathlib import Path
import pyarrow.parquet as pq
import pandas as pd
RACINE = Path(r"C:\Users\flore\Desktop\V14")
for tf in ("M15", "H4"):
    f = RACINE / "results" / "barres" / tf / "EURUSD.parquet"
    t = pq.read_table(f)
    print(tf, "metadonnees :", {k.decode(): v.decode() for k, v in t.schema.metadata.items()})
    df = t.to_pandas()
    df["dt"] = pd.to_datetime(df["time_utc"], unit="s", utc=True)
    print("  ", len(df), "barres", df["dt"].min(), "->", df["dt"].max())
    print("   minutes distinctes :", sorted(df["dt"].dt.minute.unique())[:8])
    print("   doublons de temps :", int(df["time_utc"].duplicated().sum()))
    print("   monotone :", bool(df["time_utc"].is_monotonic_increasing))
    print(df.tail(2).to_string())
