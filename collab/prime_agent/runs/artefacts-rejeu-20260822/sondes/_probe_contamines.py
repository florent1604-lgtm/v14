
import json
import numpy as np, pandas as pd
from titanium.data.archive_barres import charger_barres, inventaire

AMORCAGE, FENETRE = 250, 400
MARGE = pd.Timedelta(days=1826)

def masque_journalier(index):
    sec = index.tz_convert("UTC").tz_localize(None).astype("datetime64[s]").astype("int64")
    d = np.diff(sec) == 86400
    return np.concatenate(([False], d[:-1] & d[1:], [False]))

res = {}
for sym in sorted(inventaire("M15")):
    try:
        ltf = charger_barres(sym, "M15")
        htf = charger_barres(sym, "H4", depuis_utc=ltf.index[0] - MARGE)
    except Exception:
        continue
    m_ltf = masque_journalier(ltf.index)
    n_ltf = int(m_ltf[AMORCAGE:].sum())
    premiere = ltf.index[min(AMORCAGE, len(ltf) - 1)]
    j0 = int(htf.index.searchsorted(premiere, side="right")) - 1
    deb = max(0, j0 - FENETRE + 1)
    m_htf = masque_journalier(htf.index)
    n_htf = int(m_htf[deb:].sum())          # TOUTE la portion HTF lue par le rejeu
    if n_ltf or n_htf:
        fin = htf.index[deb:][m_htf[deb:]].max() if n_htf else None
        res[sym] = {"M15": n_ltf, "H4_lue": n_htf,
                    "derniere_H4": None if fin is None else str(fin.date())}
print(json.dumps({"n": len(res), "detail": res}, indent=1))
