"""Pilote : combien de trades l'archive rend-elle par actif ?

Question de Florent : "as-tu assez de donnees pour executer les meilleurs points
d'entree ?". La reponse ne s'opine pas, elle se compte. On rejoue la MEME
confluence_gate qu'en live sur les barres archivees et on compte les clotures
par actif. Aucun parametre de strategie n'est touche, rien n'est optimise.
"""
import json
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))

from titanium.backtest import decouper_walk_forward, rejouer
from titanium.data.archive_barres import charger_barres, resume

SYMBOLES = sys.argv[1].split(",") if len(sys.argv) > 1 else ["EURUSD"]
BARRES = int(sys.argv[2]) if len(sys.argv) > 2 else 20000

from titanium.data.mt5_vendor import ensure_symbol  # noqa: E402

sortie = []
for sym in SYMBOLES:
    t0 = time.time()
    try:
        ltf = charger_barres(sym, "M15", BARRES)
        htf = charger_barres(sym, "H4")
    except Exception as e:
        print(f"{sym}: {type(e).__name__} {e}", flush=True)
        continue
    try:
        spec = ensure_symbol(sym)
        spread = spec.spread * spec.point
    except Exception:
        spread = 0.0
    r = rejouer(sym, ltf, htf, spread=spread, pas=1)
    d = r.resume()
    jours = (ltf.index[-1] - ltf.index[0]).days or 1
    d["jours"] = jours
    d["trades_par_an"] = round(d["trades"] * 365.0 / jours, 1)
    d["barres_ltf"] = len(ltf)
    d["n_enter"] = r.n_enter
    d["spread_prix"] = spread
    d["secondes"] = round(time.time() - t0, 1)
    d["debut"] = str(ltf.index[0])
    segs = decouper_walk_forward(r, 3)
    d["segments"] = [{"n": s.n, "esp": round(s.esperance_r, 4)} for s in segs]
    sortie.append(d)
    pf = d["profit_factor"]
    print(f"{sym:10} {d['trades']:5} trades ({d['trades_par_an']:6.1f}/an) "
          f"esp {d['esperance_r']:+.4f} R  win {d['winrate']*100:4.1f}%  "
          f"PF {pf if pf is not None else 'inf'}  [{d['secondes']}s, "
          f"{d['barres_ltf']} barres depuis {d['debut'][:10]}]", flush=True)

etiquette = sys.argv[3] if len(sys.argv) > 3 else "_".join(SYMBOLES)[:40]
dest = Path(__file__).with_name(f"pilote_{etiquette}.json")
anciens = json.loads(dest.read_text()) if dest.is_file() else []
dest.write_text(json.dumps(anciens + sortie, indent=1))
print("ecrit:", dest, flush=True)
