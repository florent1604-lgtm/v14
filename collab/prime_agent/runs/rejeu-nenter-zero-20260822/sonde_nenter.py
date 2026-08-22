
"""Sonde: prouve que n_enter=0 vient de l'horloge murale (samedi), pas des donnees.

Ne publie aucun artefact. Rejoue une tranche bornee de barres, une fois avec le
code actuel (horloge de barre) et une fois en forcant l'ancien comportement
(horloge murale) par patch local de build_feats/evaluate.
"""
import sys
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))

import titanium.backtest as bt
from tools.rejeu_univers import charger_barres, spread_median_prix, specifications

SYMBOLE = sys.argv[1] if len(sys.argv) > 1 else "AAVE-USD"
BARRES = int(sys.argv[2]) if len(sys.argv) > 2 else 6000

ltf = charger_barres(SYMBOLE, "M15", BARRES)
htf = charger_barres(SYMBOLE, "H4")
spread = spread_median_prix(ltf, specifications().get(SYMBOLE, {}))

res = bt.rejouer(SYMBOLE, ltf, htf, spread=spread, pas=1)
print(f"[corrige ] barres={res.barres_evaluees} n_enter={res.n_enter} "
      f"trades={len(res.trades)} erreurs={res.erreurs}")

# Reproduction de l'ancien comportement: aucune horloge de barre transmise.
_duree = bt._duree_barre
bt._duree_barre = lambda index: __import__("pandas").Timedelta(0)
import titanium.features.builder as builder
_bf = builder.build_feats
def build_feats_mur(df_ltf, df_htf, **kw):
    kw.pop("now", None)
    kw.pop("marche_continu", None)
    return _bf(df_ltf, df_htf, **kw)
builder.build_feats = build_feats_mur

res2 = bt.rejouer(SYMBOLE, ltf, htf, spread=spread, pas=1)
print(f"[horl.mur] barres={res2.barres_evaluees} n_enter={res2.n_enter} "
      f"trades={len(res2.trades)} erreurs={res2.erreurs}")
