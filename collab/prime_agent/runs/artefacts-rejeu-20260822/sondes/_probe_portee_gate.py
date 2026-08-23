
import json
import pandas as pd
from titanium.data.archive_barres import (charger_barres, inventaire,
                                          ArchiveHorsUniversError, ArchiveQualiteError)
MARGE = pd.Timedelta(days=1826)
MIN_HTF = 650
change, inchange, hors = {}, 0, {}
for sym in sorted(inventaire("M15")):
    def charge(strict):
        ltf = charger_barres(sym, "M15", granularite_stricte=strict)
        htf = charger_barres(sym, "H4", depuis_utc=ltf.index[0] - MARGE,
                             granularite_stricte=strict)
        return ltf, htf
    try:
        l0, h0 = charge(False)
    except Exception as e:
        l0 = h0 = None
    try:
        l1, h1 = charge(True)
    except (ArchiveHorsUniversError, ArchiveQualiteError) as e:
        hors[sym] = f"{type(e).__name__}: {e}"
        continue
    if len(h1) < MIN_HTF:
        hors[sym] = f"profondeur HTF {len(h1)} < {MIN_HTF}"
        continue
    if l0 is None:
        hors[sym] = "illisible sans la porte"
        continue
    if len(l0) == len(l1) and len(h0) == len(h1):
        inchange += 1
    else:
        change[sym] = {"M15": [len(l0), len(l1)], "H4": [len(h0), len(h1)],
                       "debut_M15": [str(l0.index[0].date()), str(l1.index[0].date())],
                       "debut_H4": [str(h0.index[0].date()), str(h1.index[0].date())]}
print(json.dumps({"inchanges": inchange, "changes": len(change), "hors_univers": hors,
                  "detail": change}, indent=1))
