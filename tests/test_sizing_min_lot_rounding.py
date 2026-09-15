from __future__ import annotations

import pytest

from titanium.data.mt5_vendor import SymbolSpec
from titanium.execution.mt5_executor import compute_lot
from titanium.sizing import budget_for, loss_per_lot


@pytest.mark.unit
def test_minimum_lot_budget_survives_executor_recalculation() -> None:
    instrument = SymbolSpec(
        name="XAGUSD",
        digits=5,
        point=1e-5,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=12,
        tick_value=1.0,
        tick_size=1e-5,
    )
    stop_distance = 0.001004
    exact_minimum_risk = instrument.volume_min * loss_per_lot(
        instrument,
        stop_distance,
    )

    budget = budget_for(instrument, stop_distance, equity=100.0)
    lot, overrisk = compute_lot(instrument, stop_distance, budget.risk_money)

    assert exact_minimum_risk == pytest.approx(1.004)
    assert budget.tradable
    assert budget.at_min_lot
    assert budget.lot == instrument.volume_min
    assert budget.risk_money >= exact_minimum_risk
    assert budget.effective_pct == pytest.approx(exact_minimum_risk)
    assert lot == instrument.volume_min
    assert overrisk == 1.0
