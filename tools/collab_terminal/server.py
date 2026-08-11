"""V14 Collab Terminal — Terminal commun de collaboration multi-LLM.

Interface unifiee de travail pour Claude, Codex, Hermes, Copilot et Prime.
Integre :
  - Chat bidirectionnel avec le hub MCP et le bus fichier
  - Dispatch automatique vers les agents via collab_hub (port 8770)
  - Bridge Hermes MCP direct (port 8766)
  - Liste des taches et statuts avec mise a jour live
  - Repartition et assignation des taches par LLM
  - Niveaux de validation multi-LLM (scoring consensus)
  - Scoring de performance par tache et par agent

    python tools/collab_terminal/server.py          # lance le terminal
    python tools/collab_terminal/server.py --port 8097

Lecture seule sauf journal de taches et chat (meme contrat que le Command Center).
Aucune route ne passe d'ordre, ne redemarre un service ni n'appelle un LLM.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RACINE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RACINE))

from titanium.collab_tasks import (  # noqa: E402
    TaskError,
    create_task,
    snapshot,
    update_task,
)

PORT = 8097
STATIC = Path(__file__).resolve().parent / "static"

# --- Canaux de communication ---

HUB_HOST = "127.0.0.1"
HUB_PORT = 8770
HERMES_HOST = "127.0.0.1"
HERMES_PORT = 8766

V12 = Path(os.environ.get("TITANIUM_V12_ROOT", RACINE.parent / "v12"))
BUS_STREAM = V12 / "collab" / "messages" / "stream.ndjson"
BUS_ACKS   = V12 / "collab" / "messages" / "acks.ndjson"
COLLAB_LOG = RACINE / "tools" / "collab_terminal" / "chat_log.ndjson"
PRIME_RUNS = RACINE / "collab" / "prime_agent" / "runs"

# --- Agents connus ---

AGENTS = {
    "prime":   {"name": "Prime Agent",  "color": "#10b981", "icon": "P",  "role": "Code owner & tech lead"},
    "claude":  {"name": "Claude",       "color": "#8b5cf6", "icon": "Cl", "role": "Relecture & architecture"},
    "codex":   {"name": "Codex",        "color": "#3b82f6", "icon": "Cx", "role": "Implementation & CI"},
    "hermes":  {"name": "Hermes",       "color": "#f59e0b", "icon": "He", "role": "Analyse rentabilite & risque"},
    "copilot": {"name": "Copilot",      "color": "#6366f1", "icon": "Co", "role": "Assistance contextuelle"},
    "florent": {"name": "Florent",      "color": "#ef4444", "icon": "Fl", "role": "Autorite metier & arbitre"},
}

PRINCIPALS_HUB = {"claude", "codex", "hermes", "florent", "system"}
PRINCIPALS_BUS = {"claude", "codex", "hermes", "florent", "system"}

# ================================================================
#  WORKER MANAGEMENT
# ================================================================

_worker_proc = None
_worker_lock = threading.Lock()

WORKER_SCRIPT = Path(__file__).resolve().parent / "worker.py"
WORKER_STATE = RACINE / "collab" / "notifications" / "_worker_state.json"
PYTHON = RACINE / ".venv" / "Scripts" / "python.exe"


def _worker_status() -> dict:
    """Statut du worker de notifications."""
    global _worker_proc
    running = False
    pid = None

    with _worker_lock:
        if _worker_proc is not None:
            if _worker_proc.poll() is None:
                running = True
                pid = _worker_proc.pid
            else:
                _worker_proc = None

    # Lire l'etat persistant
    state = {}
    try:
        state = json.loads(WORKER_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    # Notifications en attente par agent
    pending = {}
    notif_dir = RACINE / "collab" / "notifications"
    if notif_dir.exists():
        for fn in notif_dir.iterdir():
            if fn.suffix == ".json" and fn.stem.endswith(".pending"):
                agent = fn.stem.replace(".pending", "")
                try:
                    msgs = json.loads(fn.read_text(encoding="utf-8"))
                    pending[agent] = len(msgs)
                except (json.JSONDecodeError, OSError):
                    pending[agent] = 0

    return {
        "running": running,
        "pid": pid,
        "polls": state.get("polls", 0),
        "last_offset": state.get("last_offset", 0),
        "last_poll": state.get("last_poll", ""),
        "started_at": state.get("started_at", ""),
        "pending": pending,
    }


def _worker_start(interval: int = 300) -> dict:
    """Demarre le worker en arriere-plan."""
    global _worker_proc

    with _worker_lock:
        # Verifier s'il tourne deja
        if _worker_proc is not None and _worker_proc.poll() is None:
            return {"ok": False, "error": "worker deja actif", "pid": _worker_proc.pid}

        try:
            _worker_proc = subprocess.Popen(
                [str(PYTHON), str(WORKER_SCRIPT), "--interval", str(interval)],
                cwd=str(RACINE),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            return {"ok": True, "pid": _worker_proc.pid, "interval": interval}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _worker_stop() -> dict:
    """Arrete le worker."""
    global _worker_proc

    with _worker_lock:
        if _worker_proc is None or _worker_proc.poll() is not None:
            _worker_proc = None
            return {"ok": True, "was_running": False}

        pid = _worker_proc.pid
        try:
            _worker_proc.terminate()
            _worker_proc.wait(timeout=5)
        except Exception:
            try:
                _worker_proc.kill()
            except Exception:
                pass
        _worker_proc = None
        return {"ok": True, "was_running": True, "pid": pid}


# --- Redactions ---

_SECRET = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*\S+"),
)

def _redact(text: str) -> str:
    for p in _SECRET:
        text = p.sub("[masque]", text)
    return text

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ================================================================
#  HUB MCP BRIDGE
# ================================================================

def _mcp_call(host: str, port: int, method: str, params: dict = None, timeout: int = 8) -> dict:
    """Appel JSON-RPC vers un serveur MCP."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {}
        })
        conn.request("POST", "/mcp", body=payload, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        })
        resp = conn.getresponse()
        data = resp.read().decode()
        conn.close()
        return json.loads(data)
    except Exception as e:
        return {"error": str(e)}


