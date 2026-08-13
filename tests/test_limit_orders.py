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
    append_limit_event,
    limit_lifecycle_summary,
    reconcile_pending_contexts,
    save_pending_context,
)
from titanium.execution.position_manager import TrackedState, load_state, save_state


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
    # MetaTrader5 refuse un datetime : order_send renvoie None et rien n est
    # envoye. L expiration doit etre un horodatage POSIX entier.
    assert isinstance(request["expiration"], int)
    assert not isinstance(request["expiration"], bool)
    attendu = datetime.now(timezone.utc) + timedelta(seconds=600)
    assert abs(request["expiration"] - int(attendu.timestamp())) <= 5
    assert result.market_reference_price == pytest.approx(terminal.ask)


def test_expiration_datetime_serait_refusee_par_le_terminal(monkeypatch):
    """Regression 12/08/2026 : 100 % de ORDER_SEND_NUL, zero limite posee.

    Le vrai terminal valide le type de ``expiration`` AVANT d envoyer quoi que
    ce soit et renvoie ``None``. Ce faux terminal reproduit ce contrat pour que
    la suite echoue si un datetime revenait un jour dans la requete.
    """

    class StrictMt5(FakeMt5):
        def order_send(self, request):
            self.requests.append(request)
            if not isinstance(request.get("expiration"), int):
                self.erreur = (-2, 'Invalid "expiration" argument')
                return None
            return FakeResult()

    install(monkeypatch, mt5=StrictMt5())
    result = place_limit_order(
        "EURUSD", 1, 100.0, 0.005, policy=policy(), tp_distance=0.0075,
        idempotency_key="EURUSD:M15:bar-strict",
    )
    assert result.sent and result.pending
    assert result.reason == "PLACED_LIMIT"


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
        asset_class="forex", limit_order_ticket=555,
        limit_planned_price=1.1000, limit_market_reference_price=1.1002,
        limit_target_saving_r=0.04,
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
        lifecycle_path=tmp_path / "limit_lifecycle.ndjson",
    )
    state = load_state(state_path)
    assert report["adopted"] == 1
    assert state["999"].context_key == template.context_key
    assert state["999"].entry == pytest.approx(1.0999)
    assert state["999"].r == pytest.approx(0.005)
    assert state["999"].limit_realized_saving_r == pytest.approx(0.06)
    assert state["999"].limit_slippage_r == pytest.approx(-0.02)
    assert report["events_written"] == 1
    summary = limit_lifecycle_summary(tmp_path / "limit_lifecycle.ndjson")
    assert summary["filled"] == 1
    assert summary["mean_realized_saving_r"] == pytest.approx(0.06)


def test_limite_expiree_est_classee_et_journalisee(tmp_path):
    pending_path = tmp_path / "pending.json"
    lifecycle_path = tmp_path / "limit_lifecycle.ndjson"
    state_path = tmp_path / "positions.json"
    template = TrackedState(
        r=0.005, symbol="EURUSD", side=1, entry=1.1000,
        context_key="EURUSD|long|continuation|3p",
        limit_order_ticket=555, limit_planned_price=1.1000,
        limit_market_reference_price=1.1002, limit_target_saving_r=0.04,
    )
    save_pending_context(
        pending_path, order_ticket=555, symbol="EURUSD", side=1,
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        state=template,
    )

    class Terminal:
        ORDER_STATE_CANCELED = 2
        ORDER_STATE_EXPIRED = 6

        def orders_get(self):
            return ()

        def history_orders_get(self, **kwargs):
            return [SimpleNamespace(
                ticket=555, state=6, comment="expired", time_done=123,
            )]

    report = reconcile_pending_contexts(
        Terminal(), magic=14_000, state_path=state_path,
        pending_path=pending_path, positions=[], lifecycle_path=lifecycle_path,
    )
    assert report["expired"] == 1
    assert report["canceled"] == 0
    assert report["purged"] == 1
    assert limit_lifecycle_summary(lifecycle_path)["expired"] == 1


def test_journal_cycle_limite_est_append_only_et_idempotent(tmp_path):
    path = tmp_path / "limit_lifecycle.ndjson"
    event = {
        "event": "placed", "order_ticket": 555, "symbol": "EURUSD",
        "regime": "continuation",
    }
    assert append_limit_event(path, event) == (True, "WRITTEN")
    assert append_limit_event(path, event) == (False, "DUPLICATE")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    summary = limit_lifecycle_summary(path)
    assert summary["placed"] == 1
    assert summary["open"] == 1
    assert summary["by_symbol"]["EURUSD"]["placed"] == 1


