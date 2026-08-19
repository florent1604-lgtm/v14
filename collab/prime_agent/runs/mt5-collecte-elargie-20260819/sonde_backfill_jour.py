
import sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))
from titanium.data.mt5_vendor import mt5_session, ensure_symbol, decalage_serveur_cache

for s in ("EURUSD", "XAUUSD", "US500"):
    ensure_symbol(s)
dec = decalage_serveur_cache(("EURUSD",))
jour = datetime.now(timezone.utc) - timedelta(days=30)
debut = jour.replace(hour=0, minute=0, second=0, microsecond=0)
with mt5_session() as mt5:
    for s in ("EURUSD", "XAUUSD", "US500"):
        a = time.perf_counter()
        r = mt5.copy_ticks_range(s, debut + timedelta(seconds=dec),
                                 debut + timedelta(days=1, seconds=dec), mt5.COPY_TICKS_INFO)
        d = time.perf_counter() - a
        n = 0 if r is None else len(r)
        print(f"{s:<8} J-30 journee complete : {n:>8} ticks  {d:>6.1f} s  "
              f"~{n*195/1e6:.0f} Mo ndjson  err={mt5.last_error() if not n else ''}")