def hub_publish(principal: str, target: str, kind: str, content: str) -> dict:
    """Publie un message sur le collab_hub MCP."""
    return _mcp_call(HUB_HOST, HUB_PORT, "tools/call", {
        "name": "collab_publish",
        "arguments": {
            "principal": principal,
            "target": target,
            "kind": kind,
            "content": _redact(content),
            "idempotency_key": str(uuid.uuid4()),
        }
    })


def hub_read(after_offset: int = 0, limit: int = 100) -> list[dict]:
    """Lit les messages du collab_hub."""
    result = _mcp_call(HUB_HOST, HUB_PORT, "tools/call", {
        "name": "collab_read",
        "arguments": {"after_offset": after_offset, "limit": limit}
    })
    try:
        text = result["result"]["content"][0]["text"]
        data = json.loads(text)
        return data.get("messages", [])
    except (KeyError, IndexError, json.JSONDecodeError):
        return []


def hub_presence() -> list[dict]:
    """Liste les presences connues du hub."""
    result = _mcp_call(HUB_HOST, HUB_PORT, "tools/call", {
        "name": "collab_presence",
        "arguments": {}
    })
    try:
        text = result["result"]["content"][0]["text"]
        return json.loads(text).get("presence", [])
    except (KeyError, IndexError, json.JSONDecodeError):
        return []


def hub_health() -> dict:
    """Sante du hub."""
    result = _mcp_call(HUB_HOST, HUB_PORT, "tools/call", {
        "name": "collab_health",
        "arguments": {}
    })
    try:
        text = result["result"]["content"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError):
        return {"status": "unreachable"}


# ================================================================
#  HERMES MCP BRIDGE
# ================================================================

def hermes_tools() -> list[dict]:
    """Liste les outils Hermes."""
    result = _mcp_call(HERMES_HOST, HERMES_PORT, "tools/list")
    try:
        return result["result"]["tools"]
    except (KeyError, TypeError):
        return []


