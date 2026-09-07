"""Durable entry intents and broker evidence, independent of strategy artifacts.

SQLite commits BEFORE invoking the guarded executor. An uncertain acknowledgement
blocks further entries on that account (including after restart); it never retries.
Reconciliation reads MT5 only, and never guesses an order from symbol/time proximity.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from titanium.analysis.execution_trace import ledger_summary  # noqa: F401 - public read-only helper
from titanium.execution.mt5_executor import OrderResult
from titanium.execution.policy_identity import IDENTITY_FIELDS

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "results" / "execution_ledger.sqlite3"
_reconcile_cursor: dict[str, int] = {}
UNCERTAIN = ("SUBMITTING", "UNKNOWN")
# Positive acknowledgement is not necessarily a fill. Unknown/new codes stay UNKNOWN.
REJECT_CODES = {10004, 10006, 10007, *range(10013, 10023), 10024, 10026, 10027,
                10030, *range(10032, 10036), 10040, *range(10042, 10047)}
DEAL_FIELDS = ("ticket", "order", "position_id", "time", "time_msc", "type", "entry",
               "magic", "reason", "volume", "price", "commission", "swap", "profit", "fee")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _scope(account):
    if account is None or account.trade_mode != 0 or not account.server or account.login <= 0:
        raise ValueError("DEMO_IDENTITY_REQUIRED")
    return _json([account.server, int(account.login)])


@contextmanager
def _db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS intents (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, symbol TEXT NOT NULL,
                created REAL NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                order_ticket INTEGER, outcome TEXT);
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, intent_id TEXT NOT NULL,
                at REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deals (
                account TEXT NOT NULL, ticket INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(account, ticket));
        """)
        yield db
    finally:
        db.close()


def _event(db, intent, kind, payload):
    db.execute("INSERT INTO events(intent_id,at,kind,payload) VALUES (?,?,?,?)",
               (intent, time.time(), kind, _json(payload)))


def execute_recorded(executor, symbol, side, risk_money, stop_distance, *, policy,
                     account, identity, idempotency_key, tp_distance=None,
                     path=DEFAULT_PATH):
    """Wrap the live loop's entry executor; preserve its guards and actual result.

    Identity fields are whitelisted, never a raw configuration or exception text.
    This is at-most-one *attempt*, not a promise of exactly-once broker execution.
    """
    refused = OrderResult(symbol=symbol, side=side, idempotency_key=idempotency_key)
    if not policy.enabled:
        refused.reason = "EXEC_DISARMED"
        return refused
    try:
        scope = _scope(account)
        if policy.allow_real_account or account.login != policy.expected_demo_login:
            raise ValueError("DEMO_IDENTITY_REQUIRED")
        if not idempotency_key or side not in (-1, 1):
            raise ValueError("INVALID_INTENT")
        if not all(math.isfinite(v) and v > 0 for v in (risk_money, stop_distance)):
            raise ValueError("INVALID_INTENT")
        version = {key: identity[key] for key in IDENTITY_FIELDS}
        if not all(isinstance(v, str) and v for v in version.values()):
            raise ValueError("MISSING_POLICY_IDENTITY")
        # Same bar stays the same intent across code/policy updates and tactics.
        intent = hashlib.sha256(_json([scope, symbol, idempotency_key]).encode()).hexdigest()
        tag = "titanium-v14-" + intent[:18]  # MT5 comment <=31 ASCII chars; ownership retained.
        payload = dict(symbol=symbol, side=side, risk_money=risk_money,
                       stop_distance=stop_distance, tp_distance=tp_distance,
                       bar_key=idempotency_key, magic=policy.magic, client_tag=tag, **version)
        with _db(path) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM intents WHERE id=?", (intent,)).fetchone():
                refused.reason = "TRACE_DUPLICATE"
                return refused
            if db.execute("SELECT 1 FROM intents WHERE account=? AND state IN (?,?)",
                          (scope, *UNCERTAIN)).fetchone():
                refused.reason = "TRACE_UNRESOLVED_ACCOUNT"
                return refused
            db.execute("INSERT INTO intents VALUES (?,?,?,?,?,?,NULL,NULL)",
                       (intent, scope, symbol, time.time(), _json(payload), "SUBMITTING"))
            _event(db, intent, "INTENT", payload)
    except Exception:  # fail closed before any executor call; never expose secret text
        refused.reason = "TRACE_UNAVAILABLE"
        return refused

    started = time.perf_counter()
    try:
        result = executor(symbol, side, risk_money, stop_distance,
                          policy=replace(policy, comment=tag), tp_distance=tp_distance,
                          idempotency_key=idempotency_key)
    except Exception:
        result = refused
        result.reason = "TRACE_EXECUTOR_EXCEPTION"
        result.request_attempted = True  # cannot prove no request escaped
    state = "UNKNOWN"
    if result.sent and result.ticket and result.retcode != 10010:
        state = "ACKNOWLEDGED"
    elif not result.sent and (not result.request_attempted or result.retcode in REJECT_CODES):
        state = "REJECTED"
    evidence = {key: getattr(result, key) for key in (
        "sent", "ticket", "retcode", "lot", "price", "sl", "tp", "broker_deal_ticket",
        "filled_volume", "request_attempted", "requested_price", "reference_bid",
        "reference_ask", "quote_time_msc", "submitted_at", "acknowledged_at")}
    evidence["elapsed_seconds"] = time.perf_counter() - started
    result.trace_id, result.trace_state = intent, state
    try:
        with _db(path) as db, db:
            db.execute("UPDATE intents SET state=?,order_ticket=?,outcome=? WHERE id=?",
                       (state, result.ticket, _json(evidence), intent))
            _event(db, intent, state, evidence)
    except Exception:
        # Preserve the real send result; SUBMITTING remains durably fail-closed.
        result.trace_state = "SUBMITTING"
        result._add("execution_trace", False, "ACK_PERSIST_FAILED")
    return result


