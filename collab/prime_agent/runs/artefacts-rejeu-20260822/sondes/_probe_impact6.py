
import json
import numpy as np, pandas as pd
from titanium.data.archive_barres import charger_barres

AMORCAGE, FENETRE = 250, 400
MARGE = pd.Timedelta(days=1826)
SIX = ["COFFEE.fs", "COCOA.fs", "IT40", "SPA35", "USDCLP", "USDCOP"]

def masque_grossier(index):
    sec = index.tz_convert("UTC").tz_localize(None).astype("datetime64[s]").astype("int64")
    d = np.diff(sec)
    jour = (d % 86400 == 0) & (d >= 86400)
    m = np.zeros(len(index), dtype=bool)
    m[1:-1] = jour[:-1] & jour[1:]
    return m

sortie = {}
for sym in SIX:
    ltf = charger_barres(sym, "M15")
    htf = charger_barres(sym, "H4", depuis_utc=ltf.index[0] - MARGE)
    m_ltf, m_htf = masque_grossier(ltf.index), masque_grossier(htf.index)
    pos = htf.index.searchsorted(ltf.index, side="right") - 1

    # une barre LTF est ATTEINTE si sa propre fenetre LTF ou sa fenetre HTF
    # contient au moins une barre grossiere
    cum_ltf = np.concatenate(([0], np.cumsum(m_ltf)))
    cum_htf = np.concatenate(([0], np.cumsum(m_htf)))
    atteinte = np.zeros(len(ltf), dtype=bool)
    for i in range(AMORCAGE, len(ltf)):
        j = int(pos[i])
        if j < AMORCAGE:
            continue
        a = cum_ltf[i + 1] - cum_ltf[max(0, i - FENETRE + 1)]
        b = cum_htf[j + 1] - cum_htf[max(0, j - FENETRE + 1)]
        atteinte[i] = (a > 0) or (b > 0)
    dates = ltf.index[atteinte]

    trades = [json.loads(l) for l in open(
        f"results/rejeu_univers_brut/{sym}/trades.ndjson", encoding="utf-8") if l.strip()]
    bornes = set(dates.astype(str))
    touches = [t for t in trades if str(pd.Timestamp(t["bar_entree"])) in bornes]
    par_split = {}
    for t in touches:
        par_split[t["split"]] = par_split.get(t["split"], 0) + 1
    sortie[sym] = {
        "barres_ltf": len(ltf), "barres_atteintes": int(atteinte.sum()),
        "derniere_atteinte": None if not len(dates) else str(dates.max().date()),
        "trades_total": len(trades), "trades_atteints": len(touches),
        "par_split": par_split,
    }
print(json.dumps(sortie, indent=1))