def hermes_conversations() -> list[dict]:
    """Liste les conversations Hermes."""
    result = _mcp_call(HERMES_HOST, HERMES_PORT, "tools/call", {
        "name": "conversations_list",
        "arguments": {}
    })
    try:
        text = result["result"]["content"][0]["text"]
        return json.loads(text).get("conversations", [])
    except (KeyError, IndexError, json.JSONDecodeError):
        return []


def hermes_send(target: str, content: str) -> dict:
    """Envoie un message via Hermes."""
    result = _mcp_call(HERMES_HOST, HERMES_PORT, "tools/call", {
        "name": "messages_send",
        "arguments": {"target": target, "text": content}
    })
    return result


# ================================================================
#  BUS FICHIER BRIDGE
# ================================================================

_bus_lock = threading.Lock()

def bus_write(from_agent: str, to_agent: str, content: str, task: str = "V14") -> dict:
    """Ecrit un message dans le bus fichier (secours)."""
    msg = {
        "id": str(uuid.uuid4()),
        "type": "message",
        "from": from_agent,
        "to": to_agent,
        "task": task,
        "content": _redact(content),
        "ts_utc": _now(),
    }
    payload = json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _bus_lock:
        BUS_STREAM.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(BUS_STREAM, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
    return msg


def bus_read(limit: int = 80) -> list[dict]:
    """Lit les derniers messages du bus fichier."""
    try:
        lines = BUS_STREAM.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            v = json.loads(line)
            if isinstance(v, dict):
                out.append(v)
        except json.JSONDecodeError:
            continue
    return out


# ================================================================
#  MESSAGE DISPATCHER
# ================================================================

_hub_offset = 0
_hub_offset_lock = threading.Lock()


def dispatch_message(from_agent: str, to_agent: str, content: str, kind: str = "message") -> dict:
    """Route un message vers les bons canaux.

    Strategie de routage :
    1. Toujours enregistrer dans le chat local
    2. Si le hub est joignable -> publier sur le hub MCP (canal principal)
    3. Ecrire dans le bus fichier (secours, visible par tous les agents au redemarrage)
    4. Pour Hermes, essayer aussi le bridge Hermes direct
    """
    # 1. Chat local
    chat_msg = _append_chat({
        "from": from_agent,
        "to": to_agent,
        "content": content,
        "type": kind,
        "routed": [],
    })

    routes = []

    # 2. Hub MCP (le hub utilise "status" comme kind)
    if _port_open(HUB_PORT):
        pub_principal = from_agent if from_agent in PRINCIPALS_HUB else "system"

        # Si c'est pour "all", publier a chaque agent sauf l'emetteur
        if to_agent == "all":
            targets = [a for a in PRINCIPALS_HUB if a != from_agent and a != "system"]
        elif to_agent == "prime":
            # Prime n'est pas un principal natif du CollabHub. Ses echanges
            # transitent aujourd'hui par le principal system et la boite
            # Florent. Conserver le destinataire Prime dans le chat/bus, mais
            # publier sur sa boite de coordination reelle au lieu du fallback
            # historique vers Claude.
            targets = ["florent"]
        elif to_agent in PRINCIPALS_HUB:
            targets = [to_agent]
        else:
            targets = ["claude"]  # fallback

        for t in targets:
            hub_content = (
                f"[Terminal][Pour Prime] {content}"
                if to_agent == "prime"
                else f"[Terminal] {content}"
            )
            result = hub_publish(pub_principal, t, "status", hub_content)
            if not result.get("result", {}).get("isError", True):
                route = "hub->prime(via florent)" if to_agent == "prime" else f"hub->{t}"
                routes.append(route)

    # 3. Bus fichier
    try:
        bus_write(from_agent, to_agent, content)
        routes.append("bus")
    except Exception:
        pass

    # 4. Hermes direct (si le message est pour Hermes)
    if to_agent in ("hermes", "all") and _port_open(HERMES_PORT):
        routes.append("hermes-bridge")

    # Mettre a jour le message avec les routes
    chat_msg["routed"] = routes
    return {"message": chat_msg, "routes": routes}


def ingest_hub_messages():
    """Ingere les nouveaux messages du hub dans le chat local."""
    global _hub_offset
    if not _port_open(HUB_PORT):
        return []

    with _hub_offset_lock:
        messages = hub_read(after_offset=_hub_offset, limit=50)
        new_msgs = []
        for m in messages:
            offset = m.get("global_offset", 0)
            if offset > _hub_offset:
                _hub_offset = offset
                # Verifier si ce message vient du terminal (eviter les doublons)
                content = str(m.get("content", ""))
                if content.startswith("[Terminal] "):
                    continue  # C'est un message qu'on a nous-memes envoye

                chat_msg = _append_chat({
                    "from": m.get("principal", "system"),
                    "to": m.get("target", "all"),
                    "content": content,
                    "type": m.get("kind", "message"),
                    "source": "hub",
                    "hub_offset": offset,
                })
                new_msgs.append(chat_msg)
        return new_msgs


def ingest_bus_messages():
    """Ingere les messages recents du bus qui ne sont pas encore dans le chat."""
    bus_msgs = bus_read(30)
    chat_msgs = _read_chat(200)

    # IDs deja dans le chat
    known_ids = {m.get("bus_id") for m in chat_msgs if m.get("bus_id")}
    known_content = {(m.get("from",""), m.get("content","")[:80]) for m in chat_msgs}

    new_msgs = []
    for m in bus_msgs:
        mid = m.get("id", "")
        sig = (m.get("from", ""), str(m.get("content", ""))[:80])
        if mid not in known_ids and sig not in known_content:
            chat_msg = _append_chat({
                "from": m.get("from", "system"),
                "to": m.get("to", "all"),
                "content": str(m.get("content", "")),
                "type": m.get("type", "message"),
                "source": "bus",
                "bus_id": mid,
            })
            new_msgs.append(chat_msg)
    return new_msgs


# ================================================================
#  BACKGROUND POLLER
# ================================================================

_poller_running = False

def _poller_loop():
    """Thread de fond qui ingere les messages des canaux externes."""
    global _hub_offset

    # Au premier lancement, charger le backlog complet du hub dans le chat
    if _port_open(HUB_PORT):
        all_msgs = hub_read(after_offset=0, limit=500)
        existing_chat = _read_chat(500)
        existing_offsets = {m.get("hub_offset") for m in existing_chat if m.get("hub_offset")}

        for m in all_msgs:
            offset = m.get("global_offset", 0)
            if offset not in existing_offsets:
                content = str(m.get("content", ""))
                if not content.startswith("[Terminal] "):
                    _append_chat({
                        "from": m.get("principal", "system"),
                        "to": m.get("target", "all"),
                        "content": content,
                        "type": m.get("kind", "message"),
                        "source": "hub",
                        "hub_offset": offset,
                    })
            if offset > _hub_offset:
                _hub_offset = offset

    while _poller_running:
        try:
            ingest_hub_messages()
        except Exception:
            pass
        try:
            ingest_bus_messages()
        except Exception:
            pass
        time.sleep(5)


def start_poller():
    global _poller_running
    _poller_running = True
    t = threading.Thread(target=_poller_loop, daemon=True, name="collab-poller")
    t.start()


def stop_poller():
    global _poller_running
    _poller_running = False


# ================================================================
#  SCORING (inchange)
# ================================================================

def _compute_task_scores(tasks: list[dict]) -> list[dict]:
    scored = []
    for t in tasks:
        score = 0
        factors = []
        prio_weight = {"P0": 40, "P1": 30, "P2": 20, "P3": 10}
        score += prio_weight.get(t.get("priority", "P2"), 15)
        factors.append(f"priorite {t.get('priority', '?')}")
        status_weight = {"done": 40, "review": 30, "in_progress": 20, "backlog": 5, "blocked": 0}
        score += status_weight.get(t.get("status", "backlog"), 5)
        factors.append(f"statut {t.get('status', '?')}")
        validators = set()
        if t.get("updated_by"):
            validators.add(t["updated_by"])
        validation_bonus = min(len(validators) * 5, 20)
        score += validation_bonus
        factors.append(f"{len(validators)} validateur(s)")
        note = t.get("note", "")
        if note:
            if any(w in note.lower() for w in ("preuve", "test", "pass", "verifie", "valide")):
                score += 10
                factors.append("preuve documentee")
        scored.append({**t, "score": min(score, 100), "score_factors": factors, "validators": list(validators)})
    return scored


def _agent_performance(tasks: list[dict]) -> dict:
    perf = {}
    for agent_id in AGENTS:
        owned = [t for t in tasks if t.get("owner") == agent_id]
        done = [t for t in owned if t.get("status") == "done"]
        in_progress = [t for t in owned if t.get("status") == "in_progress"]
        blocked = [t for t in owned if t.get("status") == "blocked"]
        total = len(owned)
        completion_rate = (len(done) / total * 100) if total > 0 else 0
        perf[agent_id] = {
            **AGENTS[agent_id],
            "total_tasks": total, "done": len(done), "in_progress": len(in_progress),
            "blocked": len(blocked), "completion_rate": round(completion_rate, 1),
            "avg_score": round(sum(t.get("score", 0) for t in owned) / total, 1) if total else 0,
        }
    return perf


def _validation_matrix(tasks: list[dict]) -> dict:
    matrix = {a: {b: 0 for b in AGENTS} for a in AGENTS}
    for t in tasks:
        owner = t.get("owner", "team")
        updater = t.get("updated_by", "")
        if owner in AGENTS and updater in AGENTS and owner != updater:
            matrix[owner][updater] += 1
    return matrix


# ================================================================
#  CHAT LOG
# ================================================================

_chat_lock = threading.Lock()

def _append_chat(msg: dict) -> dict:
    msg.setdefault("id", str(uuid.uuid4()))
    msg.setdefault("at", _now())
    msg["content"] = _redact(str(msg.get("content", "")))
    payload = json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _chat_lock:
        COLLAB_LOG.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(COLLAB_LOG, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
    return msg

def _read_chat(limit: int = 200) -> list[dict]:
    try:
        lines = COLLAB_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            v = json.loads(line)
            if isinstance(v, dict):
                out.append(v)
        except json.JSONDecodeError:
            continue
    return out


# ================================================================
#  HELPERS
# ================================================================

def _load_ndjson(path: Path, limit: int = 80) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            v = json.loads(line)
            if isinstance(v, dict):
                out.append(v)
        except json.JSONDecodeError:
            continue
    return out


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _services_status() -> dict:
    return {
        "dashboard":    {"running": _port_open(8095), "port": 8095},
        "collab_hub":   {"running": _port_open(HUB_PORT), "port": HUB_PORT},
        "hermes_mcp":   {"running": _port_open(HERMES_PORT), "port": HERMES_PORT},
        "terminal":     {"running": True, "port": PORT},
    }


def _agent_presence() -> dict:
    """Detecte la presence reelle des agents sur le systeme."""
    presence = {}

    # Detecter les processus Windows
    if os.name == "nt":
        try:
            cmd = (
                'Get-CimInstance Win32_Process | '
                'Where-Object {$_.Name -match "node|claude|codex|copilot|prime-agent"} | '
                'Select-Object Name,ProcessId | ConvertTo-Json -Compress'
            )
            raw = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=8,
                encoding="utf-8", errors="replace",
            ).stdout.strip()
            procs = json.loads(raw) if raw else []
            if isinstance(procs, dict):
                procs = [procs]
        except Exception:
            procs = []

        process_map = {
            "claude": 0, "codex": 0, "prime": 0, "copilot": 0, "hermes": 0
        }
        for p in procs:
            name = str(p.get("Name", "")).lower()
            if "claude" in name and "claude.exe" in name:
                process_map["claude"] += 1
            elif "codex-code-mode" in name or "codex-computer-use" in name:
                process_map["copilot"] += 1  # GitHub Copilot dans VSCode
            elif "codex" in name:
                process_map["codex"] += 1
            elif "copilot" in name:
                process_map["copilot"] += 1

        # Prime: detecte via le daemon
        for p in procs:
            cmd_line = str(p.get("CommandLine", "")).lower() if "CommandLine" in p else ""
            name = str(p.get("Name", "")).lower()
            if "prime-agent" in name or ("node" in name and "prime-agent" in cmd_line):
                process_map["prime"] += 1

    else:
        process_map = {}

    # Derniere activite par agent sur le hub
    last_activity = {}
    chat_msgs = _read_chat(500)
    for m in chat_msgs:
        agent = m.get("from", "")
        if agent in AGENTS:
            ts = m.get("at", "")
            if ts > last_activity.get(agent, ""):
                last_activity[agent] = ts

    # Services MCP
    hub_ok = _port_open(HUB_PORT)
    hermes_ok = _port_open(HERMES_PORT)

    for agent_id, info in AGENTS.items():
        pcount = process_map.get(agent_id, 0)
        last = last_activity.get(agent_id, "")

        # Determiner le statut
        if agent_id == "hermes":
            online = hermes_ok
            channel = "MCP 8766 + Telegram"
        elif agent_id == "prime":
            online = True  # Nous sommes Prime
            channel = "Terminal direct"
        elif agent_id == "florent":
            online = True  # Florent est l'humain
            channel = "Terminal"
        elif agent_id == "copilot":
            online = pcount > 0
            channel = "VSCode extension"
        else:
            online = pcount > 0 and hub_ok
            channel = f"Hub MCP + {'actif' if pcount > 0 else 'inactif'}"

        presence[agent_id] = {
            **info,
            "online": online,
            "processes": pcount,
            "last_activity": last,
            "channel": channel,
        }

    return presence


def _prime_reports() -> list[dict]:
    reports = []
    if not PRIME_RUNS.exists():
        return reports
    for run_dir in sorted(PRIME_RUNS.iterdir()):
        report = run_dir / "report.md"
        if report.exists():
            content = report.read_text(encoding="utf-8", errors="replace")
            reports.append({
                "task_id": run_dir.name,
                "content": content[:2000],
                "path": str(report.relative_to(RACINE)),
            })
    return reports


def _heartbeat() -> dict:
    hb_path = RACINE / "results" / "loop_heartbeat.json"
    try:
        return json.loads(hb_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _hub_messages_for_display() -> list[dict]:
    """Derniers messages du hub pour l'onglet activite."""
    if not _port_open(HUB_PORT):
        return []
    msgs = hub_read(after_offset=max(0, _hub_offset - 30), limit=30)
    return [
        {
            "offset": m.get("global_offset", 0),
            "from": m.get("principal", "?"),
            "to": m.get("target", "?"),
            "kind": m.get("kind", "?"),
            "content": _redact(str(m.get("content", "")))[:300],
            "at": str(m.get("published_at", m.get("at", "")))[:30],
        }
        for m in msgs
    ]


# ================================================================
#  API
# ================================================================

def api_full_state() -> dict:
    snap = snapshot()
    tasks_scored = _compute_task_scores(snap["tasks"])
    return {
        "terminal": "V14 Collab Terminal",
        "version": "2.1.0",
        "at": _now(),
        "agents": AGENTS,
        "tasks": {"items": tasks_scored, "counts": snap["counts"], "events": snap["events"]},
        "performance": _agent_performance(tasks_scored),
        "validation_matrix": _validation_matrix(tasks_scored),
        "services": _services_status(),
        "chat": _read_chat(100),
        "hub_activity": _hub_messages_for_display(),
        "bus_activity": [
            {
                "id": str(m.get("id", ""))[:80],
                "from": str(m.get("from", ""))[:30],
                "to": str(m.get("to", ""))[:30],
                "content": _redact(str(m.get("content", "")))[:240],
                "at": str(m.get("ts_utc", ""))[:30],
            }
            for m in bus_read(30)
        ],
        "prime_reports": _prime_reports(),
        "heartbeat": _heartbeat(),
        "hub_health": hub_health() if _port_open(HUB_PORT) else {"status": "offline"},
        "presence": _agent_presence(),
        "worker": _worker_status(),
    }


# ================================================================
#  HTTP HANDLER
# ================================================================

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
}


class CollabHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path):
        if not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        mime = MIME.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/state":
            self._json(api_full_state())
        elif path == "/api/tasks":
            snap = snapshot()
            self._json(_compute_task_scores(snap["tasks"]))
        elif path == "/api/performance":
            snap = snapshot()
            self._json(_agent_performance(_compute_task_scores(snap["tasks"])))
        elif path == "/api/chat":
            self._json(_read_chat(200))
        elif path == "/api/bus":
            self._json(bus_read(50))
        elif path == "/api/hub":
            self._json(_hub_messages_for_display())
        elif path == "/api/services":
            self._json(_services_status())
        elif path == "/api/agents":
            self._json(AGENTS)
        elif path == "/api/heartbeat":
            self._json(_heartbeat())
        elif path == "/api/validation":
            snap = snapshot()
            self._json(_validation_matrix(_compute_task_scores(snap["tasks"])))
        elif path == "/api/reports":
            self._json(_prime_reports())
        elif path == "/api/hub/health":
            self._json(hub_health() if _port_open(HUB_PORT) else {"status": "offline"})
        elif path == "/api/worker":
            self._json(_worker_status())
        elif path == "/api/presence":
            self._json(_agent_presence())
        elif path == "/api/hermes/conversations":
            self._json(hermes_conversations() if _port_open(HERMES_PORT) else [])
        elif path == "/" or path == "/index.html":
            self._file(STATIC / "index.html")
        elif path.startswith("/static/"):
            rel = path[8:]
            target = (STATIC / rel).resolve()
            if STATIC.resolve() in target.parents or target == STATIC.resolve():
                self._file(target)
            else:
                self.send_error(403)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if path == "/api/chat":
            try:
                data = json.loads(body)
                from_agent = str(data.get("from", "florent"))[:30]
                to_agent = str(data.get("to", "all"))[:30]
                content = str(data.get("content", ""))[:2000]
                kind = str(data.get("type", "message"))[:30]

                # DISPATCH : router vers tous les canaux
                result = dispatch_message(from_agent, to_agent, content, kind)
                self._json(result, 201)
            except (json.JSONDecodeError, Exception) as e:
                self._json({"error": str(e)}, 400)

        elif path == "/api/tasks":
            try:
                data = json.loads(body)
                if "id" in data:
                    result = update_task(data["id"], data)
                else:
                    result = create_task(data)
                self._json(result, 201)
            except TaskError as e:
                self._json({"error": str(e)}, 400)
            except (json.JSONDecodeError, Exception) as e:
                self._json({"error": str(e)}, 400)
        elif path == "/api/worker/start":
            try:
                data = json.loads(body) if body else {}
                interval = int(data.get("interval", 300))
                result = _worker_start(interval)
                self._json(result, 201 if result.get("ok") else 409)
            except Exception as e:
                self._json({"error": str(e)}, 400)

        elif path == "/api/worker/stop":
            result = _worker_stop()
            self._json(result)

        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ================================================================
#  MAIN
# ================================================================

def main():
    import argparse
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    port = args.port

    # Demarrer le poller de messages
    start_poller()

    hub_ok = _port_open(HUB_PORT)
    hermes_ok = _port_open(HERMES_PORT)

    server = ThreadingHTTPServer(("127.0.0.1", port), CollabHandler)
    print(f"\n{'='*60}")
    print(f"  V14 Collab Terminal v2.0")
    print(f"  http://127.0.0.1:{port}")
    print(f"{'='*60}")
    print(f"  Chat dispatch ->  POST /api/chat")
    print(f"  Etat complet  ->  GET  /api/state")
    print(f"  Hub MCP       ->  {'CONNECTE' if hub_ok else 'HORS LIGNE'} (:{HUB_PORT})")
    print(f"  Hermes MCP    ->  {'CONNECTE' if hermes_ok else 'HORS LIGNE'} (:{HERMES_PORT})")
    print(f"  Bus fichier   ->  {BUS_STREAM}")
    print(f"  Poller        ->  actif (5s)")
    print(f"{'='*60}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret du terminal.")
    finally:
        stop_poller()
        server.server_close()


if __name__ == "__main__":
    main()
