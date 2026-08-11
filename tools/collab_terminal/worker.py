"""V14 Collab Worker — micro-daemon de notification inter-agents.

Poll le Hub MCP toutes les N secondes (ZERO token LLM).
Quand un message arrive pour un agent, ecrit une notification dans
collab/notifications/<agent>.pending.json.
Le LLM n'est invoque que quand il y a du vrai travail.

    python tools/collab_terminal/worker.py             # 5 min par defaut
    python tools/collab_terminal/worker.py --interval 60  # toutes les minutes

Cout : 0 token, 0 dollar. Juste un appel HTTP local toutes les 5 min.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
NOTIF_DIR = RACINE / "collab" / "notifications"
STATE_FILE = RACINE / "collab" / "notifications" / "_worker_state.json"

HUB_HOST = "127.0.0.1"
HUB_PORT = 8770

AGENTS = ("claude", "codex", "hermes", "prime", "florent")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mcp_call(method: str, params: dict = None, timeout: int = 8) -> dict:
    try:
        conn = http.client.HTTPConnection(HUB_HOST, HUB_PORT, timeout=timeout)
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": method, "params": params or {}
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


def hub_read(after_offset: int, limit: int = 50) -> list[dict]:
    result = mcp_call("tools/call", {
        "name": "collab_read",
        "arguments": {"after_offset": after_offset, "limit": limit}
    })
    try:
        text = result["result"]["content"][0]["text"]
        return json.loads(text).get("messages", [])
    except (KeyError, IndexError, json.JSONDecodeError):
        return []


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_offset": 0, "started_at": _now(), "polls": 0}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def write_notification(agent: str, messages: list[dict]):
    """Ecrit les messages en attente pour un agent."""
    notif_path = NOTIF_DIR / f"{agent}.pending.json"
    NOTIF_DIR.mkdir(parents=True, exist_ok=True)

    # Charger les notifications existantes
    existing = []
    if notif_path.exists():
        try:
            existing = json.loads(notif_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []

    # Ajouter les nouvelles
    existing_offsets = {m.get("global_offset") for m in existing}
    new_msgs = [m for m in messages if m.get("global_offset") not in existing_offsets]

    if new_msgs:
        existing.extend(new_msgs)
        # Garder les 50 derniers max
        existing = existing[-50:]
        notif_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        return len(new_msgs)
    return 0


def clear_notification(agent: str):
    """Marque les notifications comme lues."""
    notif_path = NOTIF_DIR / f"{agent}.pending.json"
    ack_path = NOTIF_DIR / f"{agent}.ack.json"
    if notif_path.exists():
        # Deplacer vers ack
        data = notif_path.read_text(encoding="utf-8")
        ack_path.write_text(data, encoding="utf-8")
        notif_path.unlink()


def poll_once(state: dict) -> dict:
    """Un cycle de poll."""
    messages = hub_read(after_offset=state["last_offset"], limit=50)

    if not messages:
        state["polls"] += 1
        return state

    # Trier par agent destinataire
    by_agent: dict[str, list[dict]] = {a: [] for a in AGENTS}
    max_offset = state["last_offset"]

    for m in messages:
        offset = m.get("global_offset", 0)
        target = m.get("target", "")
        principal = m.get("principal", "")

        if offset > max_offset:
            max_offset = offset

        # Router vers l'agent cible
        if target in by_agent:
            by_agent[target].append(m)
        # Les messages topic: vont a tous
        elif target.startswith("topic:"):
            for a in AGENTS:
                if a != principal:
                    by_agent[a].append(m)

    # Ecrire les notifications
    notified = []
    for agent, msgs in by_agent.items():
        if msgs:
            count = write_notification(agent, msgs)
            if count > 0:
                notified.append(f"{agent}:{count}")

    state["last_offset"] = max_offset
    state["polls"] += 1
    state["last_poll"] = _now()
    if notified:
        state["last_notified"] = notified

    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=300,
                        help="Intervalle de poll en secondes (defaut: 300 = 5 min)")
    parser.add_argument("--once", action="store_true",
                        help="Un seul poll puis quitter")
    args = parser.parse_args()

    # Force UTF-8 sur Windows
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    state = load_state()

    print(f"V14 Collab Worker")
    print(f"  Hub : {HUB_HOST}:{HUB_PORT}")
    print(f"  Intervalle : {args.interval}s")
    print(f"  Notifications : {NOTIF_DIR}")
    print(f"  Dernier offset : {state['last_offset']}")
    print(f"  Cout tokens : 0 (pur HTTP, aucun LLM)")
    print()

    if args.once:
        state = poll_once(state)
        save_state(state)
        notif = state.get("last_notified", [])
        print(f"  Poll #{state['polls']} | offset={state['last_offset']} | {notif or 'rien de neuf'}")
        return

    try:
        while True:
            state = poll_once(state)
            save_state(state)
            notif = state.get("last_notified", [])
            ts = datetime.now().strftime("%H:%M:%S")
            if notif:
                print(f"  [{ts}] Poll #{state['polls']} | offset={state['last_offset']} | NOTIF: {', '.join(notif)}")
                state.pop("last_notified", None)
            else:
                print(f"  [{ts}] Poll #{state['polls']} | offset={state['last_offset']} | -")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        save_state(state)
        print("\nWorker arrete.")


if __name__ == "__main__":
    main()
