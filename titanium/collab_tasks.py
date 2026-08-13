"""Journal local append-only des taches communes V14.

Le journal est volontairement distinct du bus de messages : un message est une
conversation, une tache est un etat rejouable. Le dashboard et les agents
utilisent exactement ce module, donc aucun statut parallele ne peut diverger.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


RACINE = Path(__file__).resolve().parent.parent
JOURNAL = RACINE / "collab" / "tasks.ndjson"

STATUTS = ("backlog", "in_progress", "review", "done", "blocked")
PRIORITES = ("P0", "P1", "P2", "P3")
RESPONSABLES = ("florent", "claude", "codex", "hermes", "prime", "team")
ACTEURS = set(RESPONSABLES) | {"dashboard", "system"}

_LOCK = threading.Lock()
_LOCK_TIMEOUT_S = 15.0
_LOCK_RETRY_S = 0.01
_SECRET = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[:=]\s*\S+",
        re.I,
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class TaskError(ValueError):
    """Evenement de tache refuse avant ecriture."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, field: str, *, maximum: int, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise TaskError(f"{field} manquant")
    if len(text) > maximum:
        raise TaskError(f"{field} trop long ({maximum} caracteres maximum)")
    if any(pattern.search(text) for pattern in _SECRET):
        raise TaskError(f"{field} refuse: secret potentiel")
    return text


def _choice(value, field: str, allowed: tuple[str, ...]) -> str:
    text = str(value or "").strip().lower()
    mapping = {item.lower(): item for item in allowed}
    if text not in mapping:
        raise TaskError(f"{field} invalide")
    return mapping[text]


def _read(path: Path) -> tuple[list[dict], int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], 0
    events: list[dict] = []
    invalid = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
            else:
                invalid += 1
        except (json.JSONDecodeError, UnicodeError):
            invalid += 1
    return events, invalid


def snapshot(path: Path = JOURNAL) -> dict:
    """Rejoue le journal et rend l'etat courant, sans jamais le reparer."""
    events, invalid = _read(path)
    tasks: dict[str, dict] = {}
    for event in events:
        kind = event.get("event")
        task_id = str(event.get("task_id") or "")
        if kind == "created" and task_id and isinstance(event.get("task"), dict):
            task = dict(event["task"])
            task.update({
                "id": task_id,
                "created_at": event.get("at"),
                "updated_at": event.get("at"),
                "updated_by": event.get("actor"),
                "note": "",
            })
            tasks.setdefault(task_id, task)
        elif kind == "updated" and task_id in tasks:
            changes = event.get("changes")
            if isinstance(changes, dict):
                for field in ("status", "priority", "owner", "title", "description"):
                    if field in changes:
                        tasks[task_id][field] = changes[field]
            tasks[task_id]["updated_at"] = event.get("at")
            tasks[task_id]["updated_by"] = event.get("actor")
            tasks[task_id]["note"] = event.get("note") or ""

    priority_order = {value: index for index, value in enumerate(PRIORITES)}
    status_order = {value: index for index, value in enumerate(STATUTS)}
    ordered = sorted(
        tasks.values(),
        key=lambda item: (
            status_order.get(item.get("status"), 99),
            priority_order.get(item.get("priority"), 99),
            str(item.get("updated_at") or ""),
        ),
    )
    counts = {status: 0 for status in STATUTS}
    for task in ordered:
        if task.get("status") in counts:
            counts[task["status"]] += 1
    return {
        "tasks": ordered,
        "counts": counts,
        "events": len(events),
        "invalid_lines": invalid,
        "journal": str(path.relative_to(RACINE)) if path.is_relative_to(RACINE) else path.name,
    }


@contextmanager
def _process_lock(path: Path):
    """Verrou exclusif inter-processus dédié à un journal.

    Windows ne garantit pas l'atomicité de ``O_APPEND`` entre processus. Un
    octet du fichier compagnon reste verrouillé pendant tout l'ajout. Sur POSIX,
    ``flock`` fournit le même contrat aux outils de développement hors Windows.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    if handle.read(1) == b"":
                        handle.seek(0)
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"verrou du journal indisponible: {lock_path.name}",
                    ) from exc
                time.sleep(_LOCK_RETRY_S)
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _append(event: dict, path: Path) -> None:
    resolved = path.resolve()
    allowed = (RACINE / "collab").resolve()
    if allowed not in resolved.parents:
        raise TaskError("journal hors de collab/")
    payload = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _process_lock(path):
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
            try:
                written = os.write(fd, payload)
                if written != len(payload):
                    raise OSError("ecriture partielle du journal de taches")
                os.fsync(fd)
            finally:
                os.close(fd)


def create_task(data: dict, *, path: Path = JOURNAL) -> dict:
    if not isinstance(data, dict):
        raise TaskError("objet JSON attendu")
    actor = _choice(data.get("actor", "dashboard"), "actor", tuple(sorted(ACTEURS)))
    task = {
        "title": _text(data.get("title"), "title", maximum=120, required=True),
        "description": _text(data.get("description"), "description", maximum=1200),
        "owner": _choice(data.get("owner", "team"), "owner", RESPONSABLES),
        "status": _choice(data.get("status", "backlog"), "status", STATUTS),
        "priority": _choice(data.get("priority", "P2"), "priority", PRIORITES),
        "source": _text(data.get("source", "dashboard"), "source", maximum=80),
    }
    task_id = str(uuid.uuid4())
    event = {"id": str(uuid.uuid4()), "event": "created", "task_id": task_id,
             "at": _now(), "actor": actor, "task": task}
    _append(event, path)
    return next(item for item in snapshot(path)["tasks"] if item["id"] == task_id)


def update_task(task_id: str, data: dict, *, path: Path = JOURNAL) -> dict:
    task_id = _text(task_id, "task_id", maximum=64, required=True)
    current = {item["id"]: item for item in snapshot(path)["tasks"]}
    if task_id not in current:
        raise TaskError("tache inconnue")
    if not isinstance(data, dict):
        raise TaskError("objet JSON attendu")
    actor = _choice(data.get("actor", "dashboard"), "actor", tuple(sorted(ACTEURS)))
    changes: dict = {}
    if "status" in data:
        changes["status"] = _choice(data["status"], "status", STATUTS)
    if "priority" in data:
        changes["priority"] = _choice(data["priority"], "priority", PRIORITES)
    if "owner" in data:
        changes["owner"] = _choice(data["owner"], "owner", RESPONSABLES)
    if "title" in data:
        changes["title"] = _text(data["title"], "title", maximum=120, required=True)
    if "description" in data:
        changes["description"] = _text(data["description"], "description", maximum=1200)
    note = _text(data.get("note"), "note", maximum=500)
    if not changes and not note:
        raise TaskError("aucun changement")
    event = {"id": str(uuid.uuid4()), "event": "updated", "task_id": task_id,
             "at": _now(), "actor": actor, "changes": changes, "note": note}
    _append(event, path)
    return next(item for item in snapshot(path)["tasks"] if item["id"] == task_id)
