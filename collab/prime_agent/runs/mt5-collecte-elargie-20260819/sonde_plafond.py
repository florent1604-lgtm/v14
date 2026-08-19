
import sys
sys.path.insert(0, r"C:\Users\flore\Desktop\V14")
from datetime import datetime, timezone
from titanium.data.mt5_vendor import mt5_session
import time
with mt5_session() as mt5:
    tf15 = mt5.TIMEFRAME_M15
    for an in (2015, 2019, 2021, 2022):
        a = datetime(an,6,1,tzinfo=timezone.utc); b = datetime(an,6,8,tzinfo=timezone.utc)
        r = mt5.copy_rates_range("EURUSD", tf15, a, b)
        print("M15", an, "->", 0 if r is None else len(r), mt5.last_error())
    # H1 profondeur
    r = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_H1, 0, 99000)
    print("H1 from_pos:", len(r), datetime.fromtimestamp(int(r[0]["time"]), tz=timezone.utc))
    # H4 des symboles vides
    for s in ("AUDCAD","AUDCHF"):
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_H4, 0, 99000)
        print("H4", s, "->", 0 if r is None else len(r), mt5.last_error())