def test_disparition_sans_historique_ne_devient_pas_une_fausse_expiration(tmp_path):
    pending_path = tmp_path / "pending.json"
    lifecycle_path = tmp_path / "limit_lifecycle.ndjson"
    template = TrackedState(
        r=0.005, symbol="EURUSD", side=1, entry=1.1000,
        context_key="EURUSD|long|continuation|3p", limit_order_ticket=777,
    )
    save_pending_context(
        pending_path, order_ticket=777, symbol="EURUSD", side=1,
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        state=template,
    )

    class Terminal:
        def orders_get(self):
            return ()

        def history_orders_get(self, **kwargs):
            return ()

    report = reconcile_pending_contexts(
        Terminal(), magic=14_000, state_path=tmp_path / "positions.json",
        pending_path=pending_path, positions=[], lifecycle_path=lifecycle_path,
    )
    assert report["unknown"] == 1
    assert report["expired"] == 0
    assert limit_lifecycle_summary(lifecycle_path)["unknown"] == 1


def test_fill_et_cloture_entre_deux_tours_conservent_le_contexte(tmp_path):
    """Un fill ferme avant le prochain tour ne doit pas devenir ``unknown``."""
    pending_path = tmp_path / "pending.json"
    lifecycle_path = tmp_path / "limit_lifecycle.ndjson"
    state_path = tmp_path / "positions.json"
    template = TrackedState(
        r=0.005, symbol="EURUSD", side=1, entry=1.1000,
        sl_initial=1.0950, tp_initial=1.1075,
        context_key="EURUSD|long|continuation|3p", risque_devise=25.0,
        asset_class="fx", limit_order_ticket=555,
        limit_planned_price=1.1000, limit_market_reference_price=1.1002,
        limit_target_saving_r=0.04,
    )
    save_pending_context(
        pending_path, order_ticket=555, symbol="EURUSD", side=1,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        state=template,
    )

    class Terminal:
        ORDER_TYPE_BUY = 0
        ORDER_STATE_FILLED = 4

        def orders_get(self):
            return ()

        def history_orders_get(self, **kwargs):
            return [SimpleNamespace(
                ticket=555, state=4, position_id=999, time_done=123,
                price_open=1.1000, price_current=1.0999,
                sl=1.0949, tp=1.1074, comment="filled",
            )]

    report = reconcile_pending_contexts(
        Terminal(), magic=14_000, state_path=state_path,
        pending_path=pending_path, positions=[], lifecycle_path=lifecycle_path,
    )

    restored = load_state(state_path)["999"]
    assert report["adopted"] == 1
    assert report["recovered_filled"] == 1
    assert report["unknown"] == 0
    assert restored.context_key == template.context_key
    assert restored.entry == pytest.approx(1.0999)
    assert restored.r == pytest.approx(0.005)
    assert restored.limit_realized_saving_r == pytest.approx(0.06)
    assert restored.limit_slippage_r == pytest.approx(-0.02)
    assert pending_path.read_text(encoding="utf-8").strip() == "{}"
    summary = limit_lifecycle_summary(lifecycle_path)
    assert summary["filled"] == 1
    assert summary["unknown"] == 0


def test_fill_historique_sans_position_id_reste_fail_closed(tmp_path):
    pending_path = tmp_path / "pending.json"
    template = TrackedState(
        r=0.005, symbol="EURUSD", side=1, entry=1.1000,
        context_key="EURUSD|long|continuation|3p", limit_order_ticket=555,
    )
    save_pending_context(
        pending_path, order_ticket=555, symbol="EURUSD", side=1,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        state=template,
    )

    class Terminal:
        ORDER_STATE_FILLED = 4

        def orders_get(self):
            return ()

        def history_orders_get(self, **kwargs):
            return [SimpleNamespace(
                ticket=555, state=4, position_id=0, time_done=123,
                price_current=1.1000,
            )]

    report = reconcile_pending_contexts(
        Terminal(), magic=14_000, state_path=tmp_path / "positions.json",
        pending_path=pending_path, positions=[],
        lifecycle_path=tmp_path / "lifecycle.ndjson",
    )

    assert report["adopted"] == 0
    assert report["recovered_filled"] == 0
    assert report["pending"] == 1
    assert "555" in pending_path.read_text(encoding="utf-8")


