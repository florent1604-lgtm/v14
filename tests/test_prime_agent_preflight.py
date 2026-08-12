from __future__ import annotations

import json
from pathlib import Path

from tools.prime_agent_preflight import (
    owner_is_alive,
    quarantine_stale_leases,
    quarantine_stale_workers,
    select_reusable_session,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_owner_exige_pid_et_identite_de_demarrage() -> None:
    owner = {"pid": 42, "processStartId": "win:123"}
    assert owner_is_alive(owner, lambda pid: "win:123" if pid == 42 else None)
    assert not owner_is_alive(owner, lambda _pid: None)
    assert not owner_is_alive(owner, lambda _pid: "win:999")


def test_lease_mort_est_mis_en_quarantaine(tmp_path: Path) -> None:
    lease = tmp_path / "session-leases" / "abc.lock"
    _write_json(
        lease / "owner.json",
        {"pid": 12, "processStartId": "win:old", "activeSessionId": "dead"},
    )
    moved = quarantine_stale_leases(tmp_path, lookup=lambda _pid: None, stamp="STAMP")
    assert len(moved) == 1
    assert not lease.exists()
    assert (tmp_path / "quarantine" / "STAMP" / "session-leases" / "abc.lock").is_dir()


def test_lease_vivant_est_preserve(tmp_path: Path) -> None:
    lease = tmp_path / "session-leases" / "abc.lock"
    _write_json(lease / "owner.json", {"pid": 12, "processStartId": "win:same"})
    moved = quarantine_stale_leases(
        tmp_path, lookup=lambda _pid: "win:same", stamp="STAMP"
    )
    assert moved == []
    assert lease.is_dir()


def test_pid_reutilise_ne_protege_pas_un_vieux_lease(tmp_path: Path) -> None:
    lease = tmp_path / "session-leases" / "abc.lock"
    _write_json(lease / "owner.json", {"pid": 12, "processStartId": "win:old"})
    moved = quarantine_stale_leases(
        tmp_path, lookup=lambda _pid: "win:new", stamp="STAMP"
    )
    assert len(moved) == 1
    assert not lease.exists()


def test_lease_illisible_reste_en_place_fail_closed(tmp_path: Path) -> None:
    lease = tmp_path / "session-leases" / "abc.lock"
    lease.mkdir(parents=True)
    (lease / "owner.json").write_text("{invalide", encoding="utf-8")
    assert quarantine_stale_leases(tmp_path, stamp="STAMP") == []
    assert lease.is_dir()


def test_worker_mort_et_journal_sont_quarantaines(tmp_path: Path) -> None:
    root = tmp_path / "daemon-workers" / "machine"
    _write_json(
        root / "worker1.json",
        {"workerId": "worker1", "pid": 77, "processStartId": "win:dead"},
    )
    (root / "worker1.recovery.jsonl").write_text("preuve", encoding="utf-8")
    _write_json(
        root / "worker2.json",
        {"workerId": "worker2", "pid": 88, "processStartId": "win:live"},
    )
    moved = quarantine_stale_workers(
        tmp_path,
        lookup=lambda pid: "win:live" if pid == 88 else None,
        stamp="STAMP",
    )
    assert {source.name for source, _target in moved} == {
        "worker1.json",
        "worker1.recovery.jsonl",
    }
    assert (root / "worker2.json").is_file()


def test_ancienne_quarantaine_worker_est_ignoree(tmp_path: Path) -> None:
    root = tmp_path / "daemon-workers" / "machine.stale-20260812"
    _write_json(
        root / "worker1.json",
        {"workerId": "worker1", "pid": 77, "processStartId": "win:dead"},
    )
    assert quarantine_stale_workers(
        tmp_path, lookup=lambda _pid: None, stamp="STAMP"
    ) == []
    assert (root / "worker1.json").is_file()


def test_selection_reutilise_uniquement_worker_sain_de_meme_racine(tmp_path: Path) -> None:
    payload = {
        "sessions": [
            {
                "id": "deadbeef",
                "cwd": str(tmp_path),
                "lifecycle": "live",
                "isSessionActive": True,
                "workerState": "failed",
                "lastActivityAt": "2026-08-12T10:00:00Z",
            },
            {
                "id": "cafebabe",
                "cwd": str(tmp_path),
                "lifecycle": "live",
                "isSessionActive": False,
                "workerState": "ready",
                "taskState": "needs_input",
                "lastActivityAt": "2026-08-12T09:00:00Z",
            },
            {
                "id": "01234567",
                "cwd": str(tmp_path / "autre"),
                "lifecycle": "live",
                "isSessionActive": True,
                "workerState": "ready",
                "lastActivityAt": "2026-08-12T11:00:00Z",
            },
        ]
    }
    assert select_reusable_session(payload, tmp_path) == "cafebabe"
