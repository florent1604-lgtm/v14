"""Bound concurrency and publication delay without loosening freshness gates."""

import threading

import pytest

from tools import collecteur_microstructure as collector


def snapshot(symbol):
    return {"symbol": symbol, "venue_count": 2, "depth_imbalance_10bps": 0.,
            "taker_imbalance_recent": 0., "received_ms": 123., "event_ms": 100.}


def test_fast_asset_is_written_before_slow_asset_finishes(monkeypatch, tmp_path):
    published = threading.Event()
    rows = []

    def collect(symbol):
        if symbol == "SLOW":
            assert published.wait(3), "fast result waited for slow result"
        return snapshot(symbol), {}

    def write(row, output):
        rows.append(row)
        if row["symbol"] == "FAST":
            published.set()

    monkeypatch.setattr(collector, "collect_symbol", collect)
    monkeypatch.setattr(collector, "write_atomic", write)
    assert collector.cycle(["SLOW", "FAST", "FAST"], output=tmp_path) == 2
    assert [row["symbol"] for row in rows] == ["FAST", "SLOW"]
    assert all(row["received_ms"] == 123. and row["event_ms"] == 100. for row in rows)


def test_large_universe_has_two_asset_workers_at_most(monkeypatch, tmp_path):
    barrier = threading.Barrier(2, timeout=3)
    lock = threading.Lock()
    active = peak = 0

    def collect(symbol):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait()
        with lock:
            active -= 1
        return snapshot(symbol), {}

    monkeypatch.setattr(collector, "collect_symbol", collect)
    monkeypatch.setattr(collector, "write_atomic", lambda *args: None)
    assert collector.cycle([f"ASSET{i}" for i in range(150)], output=tmp_path) == 150
    assert peak == 2


def test_one_failure_does_not_erase_another_asset(monkeypatch, tmp_path):
    def collect(symbol):
        if symbol == "BAD":
            raise TimeoutError
        return snapshot(symbol), {}

    monkeypatch.setattr(collector, "collect_symbol", collect)
    assert collector.cycle(["BAD", "OK"], output=tmp_path) == 1
    assert (tmp_path / "OK.json").exists()
    assert not (tmp_path / "BAD.json").exists()


@pytest.mark.parametrize("duration,expected", [(2., 5.), (8., 8.)])
def test_period_is_measured_from_cycle_start(monkeypatch, tmp_path, duration, expected):
    clock = [0.]
    starts = []

    def cycle(*args, **kwargs):
        starts.append(clock[0])
        clock[0] += duration
        if len(starts) == 2:
            collector._stop = True

    monkeypatch.setattr(collector, "_stop", False)
    monkeypatch.setattr(collector, "cycle", cycle)
    monkeypatch.setattr(collector, "collection_health", lambda *a: {"status": "WAIT"})
    monkeypatch.setattr(collector, "write_atomic", lambda *a: None)
    monkeypatch.setattr(collector.signal, "signal", lambda *a: None)
    monkeypatch.setattr(collector.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(collector.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    assert collector.main(["--dossier", str(tmp_path), "--intervalle", "5"]) == 0
    assert starts == [0., expected]


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "0.1"])
def test_invalid_period_refused_before_start(monkeypatch, value):
    monkeypatch.setattr(collector, "cycle", lambda *a, **k: pytest.fail("collector started"))
    with pytest.raises(SystemExit) as error:
        collector.main(["--intervalle", value])
    assert error.value.code == 2
