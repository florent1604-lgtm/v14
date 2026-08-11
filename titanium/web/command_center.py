"""Agrégat local du V14 Command Center.

Lecture seule hors journal de tâches : aucune fonction de ce module ne lance
un agent, ne redémarre un service et ne touche à MT5 en écriture.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from titanium.collab_tasks import snapshot as tasks_snapshot


RACINE = Path(__file__).resolve().parent.parent.parent
V12 = Path(os.environ.get("TITANIUM_V12_ROOT", RACINE.parent / "v12"))
BUS_STREAM = V12 / "collab" / "messages" / "stream.ndjson"
BUS_ACKS = V12 / "collab" / "messages" / "acks.ndjson"
PRIME_RUNS = RACINE / "collab" / "prime_agent" / "runs"

_REDACTIONS = (
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"), "[secret masque]"),
    (re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*\S+"), r"\1=[masque]"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value, maximum: int = 240) -> str:
    text = " ".join(str(value or "").split())
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text[:maximum]


def _load_ndjson(path: Path, limit: int = 80) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                out.append(value)
        except json.JSONDecodeError:
            continue
    return out


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _processes() -> dict:
    """Compte les outils sans jamais publier leurs lignes de commande."""
    if os.name != "nt":
        return {"prime_clients": 0, "prime_daemons": 0, "claude_clients": 0,
                "codex_clients": 0, "models": []}
    names = (
        "Name='node.exe' OR Name='prime-agent.exe' OR "
        "Name='claude.exe' OR Name='codex.exe'"
    )
    command = (
        "Get-CimInstance Win32_Process -Filter "
        f"\"{names}\" | "
        "Select-Object Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
            check=True,
        ).stdout.strip()
        values = json.loads(raw) if raw else []
        if isinstance(values, dict):
            values = [values]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        values = []

    result = {"prime_clients": 0, "prime_daemons": 0, "claude_clients": 0,
              "codex_clients": 0, "models": []}
    models: set[str] = set()
    for value in values:
        executable = str(value.get("Name") or "").lower()
        line = str(value.get("CommandLine") or "")
        lower = line.lower()
        # Une recherche PowerShell contient elle-même le texte "prime-agent".
        # Ne compter que les vrais exécutables Prime/Node évite ces faux positifs.
        is_prime_process = (
            "prime-agent" in lower
            and (executable in {"node.exe", "node", "prime-agent.exe", "prime-agent"})
        )
        if is_prime_process:
            if "--mode daemon" in lower:
                result["prime_daemons"] += 1
            else:
                result["prime_clients"] += 1
            if match := re.search(r"--model\s+([A-Za-z0-9._:-]+)", line):
                models.add(match.group(1)[:80])
        if executable in {"claude.exe", "claude"}:
            result["claude_clients"] += 1
        if executable in {"codex.exe", "codex"}:
            result["codex_clients"] += 1
    result["models"] = sorted(models)
    return result


def services() -> dict:
    try:
        from tools.etat_services import racines
        roots = racines()
    except Exception:  # noqa: BLE001
        roots = {name: [] for name in ("live_demo", "dashboard", "analystes")}
    core = {
        name: {"running": len(pids) == 1, "instances": len(pids), "pid": pids[0] if len(pids) == 1 else None}
        for name, pids in roots.items()
    }
    return {
        "core": core,
        "collab_hub": {"running": _port_open(8770), "port": 8770},
        "hermes_mcp": {"running": _port_open(8766), "port": 8766},
        "processes": _processes(),
    }


def activity() -> list[dict]:
    messages = _load_ndjson(BUS_STREAM, 100) + _load_ndjson(BUS_ACKS, 100)
    messages.sort(key=lambda item: str(item.get("ts_utc") or ""), reverse=True)
    return [{
        "id": str(item.get("id") or "")[:80],
        "type": _redact(item.get("type"), 30),
        "from": _redact(item.get("from"), 30),
        "to": _redact(item.get("to"), 30),
        "task": _redact(item.get("task"), 80),
        "at": _redact(item.get("ts_utc"), 50),
        "content": _redact(item.get("content") or item.get("body"), 260),
    } for item in messages[:24]]


def prime_reports() -> list[dict]:
    if not PRIME_RUNS.exists():
        return []
    reports = sorted(
        (path for path in PRIME_RUNS.rglob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:12]
    out = []
    for path in reports:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        excerpt = next((line.strip("# ") for line in text.splitlines() if line.strip("# ").strip()), "rapport")
        out.append({
            "run": path.parent.name[:100],
            "file": path.name[:100],
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "excerpt": _redact(excerpt, 220),
        })
    return out


def governance(service_state: dict, messages: list[dict], reports: list[dict]) -> list[dict]:
    latest: dict[str, str] = {}
    for message in messages:
        actor = message.get("from")
        if actor and actor not in latest:
            latest[actor] = message.get("at") or ""
    processes = service_state.get("processes", {})
    return [
        {"id": "florent", "name": "Florent", "role": "arbitrage humain", "status": "owner", "last_activity": ""},
        {"id": "prime", "name": "Prime", "role": "gouvernance dev et preuves",
         "status": "active" if processes.get("prime_clients") or processes.get("prime_daemons") else "idle",
         "last_activity": reports[0]["modified_at"] if reports else "",
         "models": processes.get("models", [])},
        {"id": "claude", "name": "Claude", "role": "implementation et revue croisee",
         "status": "active" if latest.get("claude") or processes.get("claude_clients") else "idle",
         "last_activity": latest.get("claude", "")},
        {"id": "codex", "name": "Codex", "role": "audit, integration et tests",
         "status": "active" if latest.get("codex") or processes.get("codex_clients") else "idle",
         "last_activity": latest.get("codex", "")},
        {"id": "hermes", "name": "Hermes", "role": "conseil et memoire",
         "status": "active" if service_state.get("hermes_mcp", {}).get("running") else "offline",
         "last_activity": latest.get("hermes", "")},
    ]


def profitability(base: dict) -> dict:
    loop = base.get("loop") or {}
    stats = loop.get("stats") or {}
    tunnel = stats.get("tunnel") or {}
    edge = base.get("edge") or {}
    contexts = edge.get("contexts") or []
    try:
        from titanium.web.state import promotion
        promo = promotion()
    except Exception as exc:  # noqa: BLE001
        promo = {"error": type(exc).__name__, "cells": []}
    cells = promo.get("cells") or promo.get("cellules") or []
    promotable = sum(1 for cell in cells if cell.get("promotable") or cell.get("ready"))
    return {
        "closed_trades": edge.get("total_trades", 0),
        "rejected_lines": edge.get("rejected_lines", 0),
        "contexts": len(contexts),
        "positive_contexts": sum(1 for item in contexts if item.get("edge_ok") is True),
        "promotion_cells": len(cells),
        "promotable_cells": promotable,
        "loop": {
            "armed": loop.get("armed", False), "running": loop.get("running", False),
            "tours": stats.get("tours", 0), "evaluated": stats.get("evalues", 0),
            "enter": stats.get("enter", 0), "sent": stats.get("envoyes", 0),
        },
        "funnel": tunnel,
    }


def snapshot(base: dict | None = None) -> dict:
    if base is None:
        from titanium.web.state import state
        base = state()
    service_state = services()
    messages = activity()
    reports = prime_reports()
    return {
        "meta": {"generated_at": _now(), "mode": "DEMO_ONLY", "version": "1"},
        "safety": {
            "real_trading": False,
            "task_auto_launch": False,
            "shell_endpoint": False,
            "secrets_exposed": False,
            "message": "Les taches coordonnent le travail; elles ne lancent aucun LLM ni ordre.",
        },
        "runtime": service_state,
        "governance": governance(service_state, messages, reports),
        "tasks": tasks_snapshot(),
        "profitability": profitability(base),
        "activity": messages,
        "prime_reports": reports,
        "tests": base.get("tests") or {},
        "account": base.get("account") or {},
        "wall": base.get("wall") or {},
    }
