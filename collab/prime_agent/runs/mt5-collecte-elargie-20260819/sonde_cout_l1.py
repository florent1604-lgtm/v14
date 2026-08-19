
"""Sonde LECTURE SEULE : cout d'une collecte L1 elargie. Aucun ordre."""
import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))
from titanium.data.mt5_vendor import mt5_session, decalage_serveur_cache

with mt5_session() as mt5:
    tous = mt5.symbols_get()
    total = len(tous)
    visibles = [s.name for s in tous if getattr(s, "visible", False)]
print("catalogue total :", total)
print("visibles        :", len(visibles))
print(visibles[:80])

dec = decalage_serveur_cache(tuple(visibles[:8]))
print("decalage serveur s :", dec)

ech = visibles[:40]
t0 = time.perf_counter()
res = []
maintenant = datetime.now(timezone.utc).timestamp()
for s in ech:
    a = time.perf_counter()
    with mt5_session() as mt5:
        brut = mt5.copy_ticks_range(
            s,
            datetime.fromtimestamp(maintenant - 60 + dec, tz=timezone.utc),
            datetime.fromtimestamp(maintenant + dec, tz=timezone.utc),
            mt5.COPY_TICKS_INFO,
        )
    d = time.perf_counter() - a
    n = 0 if brut is None else len(brut)
    res.append((s, n, round(d * 1000, 1)))
tot = time.perf_counter() - t0
print(f"\n{len(ech)} symboles en {tot:.2f}s  -> {tot/len(ech)*1000:.0f} ms/symbole")
for r in sorted(res, key=lambda x: -x[2])[:15]:
    print(f"  {r[0]:<12} {r[1]:>6} ticks/60s  {r[2]:>8} ms")
ticks = sum(r[1] for r in res)
print("ticks/60s sur l'echantillon :", ticks)
Path(__file__).with_name("sonde_resultat.json").write_text(json.dumps({
    "catalogue_total": total, "visibles": len(visibles),
    "echantillon": res, "duree_s": tot, "decalage_s": dec,
    "at": datetime.now(timezone.utc).isoformat(),
}, indent=2), encoding="utf-8")
