from datetime import timezone
from types import SimpleNamespace

from titanium import correlation
from titanium.execution.demo_cohort import DEMO_COHORT_START_UTC, DEMO_COHORT_SYMBOLS
from titanium.execution.live_loss_guard import LiveLossVerdict
from tools import live_demo as live


def test_le_worker_voit_le_verrou_de_quarantaine(monkeypatch, tmp_path):
    """Le second lecteur ne peut plus dire ALLOW pendant que le moteur bloque.

    Le verrou est le seul ecart : la fenetre glissante est saine, donc un
    lecteur qui ne lirait que la fenetre repondrait ALLOW.
    """
    import json
    from datetime import datetime

    from titanium.execution import live_loss_guard
    from titanium.execution.live_loss_guard import (
        LiveLossVerdict,
        persist_live_loss_quarantine,
    )
    from titanium.execution.mt5_executor import ExecutionPolicy
    from tools import analystes

    maintenant = datetime.now(timezone.utc)
    journal = tmp_path / "trades.ndjson"
    journal.write_text(
        json.dumps({
            "context": "DAX40.fs|long|reversal|3p",
            "pnl_r": 0.5,
            "closed_at": maintenant.isoformat(),
            "ticket": "live:1",
            "source": "live",
            "account": "10055401",
            "exact_net": True,
            "horloge": "utc",
        }) + "\n",
        encoding="utf-8",
    )
    verrou = tmp_path / "10055401.json"
    persist_live_loss_quarantine(
        LiveLossVerdict(
            action="BLOCK",
            reason="DAILY_LOSS_LIMIT",
            daily_trades=9,
            daily_net_r=-2.8193,
        ),
        path=verrou,
        account="10055401",
        now=maintenant,
    )

    monkeypatch.setattr(analystes, "RACINE", tmp_path)
    monkeypatch.setattr(
        live_loss_guard,
        "live_loss_guard_path",
        lambda _racine, _compte: verrou,
    )
    monkeypatch.setattr(
        ExecutionPolicy,
        "from_config",
        classmethod(lambda _cls: SimpleNamespace(expected_demo_login=10055401)),
    )

    # La fenetre glissante est saine : seul le verrou peut bloquer.
    assert live_loss_guard.evaluate_live_loss_guard(
        journal, account="10055401", now=maintenant,
    ).action == "ALLOW"

    verdict = analystes._entry_loss_gate()

    assert verdict.action == "BLOCK"
    assert verdict.reason == "PERSISTENT_LOSS_QUARANTINE"
    assert verdict.quarantine_reason == "DAILY_LOSS_LIMIT"


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


def test_worker_loss_gate_uses_common_demo_cohort_start(monkeypatch, tmp_path):
    from titanium.execution import live_loss_guard
    from titanium.execution.mt5_executor import ExecutionPolicy
    from tools import analystes

    captured = {}

    def evaluate(_path, *, account, not_before=None, **_kwargs):
        captured.update(account=account, not_before=not_before)
        return LiveLossVerdict("ALLOW", "WITHIN_LOSS_LIMITS")

    monkeypatch.setattr(live_loss_guard, "evaluate_live_loss_guard", evaluate)
    # Le verrou vit dans l'arbre du compte : le test nomme son emplacement au
    # lieu de dependre de l'etat de la quarantaine au moment ou il tourne.
    monkeypatch.setattr(
        live_loss_guard,
        "live_loss_guard_path",
        lambda _racine, account: tmp_path / f"{account}.json",
    )
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
