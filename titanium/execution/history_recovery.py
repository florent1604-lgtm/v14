"""Récupération append-only des clôtures MT5 invisibles entre deux tours.

Une position peut s'ouvrir et se fermer avant le prochain ``manage_once``.
Son résultat comptable existe alors chez MT5, mais V14 n'a jamais pu capturer
son contexte. La preuve est conservée hors edge, sans fabriquer un ``pnl_r``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from titanium.analysis.reconciliation import aggregate_mt5_deals
from titanium.data.mt5_vendor import decalage_serveur

_REASON = "CONTEXTE_ABSENT_CLOTURE_HISTORIQUE"
_CLOCK_SYMBOLS = ("BTCUSD", "ETHUSD", "EURUSD", "XAUUSD")


def _tickets(path: Path) -> set[str]:
    """Lit les tickets connus d'un NDJSON sans rendre la boucle fragile."""
    if not path.exists():
        return set()
    result: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        try:
            raw = str(json.loads(line).get("ticket", "") or "")
        except (json.JSONDecodeError, AttributeError):
            continue
        if raw:
            result.add(raw.removeprefix("live:"))
    return result


def _records(path: Path) -> list[dict]:
    """Relit les objets valides d'un journal append-only."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            result.append(row)
    return result


def _resolve_quarantines(
    positions,
    *,
    rejected_path: Path,
    journal_path: Path,
    edge_tickets: set[str],
) -> int:
    """Complete une quarantaine seulement avec une comptabilite MT5 prouvee."""
    records = _records(rejected_path)
    resolved = {
        str(row.get("ticket", "")).removeprefix("live:")
        for row in records
        if row.get("event") == "SORTIE_INTROUVABLE_RESOLUE"
    }
    quarantines = {
        str(row.get("ticket", "")).removeprefix("live:"): row
        for row in records
        if row.get("quarantine_recoverable") is True
        and isinstance(row.get("tracked_state"), dict)
    }
    positions_by_id = {row.position_id: row for row in positions}
    candidates = sorted((set(quarantines) & set(positions_by_id)) - resolved)
    if not candidates:
        return 0

    from titanium.execution.position_manager import TrackedState, journaliser_cloture

    count = 0
    for ticket in candidates:
        position = positions_by_id[ticket]
        if not position.accounting_complete or position.entry_deal_count < 1:
            continue
        already_edge = ticket in edge_tickets
        if not already_edge:
            try:
                state = TrackedState.from_dict(quarantines[ticket]["tracked_state"])
            except (KeyError, TypeError, ValueError):
                continue
            fees = position.commission + position.swap + position.fee
            fee_r = (
                abs(fees) / state.risque_devise
                if state.risque_devise > 0 else None
            )
            cost_r = None
            if state.spread_r is not None and fee_r is not None:
                cost_r = abs(float(state.spread_r)) + fee_r
            already_edge = journaliser_cloture(
                state,
                ticket,
                prix_sortie=position.exit_price,
                ts_exit=position.closed_at,
                journal_path=journal_path,
                cost_r=cost_r,
                net_devise=position.net_currency,
                exact_net=True,
            )
        if not already_edge:
            continue
        resolved_at = datetime.now(timezone.utc)
        try:
            missing_since = datetime.fromisoformat(
                str(quarantines[ticket]["tracked_state"][
                    "history_missing_since"
                ]).replace("Z", "+00:00")
            )
            if missing_since.tzinfo is None:
                missing_since = missing_since.replace(tzinfo=timezone.utc)
            resolution_age = max(
                0, int((resolved_at - missing_since).total_seconds()),
            )
        except (KeyError, TypeError, ValueError):
            resolution_age = 0
        resolution = {
            "ticket": f"live:{ticket}",
            "event": "SORTIE_INTROUVABLE_RESOLUE",
            "resolved_at": resolved_at.isoformat(),
            "ts_exit": position.closed_at,
            "history_resolution_age_seconds": resolution_age,
            "deal_in_proven": True,
            "entry_deal_count": position.entry_deal_count,
            "entry_volume": position.entry_volume,
            "exit_volume": position.exit_volume,
            "accounting_complete": True,
            "edge_eligible": True,
            "source": "live",
        }
        try:
            with rejected_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(resolution, ensure_ascii=False) + "\n")
        except OSError:
            continue
        edge_tickets.add(ticket)
        count += 1
    return count


def recover_unobserved_closures(
    mt5,
    *,
    magic: int,
    journal_path: Path,
    open_position_ids=(),
    protected_position_ids=(),
    lookback_days: int = 7,
    now: datetime | None = None,
) -> dict:
    """Met en quarantaine les clôtures MT5 encore absentes des journaux.

    Les tickets protégés restent au gestionnaire normal afin qu'un échec I/O
    ne transforme jamais un trade mesurable en observation sans contexte.
    """
    report = {
        "recovered": 0,
        "scanned": 0,
        "mt5_closed": 0,
        "journal_edge": 0,
        "missing_in_edge": 0,
        "missing_in_edge_rate": 0.0,
        "reason": "",
    }
    history = getattr(mt5, "history_deals_get", None)
    if not callable(history):
        report["reason"] = "HISTORY_UNAVAILABLE"
        return report

    until = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = until - timedelta(days=max(1, int(lookback_days)))
    try:
        offset_seconds = decalage_serveur(mt5, _CLOCK_SYMBOLS)
        # Ce terminal compare les bornes aux epochs encodes dans l'heure du
        # serveur. La meme mesure transforme donc les bornes UTC et les epochs
        # rendus. Le filtre UTC ci-dessous reste l'autorite finale.
        offset = timedelta(seconds=offset_seconds)
        deals = history(since + offset, until + offset + timedelta(minutes=1))
        if deals is None:
            report["reason"] = "HISTORY_UNAVAILABLE"
            return report
        positions = aggregate_mt5_deals(
            deals,
            magic=magic,
            open_position_ids=open_position_ids,
            server_offset_seconds=offset_seconds,
        )
        window_end = until + timedelta(minutes=1)
        positions = [
            row for row in positions
            if since <= datetime.fromisoformat(row.closed_at) <= window_end
        ]
    except Exception as exc:  # noqa: BLE001 - observation fail-closed
        report["reason"] = f"HISTORY_ERROR:{type(exc).__name__}"
        return report

    report["scanned"] = len(positions)
    report["mt5_closed"] = len(positions)
    rejected_path = journal_path.parent / "journal_rejets.ndjson"
    edge_tickets = _tickets(journal_path)
    recoverable_exists = any(
        row.get("quarantine_recoverable") is True
        for row in _records(rejected_path)
    )
    if recoverable_exists:
        report["resolved_quarantines"] = _resolve_quarantines(
            positions,
            rejected_path=rejected_path,
            journal_path=journal_path,
            edge_tickets=edge_tickets,
        )
    closed_tickets = {row.position_id for row in positions}
    edge_matches = closed_tickets & edge_tickets
    missing_edge = closed_tickets - edge_tickets
    report["journal_edge"] = len(edge_matches)
    report["missing_in_edge"] = len(missing_edge)
    if positions:
        report["missing_in_edge_rate"] = round(
            len(missing_edge) / len(positions), 6,
        )

    known = edge_tickets | _tickets(rejected_path)
    protected = {str(value) for value in protected_position_ids}
    missing = [
        row for row in positions
        if row.position_id not in known and row.position_id not in protected
    ]
    if not missing:
        return report

    try:
        rejected_path.parent.mkdir(parents=True, exist_ok=True)
        with rejected_path.open("a", encoding="utf-8") as handle:
            for row in missing:
                handle.write(json.dumps({
                    "ticket": f"live:{row.position_id}",
                    "symbol": row.symbol,
                    "reason": _REASON,
                    "ts_open": row.opened_at,
                    "ts_exit": row.closed_at,
                    "net_currency": row.net_currency,
                    "profit": row.profit,
                    "commission": row.commission,
                    "swap": row.swap,
                    "fee": row.fee,
                    "close_reason": row.close_reason,
                    "exit_class": row.exit_class,
                    "manual_intervention": row.manual_intervention,
                    "source": "live",
                    "magic": int(magic),
                    "horloge": "utc",
                    "recovered_from_mt5_history": True,
                    "coverage_only": True,
                    "edge_eligible": False,
                }, ensure_ascii=False) + "\n")
                report["recovered"] += 1
    except OSError as exc:
        report["reason"] = f"JOURNAL_ERROR:{type(exc).__name__}"
    return report
