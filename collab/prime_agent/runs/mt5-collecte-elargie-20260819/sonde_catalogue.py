
"""Sonde LECTURE SEULE : passe complete sur tout le catalogue. Aucun ordre, aucune ecriture d'archive."""
import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))
from titanium.data.mt5_vendor import mt5_session, decalage_serveur_cache

with mt5_session() as mt5:
    visibles = [s.name for s in mt5.symbols_get() if getattr(s, "visible", False)]
dec = decalage_serveur_cache(tuple(visibles[:8]))
maintenant = datetime.now(timezone.utc).timestamp()
res = []
t0 = time.perf_counter()
for s in visibles:
    a = time.perf_counter()
    with mt5_session() as mt5:
        brut = mt5.copy_ticks_range(
            s,
            datetime.fromtimestamp(maintenant - 300 + dec, tz=timezone.utc),
            datetime.fromtimestamp(maintenant + dec, tz=timezone.utc),
            mt5.COPY_TICKS_INFO,
        )
    res.append((s, 0 if brut is None else len(brut), round((time.perf_counter()-a)*1000, 1)))
tot = time.perf_counter() - t0
ticks5 = sum(r[1] for r in res)
print(f"catalogue {len(visibles)} symboles, fenetre 300 s")
print(f"passe complete : {tot:.1f} s  ({tot/len(visibles)*1000:.0f} ms/symbole)")
print(f"ticks sur 300 s : {ticks5}  -> {ticks5/5:.0f} ticks/min  -> {ticks5/5*60*24/1e6:.2f} M ticks/jour")
print(f"volume estime a 195 o/ligne : {ticks5/5*60*24*195/1e9:.2f} Go/jour")
print("\ntop 20 debit :")
for r in sorted(res, key=lambda x: -x[1])[:20]:
    print(f"  {r[0]:<12} {r[1]/5:>8.0f} ticks/min  {r[2]:>7} ms")
muets = [r[0] for r in res if r[1] == 0]
print(f"\nsymboles muets sur 300 s : {len(muets)} -> {muets[:40]}")
Path(__file__).with_name("sonde_catalogue.json").write_text(json.dumps({
    "visibles": len(visibles), "duree_passe_s": tot, "ticks_300s": ticks5,
    "par_symbole": res, "at": datetime.now(timezone.utc).isoformat()}, indent=2), encoding="utf-8")
