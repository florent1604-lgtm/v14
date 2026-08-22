
import json
import pandas as pd
from titanium.data.archive_barres import charger_barres, inventaire

marge = pd.Timedelta(days=1826)
alertes = []
resume = {"n": 0, "aucune_troncature": 0, "marge_ok": 0, "min_barres_htf_prefixe": None}
mini = None
for sym in sorted(inventaire("M15")):
    try:
        ltf = charger_barres(sym, "M15")
        htf_full = charger_barres(sym, "H4")
    except Exception as e:
        alertes.append((sym, f"CHARGE {type(e).__name__}: {e}"))
        continue
    resume["n"] += 1
    t0 = ltf.index[0]
    borne = t0 - marge
    premier_eval = ltf.index[min(250, len(ltf) - 1)]
    droppes = int((htf_full.index < borne).sum())
    prefixe = int(((htf_full.index >= borne) & (htf_full.index <= premier_eval)).sum())
    if droppes == 0:
        resume["aucune_troncature"] += 1
    elif prefixe >= 400:
        resume["marge_ok"] += 1
        mini = prefixe if mini is None else min(mini, prefixe)
    else:
        alertes.append((sym, f"troncature {droppes} barres, prefixe HTF {prefixe} < 400"))
resume["min_barres_htf_prefixe"] = mini
print(json.dumps({"resume": resume, "alertes": alertes}, indent=1))
