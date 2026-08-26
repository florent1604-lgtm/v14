"""Journal append-only des décisions d'entrée, résolues ou encore ouvertes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


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
        path = Path(path)
        if path.exists():
            for raw in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(raw).get("event_id") == event_id:
                        return False, "DUPLICATE"
                except (json.JSONDecodeError, AttributeError):
                    continue
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
        return True, "WRITTEN"
    except Exception as exc:  # noqa: BLE001 - la télémétrie ne casse pas l'ordre
        return False, f"ERROR_{type(exc).__name__.upper()}"
