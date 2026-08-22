
import json
import pandas as pd
from titanium.data.archive_barres import charger_barres, inventaire, resume as res_meta
from pathlib import Path

syms = sorted(inventaire("M15"))
print("univers M15:", len(syms))
i = syms.index("USDUSC")
print("USDUSC index", i, "-> lot", i % 8, "position dans le lot", i // 8)
df = pd.read_parquet("results/barres/M15/USDUSC.parquet")
print("lignes brutes:", len(df), "reconstruites:", int(df["reconstruit"].sum()) if "reconstruit" in df else None)
print("meta:", json.dumps(res_meta("USDUSC", "M15"))[:400])
existe = Path("results/rejeu_univers/USDUSC.json").is_file()
print("resume existant:", existe)
for s in ("DJ30.fs", "EURNZD"):
    ltf = charger_barres(s, "M15")
    htf = charger_barres(s, "H4", depuis_utc=ltf.index[0] - pd.Timedelta(days=1826))
    print(s, "M15 debut", ltf.index[0], "| H4 borne OK, barres", len(htf), "debut", htf.index[0])
