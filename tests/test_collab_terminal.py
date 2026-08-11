"""Tests du V14 Collab Terminal v2."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.collab_terminal.server import (
    _compute_task_scores,
    _agent_performance,
    _validation_matrix,
    _read_chat,
    _append_chat,
    _redact,
    api_full_state,
    dispatch_message,
    AGENTS,
)


class TestRedact:
    def test_mask_api_key(self):
        assert "[masque]" in _redact("my api_key=sk-proj-abc123def456ghij")

    def test_clean_text(self):
        assert _redact("hello world") == "hello world"


class TestScoring:
    @pytest.fixture
    def sample_tasks(self):
        return [
            {"title": "T1", "owner": "prime", "status": "done", "priority": "P0",
             "updated_by": "codex", "note": "test passed"},
            {"title": "T2", "owner": "claude", "status": "in_progress", "priority": "P1",
             "updated_by": "claude", "note": ""},
            {"title": "T3", "owner": "hermes", "status": "backlog", "priority": "P2",
             "updated_by": "", "note": ""},
        ]

    def test_scores_are_bounded(self, sample_tasks):
        scored = _compute_task_scores(sample_tasks)
        for t in scored:
            assert 0 <= t["score"] <= 100

    def test_done_scores_higher(self, sample_tasks):
        scored = _compute_task_scores(sample_tasks)
        done_score = next(t["score"] for t in scored if t["title"] == "T1")
        backlog_score = next(t["score"] for t in scored if t["title"] == "T3")
        assert done_score > backlog_score

    def test_proof_bonus(self, sample_tasks):
        scored = _compute_task_scores(sample_tasks)
        t1 = next(t for t in scored if t["title"] == "T1")
        assert "preuve documentee" in t1["score_factors"]

    def test_validators_tracked(self, sample_tasks):
        scored = _compute_task_scores(sample_tasks)
        t1 = next(t for t in scored if t["title"] == "T1")
        assert "codex" in t1["validators"]


class TestPerformance:
    def test_completion_rate(self):
        tasks = [
            {"owner": "prime", "status": "done", "score": 90},
            {"owner": "prime", "status": "in_progress", "score": 60},
        ]
        perf = _agent_performance(tasks)
        assert perf["prime"]["completion_rate"] == 50.0
        assert perf["prime"]["total_tasks"] == 2

    def test_zero_tasks(self):
        perf = _agent_performance([])
        assert perf["prime"]["total_tasks"] == 0
        assert perf["prime"]["avg_score"] == 0


class TestValidationMatrix:
    def test_cross_validation(self):
        tasks = [
            {"owner": "prime", "updated_by": "codex"},
            {"owner": "prime", "updated_by": "claude"},
            {"owner": "claude", "updated_by": "prime"},
        ]
        m = _validation_matrix(tasks)
        assert m["prime"]["codex"] == 1
        assert m["prime"]["claude"] == 1
        assert m["claude"]["prime"] == 1
        assert m["prime"]["prime"] == 0


class TestChat:
    def test_append_and_read(self, tmp_path, monkeypatch):
        log = tmp_path / "test_chat.ndjson"
        monkeypatch.setattr("tools.collab_terminal.server.COLLAB_LOG", log)
        msg = _append_chat({"from": "prime", "content": "hello"})
        assert msg["from"] == "prime"
        assert "id" in msg

        msgs = _read_chat(10)
        assert len(msgs) >= 1

    def test_redact_in_chat(self, tmp_path, monkeypatch):
        log = tmp_path / "test_chat2.ndjson"
        monkeypatch.setattr("tools.collab_terminal.server.COLLAB_LOG", log)
        msg = _append_chat({"from": "prime", "content": "key=sk-proj-abcdef1234567890"})
        assert "sk-proj" not in msg["content"]


class TestDispatch:
    def test_dispatch_returns_routes(self, tmp_path, monkeypatch):
        log = tmp_path / "test_dispatch.ndjson"
        monkeypatch.setattr("tools.collab_terminal.server.COLLAB_LOG", log)
        monkeypatch.setattr("tools.collab_terminal.server._port_open", lambda port: False)
        monkeypatch.setattr("tools.collab_terminal.server.bus_write", lambda *args, **kwargs: {})
        result = dispatch_message("florent", "all", "test dispatch")
        assert "message" in result
        assert "routes" in result
        assert isinstance(result["routes"], list)
        # At minimum bus should be there if stream exists
        assert result["message"]["from"] == "florent"

    def test_dispatch_chat_recorded(self, tmp_path, monkeypatch):
        log = tmp_path / "test_dispatch2.ndjson"
        monkeypatch.setattr("tools.collab_terminal.server.COLLAB_LOG", log)
        monkeypatch.setattr("tools.collab_terminal.server._port_open", lambda port: False)
        monkeypatch.setattr("tools.collab_terminal.server.bus_write", lambda *args, **kwargs: {})
        dispatch_message("prime", "claude", "hello claude")
        msgs = _read_chat(10)
        assert any(m.get("content") == "hello claude" for m in msgs)

    def test_dispatch_to_prime_uses_coordination_inbox(self, tmp_path, monkeypatch):
        log = tmp_path / "test_dispatch_prime.ndjson"
        calls = []
        monkeypatch.setattr("tools.collab_terminal.server.COLLAB_LOG", log)
        monkeypatch.setattr("tools.collab_terminal.server._port_open", lambda port: port == 8770)
        monkeypatch.setattr("tools.collab_terminal.server.bus_write", lambda *args, **kwargs: {})

        def fake_publish(principal, target, kind, content):
            calls.append((principal, target, kind, content))
            return {"result": {"isError": False}}

        monkeypatch.setattr("tools.collab_terminal.server.hub_publish", fake_publish)
        result = dispatch_message("codex", "prime", "coordination V14")

        assert calls == [("codex", "florent", "status", "[Terminal][Pour Prime] coordination V14")]
        assert "hub->prime(via florent)" in result["routes"]


class TestAPIState:
    def test_full_state_structure(self):
        state = api_full_state()
        required_keys = ["terminal", "tasks", "performance", "validation_matrix",
                         "services", "chat", "agents", "heartbeat", "hub_health",
                         "hub_activity", "bus_activity", "prime_reports"]
        for key in required_keys:
            assert key in state, f"Missing key: {key}"

    def test_version_2(self):
        state = api_full_state()
        assert state["version"] == "2.1.0"

    def test_agents_complete(self):
        state = api_full_state()
        for agent in ("prime", "claude", "codex", "hermes", "copilot", "florent"):
            assert agent in state["agents"]

    def test_presence_in_state(self):
        state = api_full_state()
        assert "presence" in state
        for agent in ("prime", "claude", "codex", "hermes", "copilot", "florent"):
            assert agent in state["presence"]
            assert "online" in state["presence"][agent]
            assert "channel" in state["presence"][agent]

    def test_tasks_scored(self):
        state = api_full_state()
        for t in state["tasks"]["items"]:
            assert "score" in t
            assert "score_factors" in t
            assert "validators" in t
