from datetime import datetime, timedelta, timezone

import pytest

from titanium.organism.contracts import (
    CORTEX_DECISION_MODEL_VERSION,
    CORTEX_DECISION_PRODUCER,
    build_decision_identity,
)
from titanium.organism.cortex import build_cortex_policy, market_observed_at, policy_ttl_s
from titanium.organism.memory import CentralMemory

NOW = datetime(2026, 9, 6, 6, 0, tzinfo=timezone.utc)
CONTEXT = "BTCUSD|long|continuation|4p|tf=M1>H1"


def policy(identity, **overrides):
    kwargs = {"context_key": CONTEXT, "action": "ALLOW", "confidence": .8,
                  "summary": "test", "evidence_digest": "e" * 64, "now": NOW,
                  "decision_model_version": CORTEX_DECISION_MODEL_VERSION,
                  "producer": CORTEX_DECISION_PRODUCER}
    kwargs.update(overrides)
    return build_cortex_policy(identity, **kwargs)


def read(memory, identity, now=NOW):
    return memory.policy_for(identity, CONTEXT, now=now,
                             expected_decision_model=CORTEX_DECISION_MODEL_VERSION,
                             expected_producer=CORTEX_DECISION_PRODUCER)


@pytest.mark.parametrize("change,code", [
    ({"decision_model_version": "qwen3.5:2b"}, "CORTEX_POLICY_MODEL_INVALID"),
    ({"producer": "local-fallback"}, "CORTEX_POLICY_PRODUCER_INVALID"),
])
def test_only_hermes_can_authorize(tmp_path, change, code):
    identity = build_decision_identity({"symbol": "BTCUSD", "side": 1})
    memory = CentralMemory(tmp_path / "core.sqlite3")
    memory.record_policy(policy(identity, **change))
    assert read(memory, identity) == (None, code)


def test_expired_newer_block_never_resurrects_old_allow(tmp_path):
    identity = build_decision_identity({"symbol": "BTCUSD", "side": 1})
    memory = CentralMemory(tmp_path / "core.sqlite3")
    memory.record_policy(policy(identity, ttl_s=300))
    memory.record_policy(policy(identity, action="BLOCK", ttl_s=10,
                                now=NOW + timedelta(seconds=1)))
    assert read(memory, identity, NOW + timedelta(seconds=20)) == (
        None, "CORTEX_POLICY_STALE",
    )


def test_market_age_not_reset_when_old_bar_is_queued_again():
    observed = market_observed_at("2026-09-06T05:00:00Z", CONTEXT, NOW.isoformat())
    assert observed == NOW - timedelta(minutes=59)


def test_horizons_match_exactly():
    assert policy_ttl_s(CONTEXT) == 60
    assert policy_ttl_s(CONTEXT.replace("M1>", "M15>")) == 300
    assert policy_ttl_s(CONTEXT.replace("M1>", "M5>")) == 120
    with pytest.raises(ValueError):
        market_observed_at("2026-09-06T05:59:00Z", "BTCUSD|long", NOW.isoformat())


def test_worker_does_not_send_expired_requests_to_hermes(tmp_path, monkeypatch):
    from titanium.avis import Demande
    from tools import analystes

    monkeypatch.setattr(analystes, "CENTRAL_MEMORY", CentralMemory(tmp_path / "core.sqlite3"))
    monkeypatch.setattr("titanium.hermes_cortex.analyse_entries", lambda _: pytest.fail("stale"))
    result = analystes._traiter_lot([Demande(
        "BTCUSD", 1, bar_time="2020-01-01T00:00:00Z", engine_context=CONTEXT,
        demande_a=datetime.now(timezone.utc).isoformat(),
    )])
    assert result[0][1].action == "WAIT"
    assert result[0][1].model_version == "none"


def test_old_qwen_fear_cannot_close_position():
    from titanium.position_sentiment import confirm_fear

    verdict = {"request_ref": "r", "model_version": "qwen3.5:2b", "state": "FEAR",
                   "confidence": .99, "rendered_at": NOW.isoformat()}
    assert not confirm_fear(verdict, last_ref="old", previous_streak=1, now=NOW).should_exit


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -1, 2])
def test_invalid_hermes_confidence_rejected(monkeypatch, confidence):
    from titanium import hermes_cortex as cortex

    monkeypatch.setattr(cortex, "collect", lambda _: [])
    monkeypatch.setattr(cortex, "_ask", lambda _: {"verdicts": [
        {"decision_ref": "a", "action": "ALLOW", "confidence": confidence},
    ]})
    with pytest.raises(cortex.HermesCortexUnavailable, match="confiance"):
        cortex.analyse_entries([{"decision_ref": "a", "symbol": "BTCUSD", "side": 1}])


def test_opposite_hermes_allow_is_wait(monkeypatch):
    from titanium import hermes_cortex as cortex

    monkeypatch.setattr(cortex, "collect", lambda _: [])
    monkeypatch.setattr(cortex, "_ask", lambda _: {"verdicts": [
        {"decision_ref": ref, "action": "ALLOW", "confidence": .8} for ref in ("a", "b")
    ]})
    results = cortex.analyse_entries([
        {"decision_ref": "a", "symbol": "BTCUSD", "side": 1},
        {"decision_ref": "b", "symbol": "BTCUSD", "side": -1},
    ])
    assert [item["action"] for item in results] == ["WAIT", "WAIT"]


def test_duplicate_reply_rejected(monkeypatch):
    from titanium import hermes_cortex as cortex

    monkeypatch.setattr(cortex, "collect", lambda _: [])
    monkeypatch.setattr(cortex, "_ask", lambda _: {"verdicts": [
        {"decision_ref": "a", "action": "ALLOW", "confidence": .8},
        {"decision_ref": "a", "action": "BLOCK", "confidence": .8},
    ]})
    with pytest.raises(cortex.HermesCortexUnavailable, match="decision_ref"):
        cortex.analyse_entries([{"decision_ref": "a", "symbol": "BTCUSD", "side": 1}])