def test_fill_deja_adopte_recupere_sa_provenance_depuis_le_journal(tmp_path):
    lifecycle_path = tmp_path / "limit_lifecycle.ndjson"
    state_path = tmp_path / "positions.json"
    save_state(state_path, {
        "89325926": TrackedState(
            r=14.073, symbol="ETHUSD", side=1, entry=1894.4,
            sl_initial=1880.327, context_key="ETHUSD|long|continuation|3p",
            asset_class="crypto", risque_devise=20.81,
        ),
    })
    append_limit_event(lifecycle_path, {
        "event": "placed", "order_ticket": 89325926, "symbol": "ETHUSD",
        "side": 1, "planned_price": 1894.467,
        "market_reference_price": 1896.26, "target_saving_r": 0.1268,
        "regime": "continuation",
    })
    append_limit_event(lifecycle_path, {
        "event": "filled", "order_ticket": 89325926,
        "position_ticket": 89325926, "symbol": "ETHUSD", "side": 1,
        "fill_price": 1894.4,
    })

    report = reconcile_pending_contexts(
        SimpleNamespace(), magic=14_000, state_path=state_path,
        pending_path=tmp_path / "pending.json", positions=[],
        lifecycle_path=lifecycle_path,
    )

    repaired = load_state(state_path)["89325926"]
    assert report["repaired"] == 1
    assert repaired.limit_order_ticket == 89325926
    assert repaired.limit_planned_price == pytest.approx(1894.467)
    assert repaired.limit_market_reference_price == pytest.approx(1896.26)
    assert repaired.limit_target_saving_r == pytest.approx(0.1268)
    assert repaired.limit_realized_saving_r == pytest.approx(
        (1896.26 - 1894.4) / 14.073)
    assert repaired.limit_slippage_r == pytest.approx(
        (1894.4 - 1894.467) / 14.073)
    summary = limit_lifecycle_summary(lifecycle_path)
    assert summary["filled"] == 1
    assert summary["mean_realized_saving_r"] == pytest.approx(
        repaired.limit_realized_saving_r)


def test_expiration_est_convertie_dans_le_fuseau_du_serveur(monkeypatch):
    """Regression 12/08/2026 : retcode 10022 sur 15 tentatives sur 15.

    Le serveur Axi est a UTC+3. Une expiration UTC lui parait passee.
    """

    class Mt5Decale(FakeMt5):
        def symbol_info_tick(self, symbol):
            return SimpleNamespace(
                bid=self.bid, ask=self.ask,
                time=int(datetime.now(timezone.utc).timestamp()) + 3 * 3600,
            )

    terminal = install(monkeypatch, mt5=Mt5Decale())
    result = place_limit_order(
        "EURUSD", 1, 100.0, 0.005, policy=policy(), tp_distance=0.0075,
        idempotency_key="EURUSD:M15:bar-decale",
    )
    assert result.sent
    envoye = terminal.requests[0]["expiration"]
    attendu_utc = datetime.now(timezone.utc) + timedelta(seconds=600)
    assert envoye - int(attendu_utc.timestamp()) == pytest.approx(3 * 3600, abs=5)
    # Le journal, lui, reste en UTC : deux horloges melangees dans les traces
    # rendraient toute analyse posterieure fausse.
    assert result.expires_at.endswith("+00:00")


def test_decalage_aberrant_est_ignore(monkeypatch):
    """Un tick vieux d une semaine ne doit pas deplacer l expiration."""

    class Mt5Aberrant(FakeMt5):
        def symbol_info_tick(self, symbol):
            return SimpleNamespace(
                bid=self.bid, ask=self.ask,
                time=int(datetime.now(timezone.utc).timestamp()) - 7 * 86400,
            )

    terminal = install(monkeypatch, mt5=Mt5Aberrant())
    place_limit_order(
        "EURUSD", 1, 100.0, 0.005, policy=policy(),
        idempotency_key="EURUSD:M15:bar-aberrant",
    )
    envoye = terminal.requests[0]["expiration"]
    attendu = datetime.now(timezone.utc) + timedelta(seconds=600)
    assert abs(envoye - int(attendu.timestamp())) <= 5


def test_decalage_est_arrondi_au_quart_d_heure(monkeypatch):
    """La latence du tick ne doit pas fabriquer un decalage de quelques secondes."""

    class Mt5Latence(FakeMt5):
        def symbol_info_tick(self, symbol):
            return SimpleNamespace(
                bid=self.bid, ask=self.ask,
                time=int(datetime.now(timezone.utc).timestamp()) - 7,
            )

    terminal = install(monkeypatch, mt5=Mt5Latence())
    place_limit_order(
        "EURUSD", 1, 100.0, 0.005, policy=policy(),
        idempotency_key="EURUSD:M15:bar-latence",
    )
    envoye = terminal.requests[0]["expiration"]
    attendu = datetime.now(timezone.utc) + timedelta(seconds=600)
    assert abs(envoye - int(attendu.timestamp())) <= 5
