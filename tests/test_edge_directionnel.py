from __future__ import annotations

import pandas as pd

from tools.edge_directionnel import (
    BarrierOutcome,
    barrier_outcome,
    matched_random_controls,
    paired_comparison,
    stable_seed,
    summarize,
    volatility_buckets,
)


def _frame(rows):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, index=idx)


def test_barriere_positive_touchee_avant_negative():
    frame = _frame([
        {"open": 100, "high": 100, "low": 100, "close": 100},
        {"open": 100, "high": 101.1, "low": 99.8, "close": 100.8},
    ])
    out = barrier_outcome(frame, 0, side=1, entry=100, r_unit=1, horizon=10)
    assert out.outcome == 1
    assert out.mfe_r == 1.1
    assert out.mae_r == -0.2


def test_double_touche_compte_comme_perte_conservatrice():
    frame = _frame([
        {"open": 100, "high": 100, "low": 100, "close": 100},
        {"open": 100, "high": 101.2, "low": 98.8, "close": 100},
    ])
    assert barrier_outcome(frame, 0, side=1, entry=100, r_unit=1).outcome == -1


def test_barriere_short_est_symetrique():
    frame = _frame([
        {"open": 100, "high": 100, "low": 100, "close": 100},
        {"open": 100, "high": 100.2, "low": 98.8, "close": 99},
    ])
    assert barrier_outcome(frame, 0, side=-1, entry=100, r_unit=1).outcome == 1


def test_resume_separe_resolus_et_censures():
    frame = _frame([
        {"open": 100, "high": 100, "low": 100, "close": 100},
        {"open": 100, "high": 101.1, "low": 99.8, "close": 100.8},
        {"open": 100, "high": 100.3, "low": 99.7, "close": 100},
    ])
    outs = [
        barrier_outcome(frame.iloc[:2], 0, side=1, entry=100, r_unit=1),
        barrier_outcome(frame.iloc[[0, 2]], 0, side=1, entry=100, r_unit=1),
    ]
    s = summarize(outs)
    assert s["n"] == 2
    assert s["resolved"] == 1
    assert s["p_plus_before_minus"] == 1.0
    assert s["censored_rate"] == 0.5


def test_controles_apparies_meme_heure_et_bucket_volatilite():
    n = 80
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    frame = pd.DataFrame({
        "open": [100.0] * n,
        "high": [100.2] * n,
        "low": [99.8] * n,
        "close": [100.0] * n,
    }, index=idx)
    vol_bucket = pd.Series([i % 4 for i in range(n)], index=idx)
    controls = matched_random_controls(
        frame,
        signal_index=idx[40],
        signal_hour=idx[40].hour,
        signal_vol_bucket=int(vol_bucket.iloc[40]),
        vol_bucket=vol_bucket,
        r_unit=1.0,
        draws=8,
        seed=7,
        horizon=4,
    )
    assert len(controls) == 8
    assert all(c.entry_hour == idx[40].hour for c in controls)
    assert all(c.vol_bucket == int(vol_bucket.iloc[40]) for c in controls)
    assert {c.side for c in controls} == {-1, 1}


def test_buckets_volatilite_acceptent_la_fenetre_initiale_nan():
    frame = _frame([
        {"open": 100, "high": 101, "low": 99, "close": 100}
        for _ in range(30)
    ])
    buckets = volatility_buckets(frame)
    assert buckets.iloc[:19].eq(-1).all()
    assert buckets.iloc[20:].between(0, 3).all()


def test_comparaison_appariee_bootstrappe_les_signaux_pas_les_controles():
    def out(value):
        return BarrierOutcome(value, 1.0 if value > 0 else 0.0,
                              -1.0 if value < 0 else 0.0, 1)
    rows = [
        {"signal": out(1), "controls": [out(-1), out(-1)]},
        {"signal": out(-1), "controls": [out(1), out(1)]},
    ]
    comparison = paired_comparison(rows, seed=1, samples=200)
    assert comparison["pairs"] == 2
    assert comparison["delta"] == 0.0


def test_seed_est_stable_et_sensible_aux_entrees():
    assert stable_seed("EURUSD", 3) == stable_seed("EURUSD", 3)
    assert stable_seed("EURUSD", 3) != stable_seed("EURUSD", 4)
