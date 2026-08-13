"""Conserve le contexte d'un ordre limite jusqu'à sa transformation en position."""

from __future__ import annotations

import json
import math
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


def append_limit_event(path: Path, event: dict) -> tuple[bool, str]:
    """Ajoute un evenement immutable et idempotent au cycle des limites.

    L'identifiant ``ordre:event`` evite les doublons apres une relance tout en
    conservant un journal strictement append-only. Une panne de telemetrie est
    rendue au caller ; elle ne doit jamais lever dans la boucle de trading.
    """
    try:
        order_ticket = int(event.get("order_ticket", 0) or 0)
        event_name = str(event.get("event", "") or "").strip().lower()
        if not order_ticket or not event_name:
            return False, "EVENT_INVALIDE"
        event_id = f"{order_ticket}:{event_name}"
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
            "event_id": event_id,
            "event": event_name,
            "order_ticket": order_ticket,
            "at": str(event.get("at") or datetime.now(timezone.utc).isoformat()),
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True, "WRITTEN"
    except Exception as exc:  # noqa: BLE001
        return False, f"ERROR_{type(exc).__name__.upper()}"


def _lifecycle_events(path: Path) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    seen: set[str] = set()
    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event_id = str(event.get("event_id", ""))
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        events.append(event)
    return events


def _repair_adopted_limit_states(state_path: Path, lifecycle_path: Path) -> dict:
    """Repare uniquement la provenance d'un fill deja adopte.

    Le commit initial du journal de cycle a place par erreur les champs limite
    dans le chemin des ordres au marche. Les evenements append-only ``placed``
    et ``filled`` restent toutefois une preuve suffisante pour completer ces
    champs sans modifier entree, stop, risque, phase ou logique de gestion.
    """
    report = {"repaired": 0, "events_written": 0, "event_failures": 0}
    events = _lifecycle_events(lifecycle_path)
    if not events:
        return report
    placed = {
        int(event.get("order_ticket", 0) or 0): event
        for event in events if event.get("event") == "placed"
    }
    fills = [event for event in events if event.get("event") == "filled"]
    state = load_state(state_path)
    changed = False
    for fill in fills:
        order_ticket = int(fill.get("order_ticket", 0) or 0)
        position_ticket = str(fill.get("position_ticket", "") or "")
        template = state.get(position_ticket)
        plan = placed.get(order_ticket)
        if not order_ticket or template is None or plan is None:
            continue
        if template.limit_order_ticket:
            continue
        if str(plan.get("symbol", "")) != str(template.symbol):
            continue
        planned_price = float(plan.get("planned_price", 0.0) or 0.0)
        market_reference = float(plan.get("market_reference_price", 0.0) or 0.0)
        fill_price = float(fill.get("fill_price", 0.0) or template.entry or 0.0)
        if not (planned_price > 0 and market_reference > 0 and template.r > 0):
            continue
        realized_saving_r = (
            (market_reference - fill_price) * template.side / template.r)
        slippage_r = ((fill_price - planned_price) * template.side / template.r)
        target_saving_r = plan.get("target_saving_r")
        state[position_ticket] = replace(
            template,
            limit_order_ticket=order_ticket,
            limit_planned_price=planned_price,
            limit_market_reference_price=market_reference,
            limit_target_saving_r=(
                None if target_saving_r is None else float(target_saving_r)),
            limit_realized_saving_r=realized_saving_r,
            limit_slippage_r=slippage_r,
        )
        written, reason = append_limit_event(lifecycle_path, {
            "event": "filled_metrics", "order_ticket": order_ticket,
            "position_ticket": int(position_ticket), "symbol": template.symbol,
            "side": template.side, "planned_price": planned_price,
            "market_reference_price": market_reference,
            "fill_price": fill_price, "r_unit": template.r,
            "target_saving_r": target_saving_r,
            "realized_saving_r": realized_saving_r,
            "slippage_r": slippage_r,
            "context": template.context_key,
            "regime": plan.get("regime", "unknown"),
            "asset_class": template.asset_class,
            "mode": template.mode,
            "repair_source": "placed+filled",
        })
        report["events_written"] += int(written)
        report["event_failures"] += int(not written and reason != "DUPLICATE")
        report["repaired"] += 1
        changed = True
    if changed:
        save_state(state_path, state)
    return report


