"""Command Center : journal commun, agrégat et surface HTTP bornée."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from titanium import collab_tasks
from titanium.collab_tasks import TaskError
from titanium.web import command_center


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def task_path(tmp_path, monkeypatch):
    monkeypatch.setattr(collab_tasks, "RACINE", tmp_path)
    return tmp_path / "collab" / "tasks.ndjson"


def test_journal_append_only_rejoue_les_transitions(task_path):
    task = collab_tasks.create_task({
        "title": "Mesurer le tunnel",
        "owner": "prime",
        "priority": "P0",
        "actor": "codex",
    }, path=task_path)
    size_before = task_path.stat().st_size

    updated = collab_tasks.update_task(task["id"], {
        "status": "in_progress", "actor": "prime", "note": "audit lancé",
    }, path=task_path)
    snap = collab_tasks.snapshot(task_path)

    assert task_path.stat().st_size > size_before
    assert len(task_path.read_text(encoding="utf-8").splitlines()) == 2
    assert updated["status"] == "in_progress"
    assert updated["updated_by"] == "prime"
    assert snap["counts"]["in_progress"] == 1
    assert snap["events"] == 2


def test_journal_refuse_secret_statut_et_chemin_hors_collab(task_path, tmp_path):
    with pytest.raises(TaskError, match="secret"):
        collab_tasks.create_task({
            "title": "API_KEY=super-secret-value-123456",
            "owner": "prime", "actor": "codex",
        }, path=task_path)
    with pytest.raises(TaskError, match="status"):
        task = collab_tasks.create_task({"title": "x", "status": "execute",
                                         "actor": "codex"}, path=task_path)
    with pytest.raises(TaskError, match="hors de collab"):
        collab_tasks.create_task({"title": "x", "actor": "codex"},
                                 path=tmp_path / "outside.ndjson")


def test_journal_signale_les_lignes_invalides_sans_reparer(task_path):
    task_path.parent.mkdir(parents=True)
    task_path.write_text("{invalide}\n", encoding="utf-8")
    before = task_path.read_bytes()
    snap = collab_tasks.snapshot(task_path)
    assert snap["invalid_lines"] == 1
    assert task_path.read_bytes() == before


def test_command_center_agrege_sans_exposer_commandline(monkeypatch, tmp_path):
    monkeypatch.setattr(command_center, "BUS_STREAM", tmp_path / "stream.ndjson")
    monkeypatch.setattr(command_center, "BUS_ACKS", tmp_path / "acks.ndjson")
    monkeypatch.setattr(command_center, "PRIME_RUNS", tmp_path / "runs")
    monkeypatch.setattr(command_center, "_port_open", lambda port: port == 8766)
    monkeypatch.setattr(command_center, "_processes", lambda: {
        "prime_clients": 1, "prime_daemons": 1, "claude_clients": 0,
        "codex_clients": 1, "models": ["claude-opus-5"],
    })
    monkeypatch.setattr("tools.etat_services.racines", lambda: {
        "live_demo": [10], "dashboard": [11], "analystes": [12],
    })
    monkeypatch.setattr("titanium.web.state.promotion", lambda: {"cells": []})
    monkeypatch.setattr(command_center, "tasks_snapshot", lambda: {
        "tasks": [], "counts": {}, "events": 0, "invalid_lines": 0,
    })
    base = {
        "loop": {"running": True, "armed": True, "stats": {
            "tours": 2, "evalues": 8, "enter": 1, "envoyes": 1,
            "tunnel": {"flow": {"catalogue": 20, "portables": 8}},
        }},
        "edge": {"total_trades": 3, "rejected_lines": 0, "contexts": []},
        "tests": {"total": 100, "echecs": 0},
        "account": {"login": 123, "equity": 5000},
        "wall": {"safe": True},
    }

    result = command_center.snapshot(base)

    assert result["meta"]["mode"] == "DEMO_ONLY"
    assert result["safety"]["real_trading"] is False
    assert result["safety"]["task_auto_launch"] is False
    assert result["runtime"]["core"]["live_demo"]["running"] is True
    assert result["runtime"]["hermes_mcp"]["running"] is True
    assert result["runtime"]["collab_hub"]["running"] is False
    assert result["profitability"]["loop"]["sent"] == 1
    assert "CommandLine" not in json.dumps(result)


def test_bus_et_rapports_sont_bornes_et_masques(monkeypatch, tmp_path):
    stream = tmp_path / "stream.ndjson"
    stream.write_text(json.dumps({
        "id": "1", "from": "claude", "to": "codex", "task": "T",
        "ts_utc": "2026-01-01T00:00:00Z",
        "content": "API_KEY=top-secret-value-123456",
    }) + "\n", encoding="utf-8")
    runs = tmp_path / "runs" / "r1"; runs.mkdir(parents=True)
    (runs / "report.md").write_text("# Rapport sûr\n", encoding="utf-8")
    monkeypatch.setattr(command_center, "BUS_STREAM", stream)
    monkeypatch.setattr(command_center, "BUS_ACKS", tmp_path / "none.ndjson")
    monkeypatch.setattr(command_center, "PRIME_RUNS", tmp_path / "runs")

    messages = command_center.activity()
    reports = command_center.prime_reports()

    assert "top-secret" not in messages[0]["content"]
    assert "masque" in messages[0]["content"]
    assert reports[0]["run"] == "r1"


def test_surface_dashboard_n_expose_ni_shell_ni_lancement_agent():
    source = (ROOT / "tools" / "dashboard.py").read_text(encoding="utf-8")
    ui = (ROOT / "tools" / "ui" / "command.html").read_text(encoding="utf-8")
    script = (ROOT / "tools" / "ui" / "command.js").read_text(encoding="utf-8")
    prime = (ROOT / ".prime" / "agent" / "APPEND_SYSTEM.md").read_text(encoding="utf-8")

    assert 'chemin == "/command"' in source
    assert 'chemin == "/api/command-center"' in source
    assert 'route.path != "/api/tasks"' in source
    assert "Content-Security-Policy" in source
    assert "/api/tasks" in script
    assert "ne lance aucun agent" in ui
    assert "responsable technique principal" in prime
    assert "passer directement à `done`" in prime
    for forbidden in ("/api/shell", "/api/exec", "/api/order", "/api/restart", "order_send"):
        assert forbidden not in source


def test_processes_powershell_filtre_par_nom(monkeypatch):
    """La requete PowerShell ne demande que les processus surveilles."""
    import subprocess as _sp
    captured = {}

    class FakeResult:
        stdout = "[]"

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return FakeResult()

    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(command_center, "subprocess", _sp)

    command_center._processes()

    ps_command = captured["cmd"][-1] if captured.get("cmd") else ""
    # WQL Win32_Process n'accepte pas IN sur Windows : employer des OR.
    assert "Name='node.exe' OR" in ps_command
    for name in ("node.exe", "prime-agent.exe", "claude.exe", "codex.exe"):
        assert name in ps_command
    assert captured["cmd"][:3] == ["powershell", "-NoProfile", "-Command"]
    assert captured["kw"]["check"] is True
    # Le filtrage est effectue par WMI, pas apres enumeration de toute la machine.
    assert "Where-Object" not in ps_command


def test_processes_ne_publie_pas_commandline_dans_resultat():
    """Le resultat _processes() ne contient jamais la cle CommandLine."""
    result = command_center._processes()
    import json as _json
    serialized = _json.dumps(result)
    assert "CommandLine" not in serialized
