from datetime import timezone
from types import SimpleNamespace

from titanium import correlation
from titanium.execution.demo_cohort import DEMO_COHORT_START_UTC, DEMO_COHORT_SYMBOLS
from titanium.execution.live_loss_guard import LiveLossVerdict
from tools import live_demo as live


def test_demo_cohort_contract_names_broker_symbols_and_utc_start():
    assert DEMO_COHORT_SYMBOLS == (
        "USOIL",
        "XAGUSD",
        "COFFEE.fs",
        "BTCUSD",
        "SOL-USD",
        "XAUUSD",
        "HK50",
        "FRA40",
    )
    assert DEMO_COHORT_START_UTC.tzinfo is timezone.utc


def test_full_catalogue_keeps_all_symbols_as_new_entry_candidates():
    assert live._entry_universe(
        ["AUDUSD", "BTCUSD"],
        ["USOIL", "SOL-USD"],
    ) == ["AUDUSD", "BTCUSD", "USOIL", "SOL-USD"]


def test_full_catalogue_accepts_every_scanned_symbol_for_new_entries():
    assert live.UNIVERS == []
    assert live._entry_universe(
        ["AUDUSD", "BTCUSD"],
        ["USOIL", "SOL-USD"],
    ) == ["AUDUSD", "BTCUSD", "USOIL", "SOL-USD"]


def test_full_catalogue_does_not_seed_correlation_from_a_legacy_cohort(monkeypatch):
    cached = {f"S{i}": "g1" for i in range(20)}
    monkeypatch.setattr(live, "_JOUABLES", set())
    monkeypatch.setattr(
        correlation,
        "charger_cache",
        lambda: correlation.Grappes(par_actif=cached, membres={"g1": list(cached)}),
    )

    symbols = live._tradables_connus(DEMO_COHORT_SYMBOLS)

    assert set(DEMO_COHORT_SYMBOLS) <= set(symbols)
    assert not (set(cached) & set(symbols))


def test_worker_loss_gate_uses_common_demo_cohort_start(monkeypatch):
    from titanium.execution import live_loss_guard
    from titanium.execution.mt5_executor import ExecutionPolicy
    from tools import analystes

    captured = {}

    def evaluate(_path, *, account, not_before=None, **_kwargs):
        captured.update(account=account, not_before=not_before)
        return LiveLossVerdict("ALLOW", "WITHIN_LOSS_LIMITS")

    monkeypatch.setattr(live_loss_guard, "evaluate_live_loss_guard", evaluate)
    monkeypatch.setattr(
        ExecutionPolicy,
        "from_config",
        classmethod(lambda _cls: SimpleNamespace(expected_demo_login=10055401)),
    )

    assert analystes._entry_loss_gate().action == "ALLOW"
    assert captured == {
        "account": "10055401",
        "not_before": DEMO_COHORT_START_UTC,
    }
