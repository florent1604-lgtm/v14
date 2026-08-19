
import glob, os, json
import pyarrow.parquet as pq
base = r"C:\Users\flore\Desktop\V14\results\barres"
out={}
for tf in ["M1","M5","M15","H1","H4","D1"]:
    rows=[]
    for fp in sorted(glob.glob(os.path.join(base,tf,"*.parquet"))):
        f=pq.ParquetFile(fp); n=f.metadata.num_rows
        rows.append((os.path.basename(fp)[:-8], n))
    ns=[n for _,n in rows]
    import statistics as st
    exact = sum(1 for n in ns if 99000 <= n <= 101000)
    print(f"{tf}: {len(ns)} fichiers, total {sum(ns)}, min {min(ns)}, max {max(ns)}, mediane {st.median(ns)}, dans [99k,101k]: {exact}")
    out[tf]=rows
json.dump(out, open(r"C:\Users\flore\Desktop\V14\collab\prime_agent\runs\mt5-collecte-elargie-20260819\comptes_par_fichier.json","w"))
