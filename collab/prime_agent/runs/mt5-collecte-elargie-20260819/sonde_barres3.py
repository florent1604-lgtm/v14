
import sys, time
from datetime import datetime, timezone
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))
from titanium.data.mt5_vendor import mt5_session, ensure_symbol

for s in ["EURUSD", "XAUUSD", "BTCUSD", "US500"]:
    ensure_symbol(s)
    with mt5_session() as mt5:
        for nom, tf in [("M1", mt5.TIMEFRAME_M1), ("M15", mt5.TIMEFRAME_M15),
                        ("H4", mt5.TIMEFRAME_H4), ("D1", mt5.TIMEFRAME_D1)]:
            best = None
            for n in (1000, 10000, 50000, 65000, 99000):
                a = time.perf_counter()
                r = mt5.copy_rates_from_pos(s, tf, 0, n)
                d = (time.perf_counter() - a) * 1000
                if r is None or len(r) == 0:
                    break
                best = (n, len(r), r, d)
                if len(r) < n:
                    break
            if best is None:
                print(f"{s:<8} {nom:<4} rien  last_error={mt5.last_error()}")
                continue
            n, got, r, d = best
            t0 = datetime.fromtimestamp(int(r[0]["time"]), tz=timezone.utc).strftime("%Y-%m-%d")
            t1 = datetime.fromtimestamp(int(r[-1]["time"]), tz=timezone.utc).strftime("%Y-%m-%d")
            print(f"{s:<8} {nom:<4} demande {n:>6} -> {got:>6} barres  {t0} -> {t1}  {d:>6.0f} ms  {r.nbytes/1e6:.2f} Mo")