def _store_deals(db, scope, deals):
    for deal in deals:
        data = {key: getattr(deal, key, None) for key in DEAL_FIELDS}
        data["symbol"] = getattr(deal, "symbol", "")
        data["clock"] = "broker_raw"  # no invented UTC correction/latency
        if not data["ticket"] or not data["position_id"]:
            raise ValueError("INVALID_DEAL")
        payload = _json(data)
        old = db.execute("SELECT payload FROM deals WHERE account=? AND ticket=?",
                         (scope, data["ticket"])).fetchone()
        if old and old[0] != payload:
            raise ValueError("BROKER_DEAL_CHANGED")
        db.execute("INSERT OR IGNORE INTO deals VALUES (?,?,?)",
                   (scope, data["ticket"], payload))


def reconcile_recorded(mt5, *, account, path=DEFAULT_PATH):
    """Read-only MT5 evidence. Uncertain sends require review, never automatic retry.

    Never equate an order id with a position id. All position deals are collected,
    including manual exits (magic may differ) and partial fills/exits. No live PNL
    is reconstructed here from an estimate; original journals remain untouched.
    """
    if not Path(path).exists():
        return {"status": "NOT_STARTED", "unresolved": 0, "observed_intents": 0}
    scope = _scope(account)
    if _scope(mt5.account_info()) != scope:
        raise ValueError("ACCOUNT_CHANGED")
    issues, observed = [], 0
    with _db(path) as db:
        rows = db.execute("SELECT * FROM intents WHERE account=? AND state!='REJECTED'",
                          (scope,)).fetchall()
        unresolved = sum(r["state"] in UNCERTAIN for r in rows)
        # Bound broker work; round-robin also revisits closed positions for late fees.
        start_index = _reconcile_cursor.get(scope, 0) % max(1, len(rows))
        batch = (rows[start_index:] + rows[:start_index])[:20]
        for row in batch:
            payload = json.loads(row["payload"])
            ticket = row["order_ticket"]
            if not ticket:
                # Conservative date envelope; exact correlation is the tag, not time.
                start = datetime.fromtimestamp(row["created"], timezone.utc) - timedelta(days=1)
                end = datetime.now(timezone.utc) + timedelta(days=1)
                orders = mt5.history_orders_get(start, end)
                active = mt5.orders_get()
                if orders is None or active is None:
                    raise ValueError("ORDER_HISTORY_UNAVAILABLE")
                matches = {o.ticket for o in (*orders, *active)
                           if o.comment == payload["client_tag"]
                           and o.magic == payload["magic"] and o.symbol == row["symbol"]}
                if len(matches) == 1:
                    ticket = matches.pop()
                    with db:
                        db.execute("UPDATE intents SET order_ticket=? WHERE id=?",
                                   (ticket, row["id"]))
                        _event(db, row["id"], "RECOVERED_ORDER_REQUIRES_REVIEW", {"ticket": ticket})
            if not ticket:
                issues.append("UNRESOLVED_ORDER")
                continue
            entry_deals = mt5.history_deals_get(ticket=int(ticket))
            if entry_deals is None:
                raise ValueError("DEAL_HISTORY_UNAVAILABLE")
            if any(d.order != ticket or d.symbol != row["symbol"]
                   or d.magic != payload["magic"] for d in entry_deals):
                raise ValueError("ORDER_DEAL_IDENTITY_MISMATCH")
            for position_id in {d.position_id for d in entry_deals if d.position_id}:
                deals = mt5.history_deals_get(position=int(position_id))
                if deals is None or not deals:
                    raise ValueError("POSITION_HISTORY_UNAVAILABLE")
                if any(d.position_id != position_id or d.symbol != row["symbol"] for d in deals):
                    raise ValueError("POSITION_DEAL_IDENTITY_MISMATCH")
                with db:
                    _store_deals(db, scope, deals)
                # Netting/add-ons need allocation, never assign the whole PNL to each entry.
                if any(d.entry == 2 or (d.entry == 0 and d.order != ticket) for d in deals):
                    issues.append("POSITION_ALLOCATION_REQUIRED")
                    with db:
                        db.execute("UPDATE intents SET state='UNKNOWN' WHERE id=?", (row["id"],))
                        if row["state"] not in UNCERTAIN:
                            _event(db, row["id"], "POSITION_ALLOCATION_REQUIRED", {})
            observed += 1
        # Advance only after a successful batch: unavailable history cannot be skipped.
        _reconcile_cursor[scope] = start_index + len(batch)
    return {"status": "WAIT" if unresolved or issues else "OK", "unresolved": unresolved,
            "observed_intents": observed, "issues": sorted(set(issues))}
