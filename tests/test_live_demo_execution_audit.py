from types import SimpleNamespace as NS

from tools.live_demo import _execution_detail


def test_execution_detail_uses_broker_fill_instead_of_pretrade_budget():
    result = NS(filled_volume=0.3, lot=0.3, risk_money_effective=18.6)
    budget = NS(lot=0.4, risk_money=24.8, effective_pct=1.26, at_min_lot=False)

    detail, actual_risk = _execution_detail(result, budget, equity=1969.72, currency="EUR")

    assert detail == "lot 0.3 · risque 18.6 EUR (0.94 %)"
    assert actual_risk == 18.6
