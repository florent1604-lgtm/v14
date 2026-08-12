"""Entrées limites adaptatives — calcul pur, mur DEMO et contexte de fill."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from titanium.data.mt5_vendor import AccountSnapshot, SymbolSpec
from titanium.execution import limit_orders as lo
from titanium.execution.limit_orders import place_limit_order, plan_limit_entry
from titanium.execution.mt5_executor import ExecutionPolicy
from titanium.execution.pending_context import (
    reconcile_pending_contexts,
    save_pending_context,
)
from titanium.execution.position_manager import TrackedState, load_state


def spec(**over) -> SymbolSpec:
    base = dict(
        name="EURUSD", digits=5, point=1e-5, volume_min=0.01,
        volume_max=100.0, volume_step=0.01, trade_contract_size=100_000.0,
        spread=20, tick_value=1.0, tick_size=1e-5,
    )
    base.update(over)
    return SymbolSpec(**base)


def account(*, demo=True) -> AccountSnapshot:
    return AccountSnapshot(
        login=42 if demo else 99, server="Axi-Demo" if demo else "Axi-Live",
        currency="EUR", balance=5000, equity=5000, margin_free=4900,
        is_demo=demo, trade_mode=0 if demo else 2,
    )


def policy() -> ExecutionPolicy:
    return ExecutionPolicy(enabled=True, expected_demo_login=42)


class FakeResult:
    retcode = 10008
    order = 555
    price = 0.0
    comment = "placed"


class FakeMt5:
    TRADE_ACTION_PENDING = 5
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_SPECIFIED = 2
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE = 10009

    def __init__(self, bid=1.1000, ask=1.1002):
        self.bid, self.ask = bid, ask
        self.requests = []

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=self.bid, ask=self.ask)

    def order_send(self, request):
        self.requests.append(request)
        return FakeResult()

    def last_error(self):
        return (0, "ok")


@pytest.fixture(autouse=True)
def reset_keys():
    lo.reset_limit_idempotency()
    yield
    lo.reset_limit_idempotency()


def install(monkeypatch, mt5=None, acc=None):
    terminal = mt5 or FakeMt5()

    @contextmanager
    def session():
        yield terminal

    monkeypatch.setattr(lo, "account_snapshot", lambda: acc or account())
    monkeypatch.setattr(lo, "ensure_symbol", lambda symbol: spec())
    monkeypatch.setattr(lo, "mt5_session", session)
    return terminal


def test_plan_buy_limit_reste_passif_et_capture_le_spread():
    plan = plan_limit_entry(spec(), bid=1.1000, ask=1.1002, side=1,
                            stop_distance=0.005)
    assert plan.price == pytest.approx(1.1000)
    assert plan.saving_vs_market == pytest.approx(0.0002)
    assert plan.spread_r == pytest.approx(0.04)
    assert plan.ttl_seconds == 600


def test_plan_spread_couteux_exige_un_prix_meilleur_et_expire_vite():
    plan = plan_limit_entry(spec(), bid=1.1000, ask=1.1010, side=1,
                            stop_distance=0.005)
    assert plan.price == pytest.approx(1.09975)
    assert plan.passive_extra == pytest.approx(0.00025)
    assert plan.ttl_seconds == 120


def test_plan_sell_limit_est_symetrique():
    plan = plan_limit_entry(spec(), bid=1.1000, ask=1.1002, side=-1,
                            stop_distance=0.005)
    assert plan.price == pytest.approx(1.1002)
    assert plan.saving_vs_market == pytest.approx(0.0002)


def test_place_limit_envoie_pending_avec_expiration(monkeypatch):
    terminal = install(monkeypatch)
    result = place_limit_order(
        "EURUSD", 1, 100.0, 0.005, policy=policy(), tp_distance=0.0075,
        idempotency_key="EURUSD:M15:bar",
    )
    assert result.sent and result.pending
    assert result.reason == "PLACED_LIMIT"
    request = terminal.requests[0]
    assert request["action"] == terminal.TRADE_ACTION_PENDING
    assert request["type"] == terminal.ORDER_TYPE_BUY_LIMIT
    assert request["price"] <= terminal.bid
    assert request["sl"] == pytest.approx(request["price"] - 0.005)
    assert request["tp"] == pytest.approx(request["price"] + 0.0075)
    assert request["expiration"].tzinfo is not None


def test_limit_reapplique_le_mur_demo(monkeypatch):
    terminal = install(monkeypatch, acc=account(demo=False))
    result = place_limit_order("EURUSD", 1, 100.0, 0.005, policy=policy())
    assert not result.sent
    assert result.reason == "WALL_NOT_DEMO"
    assert not terminal.requests


def test_limit_idempotente(monkeypatch):
    terminal = install(monkeypatch)
    first = place_limit_order("EURUSD", 1, 100.0, 0.005, policy=policy(),
                              idempotency_key="same")
    second = place_limit_order("EURUSD", 1, 100.0, 0.005, policy=policy(),
                               idempotency_key="same")
    assert first.sent
    assert second.reason == "DEJA_ENVOYE"
    assert len(terminal.requests) == 1


def test_contexte_pending_transfere_au_ticket_de_position(tmp_path):
    pending_path = tmp_path / "pending.json"
    state_path = tmp_path / "positions.json"
    template = TrackedState(
        r=0.005, symbol="EURUSD", side=1, entry=1.1000,
        sl_initial=1.0950, tp_initial=1.1075,
        context_key="EURUSD|long|continuation|3p", risque_devise=25.0,
    )
    save_pending_context(
        pending_path, order_ticket=555, symbol="EURUSD", side=1,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        state=template,
    )
    pos = SimpleNamespace(
        ticket=999, magic=14_000, symbol="EURUSD", type=0,
        price_open=1.0999, sl=1.0949, tp=1.1074,
    )

    class Terminal:
        ORDER_TYPE_BUY = 0

        def orders_get(self):
            return ()

    report = reconcile_pending_contexts(
        Terminal(), magic=14_000, state_path=state_path,
        pending_path=pending_path, positions=[pos],
    )
    state = load_state(state_path)
    assert report["adopted"] == 1
    assert state["999"].context_key == template.context_key
    assert state["999"].entry == pytest.approx(1.0999)
    assert state["999"].r == pytest.approx(0.005)
