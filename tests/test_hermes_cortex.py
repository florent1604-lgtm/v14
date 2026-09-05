from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import titanium.hermes_cortex as cortex
from titanium.fundamental_intelligence import Evidence


@pytest.fixture(autouse=True)
def reset_circuit():
    cortex._CIRCUIT.update(retry_at=0.0, error="")


def test_hermes_entry_is_strictly_bound_and_has_no_execution_tools(monkeypatch):
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        captured["prompt"] = command[command.index("-z") + 1]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"verdicts": [{
                "decision_ref": "d1", "action": "ALLOW",
                "confidence": 0.81, "summary": "contexte coherent",
            }]}),
            stderr="",
        )

    monkeypatch.setattr(cortex, "_hermes_executable", lambda: cortex.Path("hermes.exe"))
    monkeypatch.setattr(cortex.subprocess, "run", fake_run)
    monkeypatch.setattr(cortex, "collect", lambda _symbol: [
        Evidence("FRED", "macro stable", "2026-09-05"),
        Evidence("CoinGecko", "variation neutre", "2026-09-05"),
    ])
    result = cortex.analyse_entries([{
        "decision_ref": "d1", "symbol": "BTCUSD", "side": 1,
        "mechanical_summary": "4/5 piliers", "model_version": "m",
        "prompt_version": "p",
    }])[0]

    assert result["action"] == "ALLOW"
    assert result["source"] == cortex.HERMES_SOURCE
    assert captured["command"][captured["command"].index("-t") + 1] == "todo"
    assert "--ignore-rules" in captured["command"]
    assert "terminal" not in captured["command"]
    assert "MT5 DEMO uniquement" in captured["prompt"]


def test_hermes_position_batch_returns_every_ticket(monkeypatch):
    monkeypatch.setattr(cortex, "collect", lambda _symbol: [])
    monkeypatch.setattr(cortex, "_ask", lambda _prompt: {"verdicts": [
        {"request_ref": "r1", "state": "CALM", "confidence": 0.7,
         "reason": "these intacte"},
        {"request_ref": "r2", "state": "CAUTION", "confidence": 0.6,
         "reason": "impulsion faiblit"},
    ]})
    rows = cortex.analyse_positions([
        {"request_ref": "r1", "ticket": "1", "symbol": "BTCUSD", "side": 1},
        {"request_ref": "r2", "ticket": "2", "symbol": "ETHUSD", "side": -1},
    ])
    assert [(row["ticket"], row["state"]) for row in rows] == [
        ("1", "CALM"), ("2", "CAUTION"),
    ]


def test_hermes_rejects_an_unbound_answer(monkeypatch):
    monkeypatch.setattr(cortex, "collect", lambda _symbol: [])
    monkeypatch.setattr(cortex, "_ask", lambda _prompt: {
        "verdicts": [{"decision_ref": "wrong", "action": "ALLOW"}],
    })
    with pytest.raises(cortex.HermesCortexUnavailable, match="decision_ref"):
        cortex.analyse_entries([{"decision_ref": "expected", "symbol": "BTCUSD"}])
