import sys
sys.path.insert(0, r"C:\\Users\\flore\\Desktop\\V14")
import pandas as pd
t = pd.read_parquet(r"results/porte_cout_trades.parquet")
v = t[(t.split == "verification") & (t.cost_r < 0.125)]
print("VERIFICATION, porte de cout 0.125 active")
print(v.groupby("family").net_r.agg(["count", "mean", "sum"]).round(4).sort_values("count", ascending=False))
print()
print("hors FX")
vh = v[v.asset_class != "fx"]
print(vh.groupby("family").net_r.agg(["count", "mean", "sum"]).round(4).sort_values("count", ascending=False))
print()
print("reversal par classe (hors porte de cout, verification)")
r = t[(t.split == "verification") & (t.family == "reversal")]
print(r.groupby("asset_class").net_r.agg(["count", "mean"]).round(4))
