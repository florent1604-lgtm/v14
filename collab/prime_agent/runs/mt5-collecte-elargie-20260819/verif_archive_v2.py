
import json, glob, os
from datetime import datetime, timezone
import pandas as pd, pyarrow.parquet as pq
base = r"C:\Users\flore\Desktop\V14\results\barres"

# 1. schema de tous les fichiers
mauvais=[]; n=0
for fp in glob.glob(os.path.join(base,"*","*.parquet")):
    n+=1
    m = pq.ParquetFile(fp).schema_arrow.metadata or {}
    if m.get(b"schema_version") != b"2": mauvais.append(fp)
print(f"fichiers: {n}, hors schema v2: {len(mauvais)}")

# 2. compagnon
meta = json.load(open(os.path.join(base,"_metadonnees.json")))
tfs = meta["timeframes"]
print("compagnon:", {tf: len(v) for tf,v in tfs.items()})

def d(ts): return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else None

lignes=[]
for tf in ["M1","M5","M15","H1","H4","D1"]:
    tot=sum(v["barres"] for v in tfs[tf].values())
    rec=sum(v["reconstruites"] for v in tfs[tf].values())
    deb=min(v["debut_utc"] for v in tfs[tf].values())
    print(f"{tf}: {len(tfs[tf])} symboles, {tot} barres, {rec} reconstruites ({100*rec/tot:.2f}%), plus ancienne {d(deb)}")

# 3. symboles dont le debut UTILE est tardif (inexploitables en walk-forward)
print("\nDEBUT UTILE TARDIF (posterieur a 2024) :")
for tf in ["M15","H1"]:
    tard = {s: d(v["debut_utile_utc"]) for s,v in tfs[tf].items()
            if v["debut_utile_utc"] and v["debut_utile_utc"] > 1704067200}
    inutil = [s for s,v in tfs[tf].items() if v["debut_utile_utc"] is None]
    print(f" {tf}: {len(tard)} tardifs, {len(inutil)} entierement fabriques")
    for s,dt in sorted(tard.items(), key=lambda kv: kv[1])[:15]:
        print(f"   {s:<12} {dt}")

# 4. controle horloge sur trois symboles
for sym in ["EURUSD","XAUUSD","US500"]:
    df = pq.read_table(os.path.join(base,"M15",sym+".parquet"), columns=["time_utc"]).to_pandas()
    ts = pd.to_datetime(df["time_utc"], unit="s", utc=True)
    g = ts[ts.diff() > pd.Timedelta("6h")]
    hiver = g[g.dt.month.isin([12,1,2])].dt.hour.value_counts().to_dict()
    ete   = g[g.dt.month.isin([6,7,8])].dt.hour.value_counts().to_dict()
    print(f"\n{sym} ouvertures hebdo -> hiver {hiver} / ete {ete}")
