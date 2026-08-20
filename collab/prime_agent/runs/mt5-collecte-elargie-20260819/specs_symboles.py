"""Fige les specifications des symboles pour rendre le rejeu hors ligne.

Sans ce fichier, tout backtest redemande le terminal pour connaitre un point et
un spread, et n'est donc pas reproductible sans MT5 ouvert. Lecture seule.
"""
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))

from dataclasses import asdict

from titanium.data.mt5_vendor import ensure_symbol, mt5_session

DEST = RACINE / "results" / "barres" / "_specifications.json"

with mt5_session() as mt5:
    noms = [s.name for s in mt5.symbols_get() if getattr(s, "visible", False)]

specs = {}
for n in noms:
    try:
        specs[n] = asdict(ensure_symbol(n))
    except Exception as e:
        print(f"{n}: {type(e).__name__}")
DEST.write_text(json.dumps(specs, indent=1), encoding="utf-8")
print(f"{len(specs)} specifications -> {DEST}")