def limit_lifecycle_summary(path: Path) -> dict:
    """Agrege le denominateur causal des limites, globalement et par strate."""
    events = _lifecycle_events(path)

    def aggregate(rows: list[dict]) -> dict:
        counts = {name: sum(r.get("event") == name for r in rows)
                  for name in (
                      "placed", "filled", "expired", "canceled", "unknown", "closed")}
        known_resolved = counts["filled"] + counts["expired"] + counts["canceled"]
        resolved = known_resolved + counts["unknown"]
        metrics = {}
        for row in rows:
            if row.get("event") not in {"filled", "filled_metrics"}:
                continue
            ticket = int(row.get("order_ticket", 0) or 0)
            if ticket and (
                row.get("realized_saving_r") is not None
                or row.get("slippage_r") is not None
            ):
                metrics[ticket] = row
        saving = [float(r["realized_saving_r"]) for r in metrics.values()
                  if r.get("realized_saving_r") is not None
                  and math.isfinite(float(r["realized_saving_r"]))]
        slippage = [float(r["slippage_r"]) for r in metrics.values()
                    if r.get("slippage_r") is not None
                    and math.isfinite(float(r["slippage_r"]))]
        pnl = [float(r["pnl_r"]) for r in rows
               if r.get("event") == "closed" and r.get("pnl_r") is not None
               and math.isfinite(float(r["pnl_r"]))]
        return {
            **counts,
            "open": max(0, counts["placed"] - resolved),
            "fill_rate": (
                counts["filled"] / known_resolved if known_resolved else None),
            "mean_realized_saving_r": (sum(saving) / len(saving) if saving else None),
            "mean_slippage_r": (sum(slippage) / len(slippage) if slippage else None),
            "net_pnl_r": (sum(pnl) if pnl else None),
        }

    def grouped(key: str) -> dict:
        values = sorted({str(row.get(key, "") or "unknown") for row in events})
        return {value: aggregate([
            row for row in events if str(row.get(key, "") or "unknown") == value
        ]) for value in values}

    return {
        **aggregate(events),
        "by_symbol": grouped("symbol"),
        "by_regime": grouped("regime"),
    }


def _order_issue(mt5, order_ticket: str) -> tuple[str, dict]:
    """Classe la disparition d'un ordre a partir de l'historique MT5."""
    try:
        history = mt5.history_orders_get(ticket=int(order_ticket)) or []
    except Exception:  # noqa: BLE001
        history = []
    if not history:
        return "unknown", {"broker_state": None, "broker_comment": ""}
    order = max(history, key=lambda item: getattr(item, "time_done", 0) or 0)
    state = int(getattr(order, "state", -1) or -1)
    comment = str(getattr(order, "comment", "") or "")
    if state == int(getattr(mt5, "ORDER_STATE_CANCELED", 2)):
        issue = "canceled"
    elif state == int(getattr(mt5, "ORDER_STATE_EXPIRED", 6)):
        issue = "expired"
    elif "cancel" in comment.lower():
        issue = "canceled"
    elif "expir" in comment.lower():
        issue = "expired"
    else:
        issue = "unknown"
    return issue, {"broker_state": state, "broker_comment": comment}


