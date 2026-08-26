from __future__ import annotations

import json

from titanium.execution.decision_registry import (
    append_decision_event,
    make_decision_id,
)


def test_registry_est_append_only_et_idempotent(tmp_path):
    path = tmp_path / "decisions.ndjson"
    decision_id = make_decision_id("epoch-a", 123)
    event = {"event": "decided", "decision_id": decision_id, "symbol": "BTCUSD"}

    assert append_decision_event(path, event) == (True, "WRITTEN")
    assert append_decision_event(path, event) == (False, "DUPLICATE")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_id"] == f"{decision_id}:decided"


def test_registry_accepte_une_resolution_distincte(tmp_path):
    path = tmp_path / "decisions.ndjson"
    decision_id = make_decision_id("epoch-a", 123)
    assert append_decision_event(path, {
        "event": "decided", "decision_id": decision_id,
    })[0]
    assert append_decision_event(path, {
        "event": "resolved", "decision_id": decision_id, "pnl_r": 1.0,
    })[0]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
