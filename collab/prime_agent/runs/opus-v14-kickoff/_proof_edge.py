
import json, pathlib, tempfile, sys
from titanium.edge import TradeJournal, EdgeBook, MIN_SAMPLES
src = pathlib.Path(r"C:\Users\flore\Desktop\V14\results\trades.ndjson.avant_purge")
rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
corrupt = [r for r in rows if abs(r["pnl_r"]) > 100]
print("lignes reelles:", len(rows), "| dont |pnl_r|>100 R :", len(corrupt))
for r in corrupt: print("   ", r["context"], r["pnl_r"])
# Reconstitue une cellule de 20 trades: 19 pertes a -1 R + 1 ligne corrompue reelle
tmp = pathlib.Path(tempfile.mkdtemp())/"trades.ndjson"
lines=[]
for i in range(19):
    lines.append(json.dumps({"context":"EURUSD|long|continuation|3p","pnl_r":-1.0,"ticket":f"t{i}","cost_r":0.0}))
bad = dict(corrupt[0]); bad["context"]="EURUSD|long|continuation|3p"; bad["ticket"]="bad"
lines.append(json.dumps(bad))
tmp.write_text("\n".join(lines), encoding="utf-8")
book = EdgeBook(journal=TradeJournal(path=tmp))
v = list(book.refresh().values())[0]
print("\nMIN_SAMPLES =", MIN_SAMPLES)
print("19 pertes a -1 R + 1 ligne corrompue reelle :")
print("  samples    =", v.samples)
print("  expectancy =", v.expectancy_r, "R")
print("  win_rate   =", v.win_rate)
print("  edge_ok    =", v.edge_ok, "|", v.reason)
