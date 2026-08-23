
import json
import pandas as pd
from titanium.data.archive_barres import charger_barres, inventaire
MARGE = pd.Timedelta(days=1826)
AMORCAGE, FENETRE = 250, 400
alertes, mini, ok = [], None, 0
for sym in sorted(inventaire("M15")):
    try:
        ltf = charger_barres(sym, "M15")
        htf = charger_barres(sym, "H4", depuis_utc=ltf.index[0] - MARGE)
    except Exception as e:
        alertes.append((sym, f"{type(e).__name__}"))
        continue
    premiere = ltf.index[min(AMORCAGE, len(ltf) - 1)]
    prefixe = int((htf.index <= premiere).sum())
    if prefixe >= FENETRE:
        ok += 1
        mini = prefixe if mini is None else min(mini, prefixe)
    else:
        alertes.append((sym, f"prefixe HTF {prefixe} < {FENETRE}"))
print(json.dumps({"marge_ok": ok, "min_prefixe": mini, "alertes": alertes}, indent=1))
