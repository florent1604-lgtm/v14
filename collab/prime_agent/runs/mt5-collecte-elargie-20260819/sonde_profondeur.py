
import sys, time
from datetime import datetime, timezone
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))
from titanium.data.mt5_vendor import mt5_session, ensure_symbol

ensure_symbol("EURUSD")
with mt5_session() as mt5:
    for an in (2010, 2015, 2018, 2020, 2022):
        a = time.perf_counter()
        r = mt5.copy_rates_range("EURUSD", mt5.TIMEFRAME_M15,
                                 datetime(an, 1, 1, tzinfo=timezone.utc),
                                 datetime(an, 12, 31, tzinfo=timezone.utc))
        d = (time.perf_counter() - a) * 1000
        n = 0 if r is None else len(r)
        print(f"EURUSD M15 {an} : {n:>6} barres  {d:>7.0f} ms  err={mt5.last_error() if not n else ''}")
    # total M15 disponible, par paging
    a = time.perf_counter()
    r = mt5.copy_rates_range("EURUSD", mt5.TIMEFRAME_M15,
                             datetime(2000, 1, 1, tzinfo=timezone.utc),
                             datetime(2026, 8, 19, tzinfo=timezone.utc))
    d = (time.perf_counter() - a) * 1000
    if r is not None and len(r):
        t0 = datetime.fromtimestamp(int(r[0]["time"]), tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"\nEURUSD M15 total : {len(r)} barres depuis {t0}  {d:.0f} ms  {r.nbytes/1e6:.1f} Mo")
    else:
        print("\ntotal : vide", mt5.last_error())
