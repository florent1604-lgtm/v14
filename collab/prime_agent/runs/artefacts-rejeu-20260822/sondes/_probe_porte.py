
import json
from titanium.data.archive_barres import charger_barres, ArchiveHorsUniversError, ArchiveQualiteError
for sym in ("COFFEE.fs", "COCOA.fs", "IT40", "SPA35", "USDCLP", "USDCOP", "DJ30.fs", "EURUSD"):
    for tf in ("M15", "H4"):
        try:
            df = charger_barres(sym, tf)
            q = df.attrs["archive_quality"]
            print(f"{sym:10} {tf:3} barres {len(df):6} debut {df.index[0].date()} "
                  f"| borne_util {q['borne_utile']:5} borne_gran {q['borne_granularite']:5} "
                  f"grossieres_archive {q['barres_grossieres_archive']:5} rendues {q['barres_grossieres_rendues']}")
        except Exception as e:
            print(f"{sym:10} {tf:3} {type(e).__name__}: {e}")
