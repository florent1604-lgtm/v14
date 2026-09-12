"""Catalogue growth invalidates a fresh cache, missing data does not loop."""

import json
import time

from titanium import correlation
from tools import live_demo as live


def test_disk_cache_batches_new_symbols_until_refresh_window(tmp_path, monkeypatch):
    path = tmp_path / "grappes.json"
    monkeypatch.setattr(correlation, "CACHE", path)
    clock = [1_000_000.0]
    monkeypatch.setattr(correlation.time, "time", lambda: clock[0])
    path.write_text(json.dumps({"par_actif": {"X": "g1"}, "membres": {"g1": ["X"]},
                                "calcule_le": clock[0] - 60}), encoding="utf-8")
    calls = []

    def calculate(symbols):
        calls.append(list(symbols))
        # BTCUSD has no usable history yet: never invent its group.
        return correlation.Grappes(par_actif={"X": "g1"}, membres={"g1": ["X"]},
                                   calcule_le=clock[0])

    monkeypatch.setattr(correlation, "calculer", calculate)
    first = correlation.charger(["X", "BTCUSD"])
    clock[0] += correlation.CATALOGUE_REFRESH_MIN_S + 1
    second = correlation.charger(["BTCUSD", "X"])
    assert calls == [["BTCUSD", "X"]]
    assert "BTCUSD" not in first.par_actif and "BTCUSD" not in second.par_actif


def test_memory_cache_batches_continuous_catalogue_growth(monkeypatch):
    old_symbols = [f"S{i}" for i in range(20)]
    old = correlation.Grappes(par_actif=dict.fromkeys(old_symbols, "g1"),
                              calcule_le=time.time())
    monkeypatch.setattr(live, "_GRAPPES", old)
    clock = [1_000_000.0]
    monkeypatch.setattr(live.time, "time", lambda: clock[0])
    monkeypatch.setattr(live, "_GRAPPES_A", clock[0])
    monkeypatch.setattr(live, "_GRAPPES_CATALOGUE", set(), raising=False)
    calls = []

    def load(symbols):
        calls.append(list(symbols))
        return old

    monkeypatch.setattr(correlation, "charger", load)
    live.rafraichir_grappes(old_symbols)
    assert not calls
    live.rafraichir_grappes([*old_symbols, "BTCUSD"])
    live.rafraichir_grappes([*old_symbols, "BTCUSD", "ETHUSD"])
    assert not calls
    clock[0] += correlation.CATALOGUE_REFRESH_MIN_S + 1
    live.rafraichir_grappes([*old_symbols, "BTCUSD", "ETHUSD"])
    assert len(calls) == 1
    assert "BTCUSD" not in live._GRAPPES.par_actif
