"""Le journal de tâches doit résister à plusieurs écrivains Windows."""

from __future__ import annotations

import json
import multiprocessing
import os
import uuid
from pathlib import Path

from titanium import collab_tasks


def _writer(path_text: str, worker: int, count: int) -> None:
    path = Path(path_text)
    padding = "x" * 8192
    for index in range(count):
        collab_tasks._append({
            "event": "probe",
            "worker": worker,
            "index": index,
            "padding": padding,
        }, path)


def test_append_est_atomique_entre_processus_windows() -> None:
    """Chaque appel concurrent produit exactement une ligne JSON complète."""
    path = collab_tasks.RACINE / "collab" / f".test-tasks-{uuid.uuid4().hex}.ndjson"
    workers = 8
    count = 40
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_writer, args=(str(path), worker, count))
        for worker in range(workers)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == workers * count
        rows = [json.loads(line) for line in lines]
        observed = {(row["worker"], row["index"]) for row in rows}
        assert len(observed) == workers * count
    finally:
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".lock").unlink(missing_ok=True)


def test_append_termine_une_ecriture_systeme_partielle(monkeypatch) -> None:
    """Une écriture courte ne doit jamais laisser une ligne JSON déchirée."""
    path = collab_tasks.RACINE / "collab" / f".test-tasks-{uuid.uuid4().hex}.ndjson"
    real_write = os.write

    def short_write(fd: int, payload: bytes) -> int:
        return real_write(fd, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(collab_tasks.os, "write", short_write)
    event = {"event": "probe", "padding": "x" * 8192}
    try:
        collab_tasks._append(event, path)
        assert json.loads(path.read_text(encoding="utf-8")) == event
    finally:
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".lock").unlink(missing_ok=True)
