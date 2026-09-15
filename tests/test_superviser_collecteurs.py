"""La supervision ne transforme jamais une absence de preuve en ordre ou doublon."""

import json
import sys
from types import SimpleNamespace

import psutil
import pytest

from tools import superviser_collecteurs as s


@pytest.fixture
def environment(monkeypatch):
    inventory = {name: [] for name in s.NAMES}
    monkeypatch.setattr(s, "process_inventory", lambda: inventory)
    monkeypatch.setattr(s, "collection_health", lambda *a, **kw: {"status": "OK"})
    monkeypatch.setattr(s.shutil, "disk_usage", lambda _: SimpleNamespace(free=2 * 1024**3))
    started = []
    monkeypatch.setattr(s, "start_public", lambda _: started.append(99) or 99)
    return inventory, started


def test_observe_does_not_start(tmp_path, environment):
    report = s.supervise(state_dir=tmp_path, now=1000)
    assert report["action"] == "STOPPED_OBSERVE_ONLY"
    assert environment[1] == []
    assert not (tmp_path / "restarts.json").exists()


def test_restarts_bounded_and_persisted(tmp_path, environment):
    assert s.supervise(apply=True, state_dir=tmp_path, now=1000)["started_pid"] == 99
    assert s.supervise(apply=True, state_dir=tmp_path, now=1020)["action"] == "RESTART_BACKOFF"
    s.supervise(apply=True, state_dir=tmp_path, now=1060)
    s.supervise(apply=True, state_dir=tmp_path, now=1180)
    assert s.supervise(apply=True, state_dir=tmp_path, now=1300)["action"] == "RESTART_BUDGET_EXHAUSTED"
    assert len(environment[1]) == 3
    assert len((tmp_path / "events.ndjson").read_text().splitlines()) == 5


@pytest.mark.parametrize("pids,action", [([1], "RUNNING_NO_ACTION"), ([1, 2], "DUPLICATE_NO_ACTION")])
def test_running_or_duplicate_never_restarted(tmp_path, environment, pids, action):
    environment[0][s.PUBLIC] = pids
    report = s.supervise(apply=True, state_dir=tmp_path, now=1000)
    assert report["action"] == action
    assert not environment[1]


def test_stale_data_not_healthy_even_when_process_alive(tmp_path, environment, monkeypatch):
    environment[0][s.PUBLIC] = [1]
    monkeypatch.setattr(s, "collection_health", lambda *a, **kw: {"status": "WAIT"})
    report = s.supervise(apply=True, state_dir=tmp_path, now=1000)
    assert report["status"] == "WAIT"
    assert not environment[1]


def test_inventory_failure_blocks_launch(tmp_path, environment, monkeypatch):
    def fail():
        raise RuntimeError("unknown inventory")
    monkeypatch.setattr(s, "process_inventory", fail)
    assert s.supervise(apply=True, state_dir=tmp_path, now=1000)["action"] == "ERROR_NO_RETRY"
    assert not environment[1]


@pytest.mark.parametrize("value", ["broken", '{}', '{"schema":"other"}',
                                     '{"schema":"v14.collector-restarts.v1","attempts":[1001]}',
                                     '{"schema":"v14.collector-restarts.v1","attempts":[NaN]}'])
def test_corrupt_or_future_state_blocks_launch(tmp_path, environment, value):
    (tmp_path / "restarts.json").write_text(value)
    assert s.supervise(apply=True, state_dir=tmp_path, now=1000)["action"] == "ERROR_NO_RETRY"
    assert not environment[1]


def test_failed_spawn_still_consumes_budget(tmp_path, environment, monkeypatch):
    def fail(_):
        assert json.loads((tmp_path / "restarts.json").read_text())["attempts"] == [1000]
        raise OSError("cannot spawn")
    monkeypatch.setattr(s, "start_public", fail)
    assert s.supervise(apply=True, state_dir=tmp_path, now=1000)["action"] == "ERROR_NO_RETRY"
    assert s.supervise(apply=True, state_dir=tmp_path, now=1001)["action"] == "RESTART_BACKOFF"


def test_low_disk_blocks_start(tmp_path, environment, monkeypatch):
    monkeypatch.setattr(s.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    assert s.supervise(apply=True, state_dir=tmp_path, now=1000)["action"] == "LOW_DISK_NO_ACTION"
    assert not environment[1]


def test_archive_absence_is_explicit_not_auto_started(tmp_path, environment):
    environment[0][s.PUBLIC] = [1]
    report = s.supervise(apply=True, state_dir=tmp_path, now=1000)
    assert report["status"] == "OK_PUBLIC_ONLY"
    assert report["trading_control"] is False
    for archive in report["archive_collectors"].values():
        assert archive == {"process_count": 0, "data_quality": "NOT_CHECKED", "restart": "MANUAL_ONLY"}
    assert not environment[1]


def test_single_supervisor_writer(tmp_path, environment):
    with s.collector_lock(tmp_path), pytest.raises(OSError):
        s.supervise(apply=True, state_dir=tmp_path, now=1000)
    assert not environment[1]


def fake_process(pid, parent, cwd, script="tools/collecteur_microstructure.py"):
    return SimpleNamespace(pid=pid, ppid=lambda: parent, cwd=lambda: str(cwd),
                           name=lambda: "python.exe", cmdline=lambda: ["python.exe", script])


def test_inventory_collapses_relay_excludes_foreign_repo(tmp_path, monkeypatch):
    procs = [fake_process(1, 0, s.ROOT), fake_process(2, 1, s.ROOT),
             fake_process(3, 0, tmp_path), fake_process(4, 0, s.ROOT, "tools/live_demo.py")]
    monkeypatch.setattr(s.psutil, "process_iter", lambda: iter(procs))
    assert s.process_inventory()[s.PUBLIC] == [1]


def test_inventory_access_denied_is_not_empty(monkeypatch):
    def denied():
        raise psutil.AccessDenied(12)
    monkeypatch.setattr(s.psutil, "process_iter", lambda: iter([SimpleNamespace(name=denied)]))
    with pytest.raises(RuntimeError, match="INVENTORY"):
        s.process_inventory()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="le lanceur est fige sur la disposition Windows de la venv "
    "(.venv/Scripts/python.exe) et sur CREATE_NO_WINDOW ; V14 tourne sous Windows",
)
def test_public_launch_is_fixed_and_hidden(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **kw: captured.append((a, kw))
                        or SimpleNamespace(pid=44))
    assert s.start_public(tmp_path) == 44
    args, kwargs = captured[0]
    assert args[0][-1] == str(s.ROOT / "tools/collecteur_microstructure.py")
    assert len(args[0]) == 4
    assert kwargs["creationflags"] == getattr(s.subprocess, "CREATE_NO_WINDOW", 0)
    assert kwargs["cwd"] == s.ROOT


def test_restart_window_expires(tmp_path):
    path = tmp_path / "restarts.json"
    s.atomic_json(path, {"schema": "v14.collector-restarts.v1", "attempts": [1, 4000]})
    assert s.read_attempts(path, 4001) == [4000]
