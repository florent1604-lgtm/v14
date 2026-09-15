from __future__ import annotations

import json

from titanium.execution.decision_registry import (
    append_decision_event,
    make_decision_id,
    prepare_decision_registry,
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


def test_registry_charge_l_index_une_fois_avant_les_appends(monkeypatch, tmp_path):
    path = tmp_path / "decisions.ndjson"
    path.write_text(
        json.dumps({
            "event": "decided", "decision_id": "epoch-a:1",
            "event_id": "epoch-a:1:decided",
        }) + "\n",
        encoding="utf-8",
    )
    assert prepare_decision_registry(path) == (True, "READY")

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("le journal ne doit pas être relu après amorçage")

    monkeypatch.setattr(type(path), "read_text", unexpected_read)
    assert append_decision_event(path, {
        "event": "resolved", "decision_id": "epoch-a:1", "pnl_r": 1.0,
    }) == (True, "WRITTEN")
    assert append_decision_event(path, {
        "event": "resolved", "decision_id": "epoch-a:1", "pnl_r": 1.0,
    }) == (False, "DUPLICATE")
