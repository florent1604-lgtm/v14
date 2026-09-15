"""Journal append-only des décisions d'entrée, résolues ou encore ouvertes."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_event_ids_by_path: dict[Path, tuple[tuple[int, int] | None, set[str]]] = {}
_event_ids_lock = threading.Lock()


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _load_event_ids(path: Path) -> set[str]:
    event_ids: set[str] = set()
    if not path.exists():
        return event_ids
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            event_id = json.loads(raw).get("event_id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if event_id:
            event_ids.add(str(event_id))
    return event_ids


def _cached_event_ids(path: Path) -> set[str]:
    signature = _signature(path)
    cached = _event_ids_by_path.get(path)
    if cached is None or cached[0] != signature:
        event_ids = _load_event_ids(path)
        _event_ids_by_path[path] = (signature, event_ids)
        return event_ids
    return cached[1]


def prepare_decision_registry(path: Path) -> tuple[bool, str]:
    """Charge l'index de déduplication avant le premier envoi courtier."""
    try:
        resolved = Path(path).resolve(strict=False)
        with _event_ids_lock:
            _cached_event_ids(resolved)
        return True, "READY"
    except Exception as exc:  # noqa: BLE001 - télémétrie fail-soft
        return False, f"ERROR_{type(exc).__name__.upper()}"


def make_decision_id(policy_epoch: str, ticket: int | str) -> str:
    epoch = str(policy_epoch or "unsealed").strip() or "unsealed"
    normalized_ticket = str(ticket).strip()
    if not normalized_ticket:
        raise ValueError("ticket absent")
    return f"{epoch}:{normalized_ticket}"


def append_decision_event(path: Path, event: dict) -> tuple[bool, str]:
    """Ajoute une transition idempotente sans jamais lever dans le moteur."""
    try:
        decision_id = str(event.get("decision_id", "") or "").strip()
        event_name = str(event.get("event", "") or "").strip().lower()
        if not decision_id or event_name not in {"decided", "resolved"}:
            return False, "EVENT_INVALIDE"
        event_id = f"{decision_id}:{event_name}"
        path = Path(path).resolve(strict=False)
        with _event_ids_lock:
            event_ids = _cached_event_ids(path)
            if event_id in event_ids:
                return False, "DUPLICATE"
            record = dict(event)
            record.update({
                "event": event_name,
                "event_id": event_id,
                "decision_id": decision_id,
                "at": str(event.get("at") or datetime.now(timezone.utc).isoformat()),
            })
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            event_ids.add(event_id)
            _event_ids_by_path[path] = (_signature(path), event_ids)
        return True, "WRITTEN"
    except Exception as exc:  # noqa: BLE001 - la télémétrie ne casse pas l'ordre
        return False, f"ERROR_{type(exc).__name__.upper()}"
