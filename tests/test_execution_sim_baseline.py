from __future__ import annotations

import pandas as pd
import pytest

from titanium.backtest import _simuler_sortie, cout_spread_r


def test_legacy_backtest_baseline_remains_unchanged():
    bars = pd.DataFrame(
        {
            "open": [100.0, 100.0, 101.0, 102.0],
            "high": [100.2, 101.2, 102.2, 104.2],
            "low": [99.8, 99.9, 100.8, 101.8],
            "close": [100.0, 101.0, 102.0, 104.0],
        }
    )
    result = _simuler_sortie(
        bars,
        0,
        1,
        100.0,
        98.0,
        104.0,
        breakeven_r=0.8,
        trail_start_r=9.0,
        trail_dist_r=0.8,
        max_barres=10,
    )
    assert result == pytest.approx((3, 104.0, "tp", -0.05, 2.1))
    assert cout_spread_r(0.2, 2.0) == pytest.approx(0.1)
