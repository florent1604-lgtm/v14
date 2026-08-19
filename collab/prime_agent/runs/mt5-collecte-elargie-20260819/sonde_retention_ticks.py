
import sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))
from titanium.data.mt5_vendor import mt5_session, ensure_symbol, decalage_serveur_cache

ensure_symbol("EURUSD")
dec = decalage_serveur_cache(("EURUSD",))
with mt5_session() as mt5:
    for jours in (2, 7, 30, 90, 365):
        fin = datetime.now(timezone.utc) - timedelta(days=jours)
        a = time.perf_counter()
        r = mt5.copy_ticks_range("EURUSD",
                                 fin + timedelta(seconds=dec),
                                 fin + timedelta(seconds=dec + 600),
                                 mt5.COPY_TICKS_INFO)
        d = (time.perf_counter() - a) * 1000
        n = 0 if r is None else len(r)
        print(f"J-{jours:<4} fenetre 10 min : {n:>6} ticks  {d:>7.0f} ms  err={mt5.last_error() if not n else ''}")
