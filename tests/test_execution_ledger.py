"""Offline fault injection: no MT5 import, no credentials, no live orders."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from titanium.execution import execution_ledger as ledger
from titanium.execution.mt5_executor import ExecutionPolicy, OrderResult

ACCOUNT = NS(login=123, server="demo", trade_mode=0)
POLICY = ExecutionPolicy(enabled=True, expected_demo_login=123)
IDENTITY = dict.fromkeys(ledger.IDENTITY_FIELDS, "test-version")


def send(path, executor=None, **changes):
    args = {"account": ACCOUNT, "policy": POLICY, "identity": IDENTITY,
                "idempotency_key": "BTCUSD:M1:1", "path": path}
    args.update(changes)
    return ledger.execute_recorded(executor or accepted, "BTCUSD", 1, 2., 100., **args)


def accepted(*args, **kwargs):
    return OrderResult(sent=True, ticket=11, retcode=10009, request_attempted=True,
                       filled_volume=.1, broker_deal_ticket=77)


def deal(ticket=77, order=11, entry=0, volume=.1, **changes):
    result = {"ticket": ticket, "order": order, "entry": entry, "volume": volume,
                  "position_id": 22, "symbol": "BTCUSD", "magic": 14000, "price": 100.,
                  "time": 1000, "time_msc": 1000000, "type": 0, "reason": 0,
                  "commission": -.1, "swap": 0., "profit": 0., "fee": 0.}
    result.update(changes)
    return NS(**result)


class Broker:
    def __init__(self, deals=None, account=ACCOUNT):
        self.deals = deals if deals is not None else [deal()]
        self.account = account

    def account_info(self):
        return self.account

    def history_deals_get(self, *args, **kwargs):
        if "ticket" in kwargs:
            return tuple(d for d in self.deals if d.order == kwargs["ticket"])
        if "position" in kwargs:
            return tuple(d for d in self.deals if d.position_id == kwargs["position"])
        return tuple(self.deals)


def test_commit_intent_before_send_and_keep_comment_ownership(tmp_path):
    path = tmp_path / "trace.db"
    def executor(*args, **kw):
        assert ledger.ledger_summary(path)["states"] == {"SUBMITTING": 1}
        assert kw["policy"].comment.startswith("titanium-v14-")
        assert len(kw["policy"].comment) <= 29
        assert kw["policy"].enabled == POLICY.enabled
        return accepted()
    result = send(path, executor)
    assert result.trace_state == "ACKNOWLEDGED"
    assert ledger.ledger_summary(path)["intents"] == 1


def test_restart_and_policy_change_do_not_duplicate_bar(tmp_path):
    path = tmp_path / "trace.db"
    send(path)
    result = send(path, lambda *a, **kw: pytest.fail("duplicate send"),
                  identity=dict.fromkeys(ledger.IDENTITY_FIELDS, "changed"))
    assert result.reason == "TRACE_DUPLICATE"


def test_concurrent_writers_only_one_attempt(tmp_path):
    path = tmp_path / "trace.db"
    # Initialise WAL; contention is on the atomic reservation, not fixture setup.
    with ledger._db(path):
        pass
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: send(path), range(4)))
    assert sum(r.sent for r in results) == 1


@pytest.mark.parametrize("code", [None, 10010, 10011, 10012, 10028, 10031, 99999])
def test_uncertain_ack_blocks_next_bar_and_other_symbol(tmp_path, code):
    path = tmp_path / "trace.db"
    first = send(path, lambda *a, **kw: OrderResult(retcode=code, request_attempted=True))
    assert first.trace_state == "UNKNOWN"
    second = ledger.execute_recorded(accepted, "ETHUSD", -1, 2., 10.,
                                     account=ACCOUNT, policy=POLICY, identity=IDENTITY,
                                     idempotency_key="next", path=path)
    assert second.reason == "TRACE_UNRESOLVED_ACCOUNT"


def test_explicit_rejection_is_not_uncertainty(tmp_path):
    path = tmp_path / "trace.db"
    result = send(path, lambda *a, **kw: OrderResult(retcode=10016, request_attempted=True))
    assert result.trace_state == "REJECTED"
    assert send(path, idempotency_key="next").sent


def test_exception_after_send_is_never_retried(tmp_path):
    def broken(*a, **kw):
        raise RuntimeError("DO_NOT_RECORD_SECRET")
    path = tmp_path / "trace.db"
    result = send(path, broken)
    assert result.trace_state == "UNKNOWN"
    assert b"DO_NOT_RECORD_SECRET" not in path.read_bytes()


@pytest.mark.parametrize("changes", [
    {"policy": ExecutionPolicy()},
    {"account": NS(login=123, server="live", trade_mode=2)},
    {"account": NS(login=456, server="demo", trade_mode=0)},
    {"policy": replace(POLICY, allow_real_account=True)},
    {"idempotency_key": ""}, {"identity": {}},
])
def test_invalid_or_disarmed_never_calls_executor(tmp_path, changes):
    result = send(tmp_path / "trace.db", lambda *a, **kw: pytest.fail("must not send"), **changes)
    assert not result.sent


def test_storage_failure_prevents_send(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a sqlite database")
    assert send(path, lambda *a, **kw: pytest.fail("must not send")).reason == "TRACE_UNAVAILABLE"


def test_ack_write_failure_preserves_actual_send_and_durable_uncertainty(tmp_path, monkeypatch):
    path = tmp_path / "trace.db"
    def executor(*a, **kw):
        def fail(*args):
            raise OSError("disk full")
        monkeypatch.setattr(ledger, "_db", fail)
        return accepted()
    result = send(path, executor)
    assert result.sent and result.ticket == 11
    assert result.trace_state == "SUBMITTING"
    assert ledger.ledger_summary(path)["unresolved"] == 1


def test_all_partial_fills_and_manual_partial_exits_are_retained(tmp_path):
    path = tmp_path / "trace.db"
    send(path)
    broker = Broker([deal(volume=.04), deal(78, volume=.06),
                     deal(79, order=90, entry=1, volume=.03, magic=0),
                     deal(80, order=91, entry=1, volume=.07, magic=0)])
    assert ledger.reconcile_recorded(broker, account=ACCOUNT, path=path)["status"] == "OK"
    ledger.reconcile_recorded(broker, account=ACCOUNT, path=path)
    assert ledger.ledger_summary(path)["deals"] == 4
    with sqlite3.connect(path) as db:
        assert all('"position_id":22' in r[0] for r in db.execute("SELECT payload FROM deals"))


def test_empty_ticket_filter_falls_back_to_bounded_exact_order_match(tmp_path):
    path = tmp_path / "trace.db"
    send(path)
    broker = Broker()
    calls = []

    def target_terminal_behavior(*args, **kwargs):
        calls.append((args, kwargs))
        if "ticket" in kwargs:
            return None
        if "position" in kwargs:
            return tuple(d for d in broker.deals if d.position_id == kwargs["position"])
        return tuple(broker.deals)

    broker.history_deals_get = target_terminal_behavior
    broker.last_error = lambda: (-2, "Terminal: Invalid params")
    result = ledger.reconcile_recorded(broker, account=ACCOUNT, path=path)

    assert result["status"] == "OK"
    assert ledger.ledger_summary(path)["deals"] == 1
    assert calls[0] == ((), {"ticket": 11})
    assert len(calls[1][0]) == 2 and calls[1][1] == {}


def test_missing_history_not_reported_as_empty_success(tmp_path):
    path = tmp_path / "trace.db"
    send(path)
    broker = Broker()
    broker.history_deals_get = lambda *args, **kw: None
    with pytest.raises(ValueError, match="HISTORY_UNAVAILABLE"):
        ledger.reconcile_recorded(broker, account=ACCOUNT, path=path)


def test_account_changed_aborts_before_history(tmp_path):
    path = tmp_path / "trace.db"
    send(path)
    broker = Broker(account=NS(login=123, server="live", trade_mode=2))
    broker.history_deals_get = lambda **kw: pytest.fail("wrong account read")
    with pytest.raises(ValueError):
        ledger.reconcile_recorded(broker, account=ACCOUNT, path=path)


def test_changed_broker_deal_does_not_overwrite_evidence(tmp_path):
    path = tmp_path / "trace.db"
    send(path)
    ledger.reconcile_recorded(Broker(), account=ACCOUNT, path=path)
    with pytest.raises(ValueError, match="BROKER_DEAL_CHANGED"):
        ledger.reconcile_recorded(Broker([deal(fee=9)]), account=ACCOUNT, path=path)


def test_netting_allocation_requires_review(tmp_path):
    path = tmp_path / "trace.db"
    send(path)
    result = ledger.reconcile_recorded(Broker([deal(), deal(78, order=12)]),
                                       account=ACCOUNT, path=path)
    assert result["status"] == "WAIT"
    assert "POSITION_ALLOCATION_REQUIRED" in result["issues"]


def test_lost_ack_exact_tag_recovers_order_but_does_not_approve_retry(tmp_path):
    path = tmp_path / "trace.db"
    tags = []
    def lost(*a, **kw):
        tags.append(kw["policy"].comment)
        return OrderResult(request_attempted=True)
    send(path, lost)
    broker = Broker()
    broker.orders_get = lambda: ()
    broker.history_orders_get = lambda *a: (NS(ticket=11, comment=tags[0],
                                               symbol="BTCUSD", magic=14000),)
    result = ledger.reconcile_recorded(broker, account=ACCOUNT, path=path)
    assert result["status"] == "WAIT" and result["unresolved"] == 1
    assert ledger.ledger_summary(path)["deals"] == 1


def test_review_resolves_only_when_exact_tag_absent_everywhere(tmp_path):
    path = tmp_path / "trace.db"
    send(path, lambda *a, **kw: OrderResult(request_attempted=True))

    class Reviewer:
        def account_info(self): return ACCOUNT
        def history_orders_get(self, *args): return ()
        def orders_get(self): return ()
        def history_deals_get(self, *args): return ()

    review = ledger.resolve_no_broker_order(Reviewer(), account=ACCOUNT, path=path,
                                            min_age_s=0)
    assert review == {"status": "RESOLVED_NO_ORDER", "resolved": 1}
    assert ledger.ledger_summary(path)["unresolved"] == 0
    assert send(path, idempotency_key="next").sent


def test_review_keeps_uncertainty_when_exact_tag_exists(tmp_path):
    path = tmp_path / "trace.db"
    send(path, lambda *a, **kw: OrderResult(request_attempted=True))
    with sqlite3.connect(path) as db:
        payload = json.loads(db.execute("SELECT payload FROM intents").fetchone()[0])
    order = NS(ticket=91, comment=payload["client_tag"], magic=payload["magic"],
               symbol="BTCUSD")

    class Reviewer:
        def account_info(self): return ACCOUNT
        def history_orders_get(self, *args): return (order,)
        def orders_get(self): return ()
        def history_deals_get(self, *args): return ()

    review = ledger.resolve_no_broker_order(Reviewer(), account=ACCOUNT, path=path,
                                            min_age_s=0)
    assert review["status"] == "STILL_UNCERTAIN"
    assert ledger.ledger_summary(path)["unresolved"] == 1


def test_review_keeps_uncertainty_when_broker_truncates_tag(tmp_path):
    path = tmp_path / "trace.db"
    send(path, lambda *a, **kw: OrderResult(request_attempted=True))
    with sqlite3.connect(path) as db:
        payload = json.loads(db.execute("SELECT payload FROM intents").fetchone()[0])
    order = NS(ticket=91, comment=payload["client_tag"][:-1], magic=payload["magic"],
               symbol="BTCUSD")

    class Reviewer:
        def account_info(self): return ACCOUNT
        def history_orders_get(self, *args): return (order,)
        def orders_get(self): return ()
        def history_deals_get(self, *args): return ()

    review = ledger.resolve_no_broker_order(Reviewer(), account=ACCOUNT, path=path,
                                            min_age_s=0)
    assert review["status"] == "STILL_UNCERTAIN"
    assert ledger.ledger_summary(path)["unresolved"] == 1


def test_review_ignores_another_v14_intent_on_same_symbol(tmp_path):
    path = tmp_path / "trace.db"
    send(path, lambda *a, **kw: OrderResult(request_attempted=True))
    with sqlite3.connect(path) as db:
        payload = json.loads(db.execute("SELECT payload FROM intents").fetchone()[0])
    other = "titanium-v14-" + ("0" if payload["client_tag"][13] != "0" else "1") * 18
    order = NS(ticket=92, comment=other, magic=payload["magic"], symbol="BTCUSD")

    class Reviewer:
        def account_info(self): return ACCOUNT
        def history_orders_get(self, *args): return (order,)
        def orders_get(self): return ()
        def history_deals_get(self, *args): return ()

    review = ledger.resolve_no_broker_order(Reviewer(), account=ACCOUNT, path=path,
                                            min_age_s=0)
    assert review["status"] == "RESOLVED_NO_ORDER"
    assert ledger.ledger_summary(path)["unresolved"] == 0


def test_missing_summary_does_not_create_database(tmp_path):
    path = tmp_path / "trace.db"
    assert ledger.ledger_summary(path)["status"] == "NOT_STARTED"
    assert not path.exists()


def test_invalid_local_request_is_rejected_without_blocking_account(tmp_path):
    path = tmp_path / "trace.db"
    result = OrderResult(reason="ORDER_SEND_NUL", request_attempted=True)
    result.terminal_error_code = -2
    result._add("send", False, "DO_NOT_RECORD_SECRET")
    send(path, lambda *a, **kw: result)
    with sqlite3.connect(path) as db:
        state, raw = db.execute("SELECT state, outcome FROM intents").fetchone()
    evidence = json.loads(raw)
    assert state == "REJECTED"
    assert evidence["reason"] == "ORDER_SEND_NUL"
    assert evidence["terminal_error_code"] == -2
    assert "DO_NOT_RECORD_SECRET" not in raw
    assert send(path, idempotency_key="next").sent


def test_trace_does_not_persist_arbitrary_reason_or_terminal_text(tmp_path):
    path = tmp_path / "trace.db"
    result = OrderResult(reason="DO_NOT_RECORD_SECRET", request_attempted=True)
    result.terminal_error_code = "DO_NOT_RECORD_SECRET"
    send(path, lambda *a, **kw: result)
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT outcome FROM intents").fetchone()[0]
    assert "DO_NOT_RECORD_SECRET" not in raw
    assert json.loads(raw)["terminal_error_code"] is None


def test_live_loop_calls_guarded_executor_through_ledger_after_gates():
    import ast
    import inspect

    from tools import live_demo

    tree = ast.parse(inspect.getsource(live_demo.tour))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    recorded = [n for n in calls if isinstance(n.func, ast.Name)
                and n.func.id == "execute_recorded"]
    assert len(recorded) == 1
    invocation = recorded[0]
    assert isinstance(invocation.args[0], ast.Call)
    assert invocation.args[0].func.id == "_envoi_entree"
    assert {"account", "policy", "identity", "idempotency_key", "path"} <= {
        kw.arg for kw in invocation.keywords
    }
    assert not any(isinstance(n.func, ast.Attribute) and n.func.attr == "order_send" for n in calls)
