# -*- coding: utf-8 -*-
"""Sonde LECTURE SEULE n2 : verdicts d'entree par symbole (shadow_prod)."""
import json, os
from collections import defaultdict, Counter
ROOT = r"C:\Users\flore\Desktop\V14"
RES = os.path.join(ROOT, "results")
OUT = os.path.dirname(os.path.abspath(__file__))

par = defaultdict(Counter)
codes = Counter()
fam = Counter()
pil = Counter()
n = 0
with open(os.path.join(RES, "shadow_prod.ndjson"), encoding="utf-8", errors="replace") as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        n += 1
        s = r.get("symbol") or "?"
        par[s][r.get("verdict_explore")] += 1
        par[s]["_prod_" + str(r.get("verdict_prod"))] += 1
        codes[r.get("code_prod")] += 1
        fam[r.get("famille")] += 1
        pil[r.get("piliers")] += 1

lignes = []
for s, c in par.items():
    tot = sum(v for k, v in c.items() if not k.startswith("_prod_"))
    ent = c.get("ENTER", 0)
    lignes.append({"symbole": s, "evaluations": tot, "enter_explore": ent,
                   "taux_enter": round(ent / tot, 4) if tot else 0.0,
                   "enter_prod": c.get("_prod_ENTER", 0),
                   "block": c.get("BLOCK", 0), "wait": c.get("WAIT", 0)})
lignes.sort(key=lambda r: -r["enter_explore"])

payload = {"n_lignes": n, "n_symboles": len(par), "par_symbole": lignes,
           "codes_prod": dict(codes.most_common()),
           "familles": dict(fam.most_common()),
           "piliers": {str(k): v for k, v in pil.most_common()}}
with open(os.path.join(OUT, "sortie_shadow.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=1, ensure_ascii=False)

print("lignes:", n, "symboles:", len(par))
print("codes_prod:", dict(codes.most_common(6)))
print("familles:", dict(fam.most_common()))
print("piliers:", {str(k): v for k, v in pil.most_common()})
tot_ent = sum(l["enter_explore"] for l in lignes)
tot_ev = sum(l["evaluations"] for l in lignes)
print(f"ENTER explore total: {tot_ent}/{tot_ev} = {tot_ent/tot_ev:.3%}")
print(f"ENTER prod total: {sum(l['enter_prod'] for l in lignes)}")
print("\nTop 20 par ENTER explore:")
print(f"{'sym':12s} {'eval':>6s} {'ENTER':>6s} {'taux':>7s}")
for l in lignes[:20]:
    print(f"{l['symbole']:12s} {l['evaluations']:6d} {l['enter_explore']:6d} {l['taux_enter']*100:6.2f}%")
