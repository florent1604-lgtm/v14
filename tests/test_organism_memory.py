from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from titanium.organism.contracts import build_decision_identity
from titanium.organism.cortex import EXECUTION_FIELDS, build_cortex_policy
from titanium.organism.memory import POLICY_FIELDS, PROPOSAL_FIELDS, CentralMemory


def _identity(symbol="XAUUSD", bar_time="2026-08-27T12:00:00+00:00"):
    return build_decision_identity({
        "symbol": symbol,
        "side": 1,
        "bar_time": bar_time,
        "verdict": "ENTER",
        "code": "ENTER_CONFLUENCE",
        "piliers": 3,
        "total_piliers": 4,
        "famille": "continuation",
        "engine_context": f"{symbol}|long|continuation|3p",
        "indicateurs": {"rsi": 55.0},
    })


def _proposal(identity, **changes):
    payload = {
        **identity.to_dict(),
        "evidence_digest": "e" * 64,
        "action": "ALLOW",
        "confidence": 0.72,
        "summary": "faits concordants",
        "sources": ["FRED:DGS10", "ECB"],
        "rendered_at": "2026-08-27T12:01:00+00:00",
    }
    payload.update(changes)
    return payload


def test_exact_proposal_round_trip(tmp_path):
    identity = _identity()
    memory = CentralMemory(tmp_path / "core.sqlite3")
    assert memory.record_request(identity, identity.to_dict())
    assert memory.record_proposal(identity, _proposal(identity))
    proposal, code = memory.proposal_for(identity)
    assert code == "BRAIN_PROPOSAL_EXACT"
    assert proposal["action"] == "ALLOW"


def test_previous_bar_is_stale_not_reused(tmp_path):
    memory = CentralMemory(tmp_path / "core.sqlite3")
    previous = _identity(bar_time="2026-08-27T11:45:00+00:00")
    current = _identity(bar_time="2026-08-27T12:00:00+00:00")
    memory.record_proposal(previous, _proposal(previous))
    proposal, code = memory.proposal_for(current)
    assert proposal is None
    assert code == "BRAIN_PROPOSAL_STALE"


def test_fresh_cortex_policy_reuses_only_same_context(tmp_path):
    memory = CentralMemory(tmp_path / "core.sqlite3")
    previous = _identity(bar_time="2026-08-27T11:45:00+00:00")
    current = _identity(bar_time="2026-08-27T12:00:00+00:00")
    now = datetime(2026, 8, 27, 11, 59, tzinfo=timezone.utc)
    policy = build_cortex_policy(
        previous,
        context_key="XAUUSD|long|continuation|3p",
        action="ALLOW",
        confidence=0.71,
        summary="regime compatible",
        evidence_digest="e" * 64,
        decision_model_version="hermes:claude-opus-5",
        now=now,
    )
    assert memory.record_policy(policy)
    result, code = memory.policy_for(
        current, "XAUUSD|long|continuation|3p", now=now + timedelta(seconds=30),
    )
    assert code == "CORTEX_POLICY_EXACT"
    assert result["source_decision_ref"] == previous.decision_ref
    assert result["decision_model_version"] == "hermes:claude-opus-5"
    assert memory.policy_for(current, "XAUUSD|long|reversal|3p", now=now)[0] is None


def test_cortex_policy_expires_and_has_no_execution_fields(tmp_path):
    memory = CentralMemory(tmp_path / "core.sqlite3")
    identity = _identity()
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    policy = build_cortex_policy(
        identity,
        context_key="XAUUSD|long|continuation|3p",
        action="BLOCK",
        confidence=0.9,
        summary="choc macro",
        evidence_digest="f" * 64,
        ttl_s=60,
        now=now,
    )
    memory.record_policy(policy)
    assert not (EXECUTION_FIELDS & POLICY_FIELDS)
    assert memory.policy_for(
        identity, policy.context_key, now=now + timedelta(seconds=61),
    )[1] == "CORTEX_POLICY_STALE"


def test_late_cortex_answer_does_not_refresh_old_observation(tmp_path):
    memory = CentralMemory(tmp_path / "core.sqlite3")
    identity = _identity()
    observed = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    completed = observed + timedelta(minutes=10)
    policy = build_cortex_policy(
        identity,
        context_key="XAUUSD|long|continuation|3p",
        action="ALLOW",
        confidence=0.8,
        summary="reponse tardive",
        evidence_digest="e" * 64,
        now=completed,
        source_observed_at=observed.isoformat(),
    )
    memory.record_policy(policy)
    assert memory.policy_for(identity, policy.context_key, now=completed)[1] == (
        "CORTEX_POLICY_STALE"
    )


def test_direct_policy_tampering_is_rejected(tmp_path):
    memory = CentralMemory(tmp_path / "core.sqlite3")
    identity = _identity()
    policy = build_cortex_policy(
        identity,
        context_key="XAUUSD|long|continuation|3p",
        action="ALLOW",
        confidence=0.8,
        summary="valide",
        evidence_digest="e" * 64,
    ).to_dict()
    policy["summary"] = "contenu remplace"
    memory.append("brain.policy", policy["policy_ref"], identity.symbol, policy)
    assert memory.policy_for(identity, policy["context_key"])[1] == (
        "CORTEX_POLICY_REF_INVALID"
    )


def test_identity_mismatch_and_unsealed_evidence_fail_closed(tmp_path):
    memory = CentralMemory(tmp_path / "core.sqlite3")
    identity = _identity()
    memory.append("brain.proposal", identity.decision_ref, identity.symbol,
                  _proposal(identity, context_digest="wrong"))
    assert memory.proposal_for(identity)[1] == "BRAIN_IDENTITY_MISMATCH"

    other = _identity(symbol="EURUSD")
    memory.record_proposal(other, _proposal(other, evidence_digest=""))
    assert memory.proposal_for(other)[1] == "BRAIN_EVIDENCE_UNSEALED"


def test_proposal_contract_forbids_execution_payload(tmp_path):
    memory = CentralMemory(tmp_path / "core.sqlite3")
    identity = _identity()
    with pytest.raises(ValueError):
        memory.record_proposal(identity, _proposal(identity, lot=1.0))
    assert not ({"price", "lot", "sl", "tp", "order_type"} & PROPOSAL_FIELDS)


def test_alert_is_idempotent_and_human_readable(tmp_path):
    alerts = tmp_path / "alerts.ndjson"
    memory = CentralMemory(tmp_path / "core.sqlite3", alerts)
    identity = _identity()
    memory.alert("BRAIN_PROPOSAL_STALE", identity, "ancienne barre")
    memory.alert("BRAIN_PROPOSAL_STALE", identity, "ancienne barre")
    rows = alerts.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["decision_ref"] == identity.decision_ref


def test_central_memory_has_no_execution_dependency():
    source = Path(CentralMemory.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).parents[1] / source).read_text(encoding="utf-8")
    for forbidden in ("MetaTrader5", "order_send", "positions_get"):
        assert forbidden not in text
