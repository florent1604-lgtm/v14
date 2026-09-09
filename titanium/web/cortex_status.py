"""Etat observable du cortex et de la memoire d'admission.

Cette sonde ne lance aucun LLM et n'ecrit aucun fichier. Elle transforme les
journaux append-only existants en un resume borne pour le poste de controle.
"""

from __future__ import annotations

import json
import re
import socket
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
HERMES_SOURCE = "hermes-cortex/qwen3.5:2b"

_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*\S+",
    ),
)
_QUOTA_MARKERS = (
    "credit balance is too low",
    "out of extra usage",
    "usage exhausted",
    "quota exhausted",
    "provider usage/quota refusal",
    "more credits",
)


def _safe_text(value: object, maximum: int = 240) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[masque]", text)
    return text[:maximum]


def _instant(value: object) -> datetime | None:
    try:
        text = str(value or "").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _tail_json(path: Path, *, maximum_bytes: int = 2_000_000) -> list[dict]:
    """Lit seulement la fin d'un NDJSON qui peut atteindre plusieurs Go."""
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - maximum_bytes)
            stream.seek(max(0, start - 1))
            partial = start > 0 and stream.read(1) != b"\n"
            stream.seek(start)
            raw = stream.read(maximum_bytes)
    except OSError:
        return []
    if partial:
        newline = raw.find(b"\n")
        raw = b"" if newline < 0 else raw[newline + 1:]
    rows: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _recent(rows: list[dict], *, since: datetime, until: datetime,
            time_fields: tuple[str, ...]) -> list[dict]:
    out = []
    for row in rows:
        instant = next((_instant(row.get(field)) for field in time_fields
                        if row.get(field)), None)
        if instant is not None and since <= instant <= until:
            out.append(row)
    return out


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.12):
            return True
    except OSError:
        return False


def _refusal_category(detail: str) -> str:
    value = detail.upper()
    if value.startswith("MEMOIRE BLOCK"):
        return "memory_block"
    if value.startswith("MEMOIRE WAIT"):
        return "memory_wait"
    if "CORTEX_POLICY_MODEL_INVALID" in value:
        return "policy_model_invalid"
    if "CORTEX_POLICY_PRODUCER_INVALID" in value:
        return "policy_producer_invalid"
    if "CORTEX_POLICY_MISSING" in value:
        return "policy_missing"
    if "CORTEX_POLICY_STALE" in value or "CORTEX_CONTEXT" in value:
        return "policy_stale"
    if value.startswith("FONDAMENTAL BLOCK"):
        return "fundamental_block"
    if value.startswith("FONDAMENTAL WAIT"):
        return "fundamental_wait"
    return "other"


def _cortex_health(avis: list[dict]) -> tuple[str, str, dict]:
    provider_rows = [
        row for row in avis
        if row.get("source") in {HERMES_SOURCE, "hermes-unavailable"}
    ]
    last = provider_rows[-1] if provider_rows else {}
    summary = _safe_text(last.get("resume"))
    lowered = summary.lower()
    source = str(last.get("source") or "")
    if source == HERMES_SOURCE:
        status = "ready"
        label = "Hermes disponible"
    elif any(marker in lowered for marker in _QUOTA_MARKERS):
        # Le message de credit/quota ne prouve ni le compte utilise ni une
        # saturation de l'abonnement (une cle API heritee peut le produire).
        status = "provider_refused"
        label = "Refus du fournisseur Hermes"
    elif "circuit hermes ouvert" in lowered:
        status = "circuit_open"
        label = "Circuit Hermes en attente"
    elif provider_rows:
        status = "unavailable"
        label = "Hermes indisponible"
    else:
        status = "unknown"
        label = "Hermes non mesure"
    public_last = {
        "at": last.get("rendu_a", ""),
        "symbol": _safe_text(last.get("symbol"), 40),
        "action": _safe_text(last.get("action") or last.get("rating"), 20),
        "source": _safe_text(source, 80),
        "summary": summary,
    } if last else {}
    return status, label, public_last


def snapshot(*, root: Path = RACINE, now: datetime | None = None,
             window_minutes: int = 60) -> dict:
    """Resume cortex/memoire recent, sans appel reseau externe ni LLM."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    minutes = max(5, min(24 * 60, int(window_minutes)))
    since = current - timedelta(minutes=minutes)
    results = Path(root) / "results"

    avis_all = _tail_json(results / "avis_rendus.ndjson")
    avis = _recent(avis_all, since=since, until=current, time_fields=("rendu_a", "at"))
    refus = _recent(
        _tail_json(results / "refus_live.ndjson"),
        since=since,
        until=current,
        time_fields=("at",),
    )
    memory = _recent(
        _tail_json(results / "live_memory.ndjson"),
        since=since,
        until=current,
        time_fields=("at",),
    )

    actions = Counter(str(row.get("action") or "UNKNOWN").upper()
                      for row in memory)
    latest_by_context: dict[tuple[str, str], dict] = {}
    for row in memory:
        latest_by_context[(str(row.get("symbol") or ""),
                           str(row.get("context") or ""))] = row
    unique_actions = Counter(
        str(row.get("action") or "UNKNOWN").upper()
        for row in latest_by_context.values()
    )
    memory_total = sum(actions.values())
    memory_reasons = Counter(_safe_text(row.get("reason"), 120) for row in memory)

    intelligence = [row for row in refus if row.get("code") == "INTELLIGENCE_GATE"]
    categories = Counter(
        _refusal_category(str(row.get("detail") or ""))
        for row in intelligence
    )
    top_refusals = Counter(
        _safe_text(row.get("detail"), 160) for row in intelligence
    )

    status, label, last_result = _cortex_health(avis)
    return {
        "at": current.isoformat(),
        "window_minutes": minutes,
        "status": status,
        "label": label,
        "fail_closed": True,
        "last_result": last_result,
        "memory": {
            "checks": memory_total,
            "allow": actions.get("ALLOW", 0),
            "block": actions.get("BLOCK", 0),
            "wait": actions.get("WAIT", 0),
            "allow_rate": round(actions.get("ALLOW", 0) / memory_total, 4)
            if memory_total else None,
            "block_rate": round(actions.get("BLOCK", 0) / memory_total, 4)
            if memory_total else None,
            "unique_contexts": len(latest_by_context),
            "unique_allow": unique_actions.get("ALLOW", 0),
            "unique_block": unique_actions.get("BLOCK", 0),
            "unique_wait": unique_actions.get("WAIT", 0),
            "top_reasons": [
                {"reason": reason, "count": count}
                for reason, count in memory_reasons.most_common(5)
            ],
        },
        "refusals": {
            "intelligence_gate": len(intelligence),
            "categories": dict(categories),
            "top": [
                {"reason": reason, "count": count}
                for reason, count in top_refusals.most_common(5)
            ],
        },
        "communication": {
            "terminal": {"port": 8097, "running": _port_open(8097)},
            "hub": {"port": 8770, "running": _port_open(8770)},
            "hermes_mcp": {"port": 8766, "running": _port_open(8766)},
        },
    }
