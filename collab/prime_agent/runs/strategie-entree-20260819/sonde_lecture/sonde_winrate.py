# -*- coding: utf-8 -*-
"""Sonde LECTURE SEULE : winrate et PnL net moyen en R par symbole.
N'ecrit que dans son propre dossier de sortie."""
import json, os, sys
from collections import defaultdict, Counter

ROOT = r"C:\Users\flore\Desktop\V14"
RES = os.path.join(ROOT, "results")
OUT = os.path.dirname(os.path.abspath(__file__))

def load(name):
    p = os.path.join(RES, name)
    rows = []
    with open(p, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception as e:
                rows.append({"_parse_error": str(e)})
    return rows

rapport = {}

# ---------- schemas ----------
for name in ["trades.ndjson", "excursions.ndjson", "shadow_prod.ndjson",
             "limit_lifecycle.ndjson", "journal_rejets.ndjson"]:
    rows = load(name)
    champs = Counter()
    for r in rows:
        champs.update(r.keys())
    # dates
    date_keys = [k for k in ("closed_at", "ts_exit", "ts", "at", "ts_open") if champs.get(k)]
    dk = date_keys[0] if date_keys else None
    dates = sorted(str(r.get(dk)) for r in rows if r.get(dk)) if dk else []
    # symboles
    syms = set()
    for r in rows:
        s = r.get("symbol")
        if not s and isinstance(r.get("context"), str):
            s = r["context"].split("|")[0]
        if s:
            syms.add(s)
    rapport[name] = {
        "lignes": len(rows),
        "champs": {k: v for k, v in champs.most_common()},
        "cle_date": dk,
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "n_symboles": len(syms),
        "symboles": sorted(syms),
    }

# ---------- point 5 : winrate / PnL par symbole sur trades.ndjson ----------
trades = load("trades.ndjson")
# jointure de secours par ticket -> symbol via excursions
exc = load("excursions.ndjson")
tick2sym = {r.get("ticket"): r.get("symbol") for r in exc if r.get("ticket") and r.get("symbol")}

par_sym = defaultdict(list)
sans_sym = 0
for t in trades:
    ctx = t.get("context")
    sym = None
    if isinstance(ctx, str) and "|" in ctx:
        sym = ctx.split("|")[0]
    if not sym:
        sym = tick2sym.get(t.get("ticket"))
    if not sym:
        sans_sym += 1
        continue
    pnl = t.get("pnl_r")
    if pnl is None:
        continue
    par_sym[sym].append(float(pnl))

lignes = []
for sym, v in par_sym.items():
    n = len(v)
    wins = sum(1 for x in v if x > 0)
    moy = sum(v) / n
    var = sum((x - moy) ** 2 for x in v) / (n - 1) if n > 1 else 0.0
    se = (var / n) ** 0.5 if n > 1 else 0.0
    gains = sum(x for x in v if x > 0)
    pertes = -sum(x for x in v if x < 0)
    lignes.append({
        "symbole": sym, "n": n, "winrate": round(wins / n, 4),
        "wins": wins, "losses": n - wins,
        "pnl_r_moyen": round(moy, 4), "se": round(se, 4),
        "t_stat": round(moy / se, 2) if se > 0 else None,
        "somme_r": round(sum(v), 4),
        "profit_factor": round(gains / pertes, 3) if pertes > 0 else None,
        "n_suffisant_20": n >= 20, "n_suffisant_30": n >= 30,
    })
lignes.sort(key=lambda r: (-r["n"], r["symbole"]))

tous = [x for v in par_sym.values() for x in v]
glob = {
    "n_total": len(tous),
    "trades_sans_symbole": sans_sym,
    "winrate_global": round(sum(1 for x in tous if x > 0) / len(tous), 4) if tous else None,
    "pnl_r_moyen_global": round(sum(tous) / len(tous), 4) if tous else None,
    "somme_r_globale": round(sum(tous), 4),
    "n_symboles": len(par_sym),
    "symboles_n_ge_20": [r["symbole"] for r in lignes if r["n"] >= 20],
    "symboles_n_ge_30": [r["symbole"] for r in lignes if r["n"] >= 30],
}

# repartition par source / mode / classe d'actif
meta = {
    "source": Counter(t.get("source") for t in trades),
    "mode": Counter(t.get("mode") for t in trades),
    "asset_class": Counter(t.get("asset_class") for t in trades),
    "timeframe": Counter(t.get("timeframe") for t in trades),
    "exit_reason": Counter(t.get("exit_reason") for t in trades),
}
meta = {k: dict(v.most_common()) for k, v in meta.items()}

payload = {"schemas": rapport, "par_symbole": lignes, "global": glob, "meta_trades": meta}
with open(os.path.join(OUT, "sortie_winrate.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=1, ensure_ascii=False)

print("=== GLOBAL ===")
print(json.dumps(glob, indent=1, ensure_ascii=False))
print("\n=== PAR SYMBOLE (tri n decroissant) ===")
print(f"{'symbole':10s} {'n':>4s} {'win%':>7s} {'R moy':>8s} {'se':>7s} {'t':>6s} {'sumR':>8s} {'PF':>7s}")
for r in lignes:
    print(f"{r['symbole']:10s} {r['n']:4d} {r['winrate']*100:6.1f}% {r['pnl_r_moyen']:8.4f} "
          f"{r['se']:7.4f} {str(r['t_stat']):>6s} {r['somme_r']:8.3f} {str(r['profit_factor']):>7s}")
print("\n=== META ===")
print(json.dumps(meta, indent=1, ensure_ascii=False))
