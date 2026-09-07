"""A fast venue must not make older data or a slow response appear fresh."""

import json
from dataclasses import replace

import pytest

from titanium.microstructure import (
    VenueSnapshot,
    aggregate_snapshots,
    attach_live_microstructure,
    entry_microstructure_guard,
)
from tools import collecteur_microstructure as collector


def venue(name="binance", at=100_000.):
    return VenueSnapshot(name, "BTCUSDT", ((100., 2.),), ((101., 1.),), (), at, at)


def test_one_fresh_venue_cannot_refresh_stale_second_venue():
    snapshot = aggregate_snapshots([venue(), venue("bybit", 90_000.)], now_ms=100_000.)
    assert snapshot["venue_count"] == 1
    assert snapshot["venues"] == ["binance"]


def test_duplicate_source_cannot_supply_quorum():
    assert aggregate_snapshots([venue(), venue()], now_ms=100_000.)["venue_count"] == 1


def test_mixed_symbols_rejected():
    with pytest.raises(ValueError, match="mixed symbols"):
        aggregate_snapshots([venue(), replace(venue("bybit"), symbol="ETHUSDT")],
                            now_ms=100_000.)


@pytest.mark.parametrize("field", ["received_ms", "event_ms"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), 110_000., 80_000.])
def test_invalid_or_future_or_stale_clock_cannot_supply_quorum(field, value):
    snapshot = aggregate_snapshots([venue(), replace(venue("bybit"), **{field: value})],
                                   now_ms=100_000.)
    assert snapshot["venue_count"] == 1


def test_snapshot_expires_when_oldest_contributing_source_expires(tmp_path):
    snapshot = aggregate_snapshots([venue(), venue("bybit", 93_000.)], now_ms=100_000.)
    assert snapshot["received_ms"] == 93_000.
    target = tmp_path / "results" / "microstructure" / "BTCUSDT.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(snapshot))
    assert attach_live_microstructure("BTCUSD", {}, root=tmp_path, now_ms=101_001.) is None


def test_old_book_not_refreshed_by_new_trades():
    result = collector.parse_bybit("BTCUSDT", {
        "retCode": 0, "result": {"ts": 1., "b": [[100, 1]], "a": [[101, 1]]},
    }, {"retCode": 0, "result": {"list": [
        {"price": 100., "size": 1., "side": "Buy", "time": 100_000.},
    ]}}, received_ms=100_000.)
    assert result.event_ms == 1.


def test_worker_records_own_completion_time(monkeypatch):
    monkeypatch.setattr(collector, "_get_json", lambda url: {"done": True})
    monkeypatch.setattr(collector.time, "time", lambda: 123.)
    assert collector._get_json_timed("https://unused.invalid") == ({"done": True}, 123_000.)


def test_single_writer_lock_is_released(tmp_path):
    with collector.collector_lock(tmp_path), pytest.raises(OSError), collector.collector_lock(tmp_path):
        pytest.fail("second writer entered")
    with collector.collector_lock(tmp_path):
        pass


def test_health_requires_actual_fresh_assets_not_just_running_process(tmp_path):
    assert collector.collection_health(["BTCUSDT"], tmp_path, now_ms=100_000.)["status"] == "WAIT"
    snapshot = aggregate_snapshots([venue(), venue("bybit")], now_ms=100_000.)
    collector.write_atomic(snapshot, tmp_path)
    assert collector.collection_health(["BTCUSDT"], tmp_path, now_ms=100_000.)["status"] == "OK"
    assert collector.collection_health(["BTCUSDT"], tmp_path, now_ms=109_000.)["status"] == "WAIT"


def test_final_guard_refuses_missing_or_expired_crypto_data(tmp_path):
    assert entry_microstructure_guard("BTCUSD", side=1, root=tmp_path).action == "UNKNOWN"
    assert entry_microstructure_guard("EURUSD", side=1, root=tmp_path).action == "ALLOW"
    snapshot = aggregate_snapshots([venue(), venue("bybit")], now_ms=100_000.)
    target = tmp_path / "results" / "microstructure"
    collector.write_atomic(snapshot, target)
    assert entry_microstructure_guard("BTCUSD", side=1, root=tmp_path,
                                      now_ms=100_000.).action == "ALLOW"
    assert entry_microstructure_guard("BTCUSD", side=1, root=tmp_path,
                                      now_ms=109_000.).action == "UNKNOWN"


def test_live_final_freshness_check_precedes_recorded_send():
    import inspect

    from tools import live_demo

    source = inspect.getsource(live_demo.tour)
    assert source.index("_demander_avis(") < source.index("final_micro =")
    assert source.index("final_micro =") < source.index("res = execute_recorded(")
