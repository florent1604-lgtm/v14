
import glob, os, random
import pyarrow.parquet as pq
base = r"C:\Users\flore\Desktop\V14\results\barres"
random.seed(0)
tot_flat={}; tot_rows={}
for tf in ["M1","M5","M15","H1","H4","D1"]:
    files = sorted(glob.glob(os.path.join(base, tf, "*.parquet")))
    sample = files if len(files)<=20 else random.sample(files,20)
    f=0; r=0
    for fp in sample:
        t = pq.read_table(fp, columns=["high","low"])
        h=t.column("high").to_numpy(); l=t.column("low").to_numpy()
        f += int((h==l).sum()); r += len(h)
    tot_flat[tf]=f; tot_rows[tf]=r
    print(tf, f"{f}/{r}", f"{100*f/max(r,1):.2f}%", f"echantillon {len(sample)} fichiers")
print("TOTAL", sum(tot_flat.values()), sum(tot_rows.values()), f"{100*sum(tot_flat.values())/sum(tot_rows.values()):.2f}%")
# cas EURUSD H4 ancien
t = pq.read_table(os.path.join(base,"H4","EURUSD.parquet"))
df = t.to_pandas()
print(df.columns.tolist())
print(df.head(3).to_string())
print("300 premieres plates:", int((df["high"][:300]==df["low"][:300]).sum()))
