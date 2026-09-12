from __future__ import annotations

import inspect
import json

from titanium.microstructure import (
    VenueSnapshot,
    aggregate_snapshots,
    attach_live_microstructure,
    microstructure_gate,
)


def _venue(name: str, *, bid_qty: float, ask_qty: float,
           buy_qty: float, sell_qty: float, event_ms: float = 1_000_000.0):
    return VenueSnapshot(
        venue=name,
        symbol="BTCUSDT",
        bids=((100.0, bid_qty), (99.9, bid_qty / 2)),
        asks=((100.1, ask_qty), (100.2, ask_qty / 2)),
        trades=(
            (event_ms - 1_000.0, "buy", buy_qty, 100.1),
            (event_ms - 500.0, "sell", sell_qty, 100.0),
        ),
        event_ms=event_ms,
        received_ms=event_ms + 20.0,
    )


def test_aggregation_exposes_depth_flow_walls_and_source_count():
    snapshot = aggregate_snapshots([
        _venue("binance", bid_qty=8, ask_qty=2, buy_qty=6, sell_qty=2),
        _venue("bybit", bid_qty=4, ask_qty=2, buy_qty=3, sell_qty=1),
        _venue("okx", bid_qty=3, ask_qty=1, buy_qty=2, sell_qty=1),
    ], now_ms=1_000_050.0)

    assert snapshot["schema"] == "v14.microstructure.v1"
    assert snapshot["venue_count"] == 3
    assert snapshot["depth_imbalance_10bps"] > 0.4
    assert snapshot["taker_imbalance_recent"] > 0.3
    assert "taker_imbalance_60s" not in snapshot
    assert snapshot["bid_wall_distance_bps"] >= 0.0
    assert snapshot["ask_wall_distance_bps"] >= 0.0
    assert snapshot["fresh"] is True


def test_gate_blocks_only_confirmed_adverse_pressure():
    adverse = aggregate_snapshots([
        _venue("binance", bid_qty=1, ask_qty=8, buy_qty=1, sell_qty=8),
        _venue("bybit", bid_qty=1, ask_qty=7, buy_qty=1, sell_qty=7),
    ], now_ms=1_000_050.0)
    supportive = aggregate_snapshots([
        _venue("binance", bid_qty=8, ask_qty=1, buy_qty=8, sell_qty=1),
        _venue("bybit", bid_qty=7, ask_qty=1, buy_qty=7, sell_qty=1),
    ], now_ms=1_000_050.0)

    assert microstructure_gate(adverse, side=1).action == "BLOCK"
    assert microstructure_gate(adverse, side=-1).action == "ALLOW"
    assert microstructure_gate(supportive, side=1).action == "ALLOW"


def test_gate_is_unknown_for_stale_or_single_venue_data():
    one = aggregate_snapshots([
        _venue("binance", bid_qty=1, ask_qty=8, buy_qty=1, sell_qty=8),
    ], now_ms=1_000_050.0)
    stale = dict(one, venue_count=3, fresh=False)

    assert microstructure_gate(one, side=1).action == "UNKNOWN"
    assert microstructure_gate(stale, side=1).action == "UNKNOWN"


def test_live_snapshot_is_attached_first_in_llm_indicators(tmp_path):
    target = tmp_path / "results" / "microstructure" / "BTCUSDT.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "schema": "v14.microstructure.v1",
        "symbol": "BTCUSDT",
        "event_ms": 1_000_000.0,
        "received_ms": 1_000_020.0,
        "venue_count": 3,
        "fresh": True,
        "spread_bps": 1.2,
        "depth_imbalance_10bps": 0.4,
        "taker_imbalance_recent": 0.25,
        "bid_wall_distance_bps": 3.0,
        "ask_wall_distance_bps": 6.0,
    }), encoding="utf-8")
    feats = {"_trace": {"indicators": {"rsi": 55.0}}}

    attached = attach_live_microstructure(
        "BTCUSD", feats, root=tmp_path, now_ms=1_000_100.0,
    )

    assert attached is not None
    indicators = feats["_trace"]["indicators"]
    assert list(indicators)[0] == "micro_depth_imbalance_10bps"
    assert indicators["micro_taker_imbalance_recent"] == 0.25
    assert indicators["micro_venues"] == 3.0
    assert indicators["rsi"] == 55.0


def test_attachment_rejects_stale_or_malformed_snapshot(tmp_path):
    target = tmp_path / "results" / "microstructure" / "BTCUSDT.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    feats = {"_trace": {"indicators": {}}}

    assert attach_live_microstructure("BTCUSD", feats, root=tmp_path,
                                      now_ms=1_000_000.0) is None
    assert feats["_trace"]["indicators"] == {}


def test_live_loop_seals_microstructure_before_requesting_qwen():
    """La microstructure est scellée AVANT que le cortex soit interrogé.

    Depuis la bascule mécanique du 11/09/2026, `_demander_avis` est appelé
    par `_autorisation_et_conviction` : c'est ce point-là qui matérialise la
    demande cognitive dans le tour. On vérifie aussi qu'il la porte vraiment.
    """
    from tools import live_demo

    assert "_demander_avis(" in inspect.getsource(
        live_demo._autorisation_et_conviction)

    source = inspect.getsource(live_demo.tour)
    assert source.index("attach_live_microstructure") < source.index(
        "_autorisation_et_conviction(")