def _filled_order_snapshot(mt5, order_ticket: str) -> dict | None:
    """Retourne la preuve broker d'un fill disparu avant le tour suivant.

    Une limite peut etre remplie puis cloturee en moins d'une minute. Elle
    n'apparait alors ni dans ``orders_get`` ni dans ``positions_get``. Le seul
    signal fiable restant est l'ordre historique en etat FILLED, avec son
    ``position_id``. Sans ces deux preuves, on ne reconstitue rien.
    """
    try:
        history = mt5.history_orders_get(ticket=int(order_ticket)) or []
    except Exception:  # noqa: BLE001
        return None
    if not history:
        return None
    order = max(history, key=lambda item: getattr(item, "time_done", 0) or 0)
    try:
        state = int(getattr(order, "state", -1))
        filled_state = int(getattr(mt5, "ORDER_STATE_FILLED", 4))
        position_ticket = int(getattr(order, "position_id", 0) or 0)
    except (TypeError, ValueError):
        return None
    if state != filled_state or position_ticket <= 0:
        return None
    try:
        fill_price = float(
            getattr(order, "price_current", 0.0)
            or getattr(order, "price_open", 0.0)
            or 0.0
        )
        sl = float(getattr(order, "sl", 0.0) or 0.0)
        tp = float(getattr(order, "tp", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if fill_price <= 0:
        return None
    return {
        "position_ticket": position_ticket,
        "fill_price": fill_price,
        "sl": sl,
        "tp": tp,
        "broker_state": state,
        "broker_comment": str(getattr(order, "comment", "") or ""),
    }


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
                               pending_path: Path, positions=None,
                               lifecycle_path: Path | None = None) -> dict:
    """Rattache par symbole/sens le contexte pending au ticket de position."""
    report = {"adopted": 0, "pending": 0, "purged": 0,
              "expired": 0, "canceled": 0, "unknown": 0,
              "events_written": 0, "event_failures": 0,
              "repaired": 0, "recovered_filled": 0, "details": []}
    if lifecycle_path is not None:
        repair = _repair_adopted_limit_states(state_path, lifecycle_path)
        report["repaired"] += repair["repaired"]
        report["events_written"] += repair["events_written"]
        report["event_failures"] += repair["event_failures"]
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
        planned = float(template.limit_planned_price or template.entry or 0.0)
        market_reference = float(template.limit_market_reference_price or 0.0)
        realized_saving_r = None
        slippage_r = None
        if template.r > 0 and market_reference > 0 and planned > 0:
            realized_saving_r = ((market_reference - entry) * side) / template.r
            slippage_r = ((entry - planned) * side) / template.r
        state[ticket] = replace(
            template, entry=entry, sl_initial=sl, tp_initial=tp,
            r=abs(entry - sl) if entry and sl else template.r,
            limit_realized_saving_r=realized_saving_r,
            limit_slippage_r=slippage_r,
        )
        if lifecycle_path is not None:
            regime = str(template.context_key).split("|")[2:3]
            written, reason = append_limit_event(lifecycle_path, {
                "event": "filled", "order_ticket": int(key),
                "position_ticket": int(ticket), "symbol": pos.symbol,
                "side": side, "planned_price": planned,
                "market_reference_price": market_reference,
                "fill_price": entry, "r_unit": template.r,
                "target_saving_r": template.limit_target_saving_r,
                "realized_saving_r": realized_saving_r,
                "slippage_r": slippage_r,
                "context": template.context_key,
                "regime": regime[0] if regime else "unknown",
                "asset_class": template.asset_class,
                "mode": template.mode,
            })
            report["events_written"] += int(written)
            report["event_failures"] += int(not written and reason != "DUPLICATE")
            report["details"].append({
                "order_ticket": int(key), "position_ticket": int(ticket),
                "issue": "filled", "event": reason,
                "realized_saving_r": realized_saving_r,
                "slippage_r": slippage_r,
            })
        pending.pop(key, None)
        changed_state = True
        report["adopted"] += 1

    now = datetime.now(timezone.utc)
    if live_orders is not None:
        for key, value in list(pending.items()):
            if key in live_orders:
                continue
            recovered = _filled_order_snapshot(mt5, key)
            if recovered is not None:
                template = TrackedState.from_dict(value["state"])
                position_ticket = str(recovered["position_ticket"])
                planned = float(
                    template.limit_planned_price or template.entry or 0.0)
                market_reference = float(
                    template.limit_market_reference_price or 0.0)
                entry = float(recovered["fill_price"])
                realized_saving_r = None
                slippage_r = None
                if template.r > 0 and market_reference > 0 and planned > 0:
                    realized_saving_r = (
                        (market_reference - entry) * template.side / template.r)
                    slippage_r = (
                        (entry - planned) * template.side / template.r)
                base = state.get(position_ticket, template)
                sl = float(recovered["sl"] or base.sl_initial or 0.0)
                tp = float(recovered["tp"] or base.tp_initial or 0.0)
                restored = replace(
                    base,
                    entry=entry,
                    sl_initial=sl,
                    tp_initial=tp,
                    r=abs(entry - sl) if entry and sl else base.r,
                    limit_order_ticket=int(key),
                    limit_planned_price=planned,
                    limit_market_reference_price=market_reference,
                    limit_realized_saving_r=realized_saving_r,
                    limit_slippage_r=slippage_r,
                )
                event_ok = True
                event_reason = "DISABLED"
                if lifecycle_path is not None:
                    regime = str(template.context_key).split("|")[2:3]
                    written, event_reason = append_limit_event(lifecycle_path, {
                        "event": "filled", "order_ticket": int(key),
                        "position_ticket": int(position_ticket),
                        "symbol": value.get("symbol"),
                        "side": int(value.get("side", 0) or 0),
                        "planned_price": planned,
                        "market_reference_price": market_reference,
                        "fill_price": entry, "r_unit": template.r,
                        "target_saving_r": template.limit_target_saving_r,
                        "realized_saving_r": realized_saving_r,
                        "slippage_r": slippage_r,
                        "context": template.context_key,
                        "regime": regime[0] if regime else "unknown",
                        "asset_class": template.asset_class,
                        "mode": template.mode,
                        "recovered_from_history": True,
                        "broker_state": recovered["broker_state"],
                        "broker_comment": recovered["broker_comment"],
                    })
                    event_ok = written or event_reason == "DUPLICATE"
                    report["events_written"] += int(written)
                    report["event_failures"] += int(not event_ok)
                if event_ok:
                    state[position_ticket] = restored
                    pending.pop(key, None)
                    changed_state = True
                    report["adopted"] += 1
                    report["recovered_filled"] += 1
                    report["details"].append({
                        "order_ticket": int(key),
                        "position_ticket": int(position_ticket),
                        "issue": "filled_from_history",
                        "event": event_reason,
                        "realized_saving_r": realized_saving_r,
                        "slippage_r": slippage_r,
                    })
                continue
            try:
                expiry = datetime.fromisoformat(str(value.get("expires_at", "")))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if now.timestamp() > expiry.timestamp() + 60:
                    issue, broker = _order_issue(mt5, key)
                    template = TrackedState.from_dict(value["state"])
                    event_ok = True
                    event_reason = "DISABLED"
                    if lifecycle_path is not None:
                        regime = str(template.context_key).split("|")[2:3]
                        written, event_reason = append_limit_event(lifecycle_path, {
                            "event": issue, "order_ticket": int(key),
                            "symbol": value.get("symbol"),
                            "side": int(value.get("side", 0) or 0),
                            "planned_price": template.limit_planned_price or template.entry,
                            "market_reference_price": template.limit_market_reference_price,
                            "r_unit": template.r,
                            "target_saving_r": template.limit_target_saving_r,
                            "expires_at": value.get("expires_at"),
                            "context": template.context_key,
                            "regime": regime[0] if regime else "unknown",
                            "asset_class": template.asset_class,
                            "mode": template.mode,
                            **broker,
                        })
                        event_ok = written or event_reason == "DUPLICATE"
                        report["events_written"] += int(written)
                        report["event_failures"] += int(not event_ok)
                    if not event_ok:
                        continue
                    pending.pop(key, None)
                    report["purged"] += 1
                    report[issue] += 1
                    report["details"].append({
                        "order_ticket": int(key), "issue": issue,
                        "event": event_reason, **broker,
                    })
            except (TypeError, ValueError):
                continue

    report["pending"] = len(pending)
    if changed_state:
        save_state(state_path, state)
    _write(pending_path, pending)
    return report
