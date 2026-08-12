"""Conserve le contexte d'un ordre limite jusqu'à sa transformation en position."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from titanium.execution.position_manager import TrackedState, load_state, save_state


def _read(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def save_pending_context(path: Path, *, order_ticket: int, symbol: str, side: int,
                         expires_at: str, state: TrackedState) -> None:
    data = _read(path)
    data[str(order_ticket)] = {
        "symbol": str(symbol), "side": int(side),
        "expires_at": str(expires_at),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": state.to_dict(),
    }
    _write(path, data)


def reconcile_pending_contexts(mt5, *, magic: int, state_path: Path,
                               pending_path: Path, positions=None) -> dict:
    """Rattache par symbole/sens le contexte pending au ticket de position."""
    report = {"adopted": 0, "pending": 0, "purged": 0}
    pending = _read(pending_path)
    if not pending:
        return report
    current_positions = list(positions if positions is not None else (mt5.positions_get() or []))
    state = load_state(state_path)

    live_orders: set[str] | None = None
    try:
        orders = mt5.orders_get()
        if orders is not None:
            live_orders = {str(o.ticket) for o in orders
                           if int(getattr(o, "magic", 0) or 0) == int(magic)}
    except Exception:  # noqa: BLE001
        live_orders = None

    changed_state = False
    for pos in current_positions:
        if int(getattr(pos, "magic", 0) or 0) != int(magic):
            continue
        ticket = str(pos.ticket)
        if ticket in state:
            continue
        side = 1 if int(getattr(pos, "type", 0) or 0) == int(mt5.ORDER_TYPE_BUY) else -1
        matches = [
            (key, value) for key, value in pending.items()
            if str(value.get("symbol")) == str(pos.symbol)
            and int(value.get("side", 0) or 0) == side
        ]
        if not matches:
            continue
        key, value = sorted(matches, key=lambda item: item[1].get("created_at", ""))[0]
        template = TrackedState.from_dict(value["state"])
        entry = float(getattr(pos, "price_open", 0.0) or template.entry)
        sl = float(getattr(pos, "sl", 0.0) or template.sl_initial)
        tp = float(getattr(pos, "tp", 0.0) or template.tp_initial)
        state[ticket] = replace(
            template, entry=entry, sl_initial=sl, tp_initial=tp,
            r=abs(entry - sl) if entry and sl else template.r,
        )
        pending.pop(key, None)
        changed_state = True
        report["adopted"] += 1

    now = datetime.now(timezone.utc)
    if live_orders is not None:
        for key, value in list(pending.items()):
            if key in live_orders:
                continue
            try:
                expiry = datetime.fromisoformat(str(value.get("expires_at", "")))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if now.timestamp() > expiry.timestamp() + 60:
                    pending.pop(key, None)
                    report["purged"] += 1
            except (TypeError, ValueError):
                continue

    report["pending"] = len(pending)
    if changed_state:
        save_state(state_path, state)
    _write(pending_path, pending)
    return report
