from __future__ import annotations

import hashlib
import json
import time

import numpy as np
import pandas as pd

from titanium.organism.market_jepa import (
    MarketJepaRuntime,
    context_vector,
    fit_ridge,
    training_examples,
)


def _bars(n=400):
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    width = np.maximum(0.02, close * rng.uniform(0.0005, 0.003, n))
    return pd.DataFrame({
        "open": close,
        "high": close + width,
        "low": close - width,
        "close": close,
        "tick_volume": rng.integers(10, 1000, n),
    })


def _write_model(tmp_path):
    frame = _bars()
    x, y = training_examples(frame, context=64, horizon=4, stride=4)
    head = fit_ridge(x, y)
    model = {
        "schema_version": 1,
        "context": 64,
        "blocks": 8,
        "global": head,
        "assets": {"BTCUSD": head},
    }
    path = tmp_path / "model.json"
    raw = (json.dumps(model, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    path.with_suffix(".manifest.json").write_text(json.dumps({
        "sha256": hashlib.sha256(raw).hexdigest(),
    }), encoding="utf-8")
    return path, frame


def test_market_jepa_is_nondirectional_and_fast(tmp_path):
    path, frame = _write_model(tmp_path)
    runtime = MarketJepaRuntime(path)
    runtime.read("BTCUSD", frame)
    started = time.perf_counter()
    reading = runtime.read("BTCUSD", frame)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert reading["jepa_available"] == 1.0
    assert set(reading) == {
        "jepa_available", "jepa_vol_ratio", "jepa_impulse_r", "jepa_latency_ms",
    }
    assert elapsed_ms < 50.0


def test_market_jepa_rejects_tampered_artifact(tmp_path):
    path, frame = _write_model(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert MarketJepaRuntime(path).read("BTCUSD", frame)["jepa_available"] == 0.0


def test_context_and_targets_are_finite():
    frame = _bars()
    vector = context_vector(frame, context=64, blocks=8)
    x, y = training_examples(frame, context=64, horizon=4, stride=8)
    assert vector.shape == (40,)
    assert x.shape[1] == 40
    assert y.shape[1] == 2
    assert np.isfinite(x).all() and np.isfinite(y).all()
