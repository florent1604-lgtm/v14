import sys
sys.path.insert(0, r"C:\\Users\\flore\\Desktop\\V14")
import pandas as pd
t = pd.read_parquet(r"results/porte_cout_trades.parquet")
v = t[t.split == "verification"]
seuils = {"crypto":0.30, "indices":0.15, "energie":0.15, "metaux":0.08, "agricole":0.25}
def bilan(nom, sel):
    print(f"{nom:<34} n={len(sel):>7}  moyenne={sel.net_r.mean():+.4f}  somme={sel.net_r.sum():+9.1f}")
bilan("A. aucune porte", v)
bilan("B. porte globale 0.12", v[v.cost_r < 0.12])
bilan("C. globale 0.12 + FX exclu", v[(v.cost_r < 0.12) & (v.asset_class != "fx")])
masque = v.asset_class.map(seuils)
bilan("D. par classe + FX exclu", v[masque.notna() & (v.cost_r < masque.fillna(0))])
print()
print(v.groupby("asset_class").net_r.agg(["count","mean","sum"]).round(4))
