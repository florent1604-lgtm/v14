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


def _server_iso_to_utc(value: str, offset_seconds: int) -> str:
    """Ramene l'ISO encode en heure serveur vers le vrai UTC."""
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (
            parsed.astimezone(timezone.utc)
            - timedelta(seconds=int(offset_seconds))
        ).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


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
        # Les epochs de ce terminal sont encodes en heure serveur (UTC+3
        # observe), bien que l'API attende des bornes UTC. Une marge symetrique
        # evite donc de perdre les clotures recentes; le filtre UTC exact juste
        # apres retire ce qui deborde reellement de la fenetre.
        deals = history(since - timedelta(days=1), until + timedelta(days=1))
        if deals is None:
            report["reason"] = "HISTORY_UNAVAILABLE"
            return report
        positions = aggregate_mt5_deals(
            deals,
            magic=magic,
            open_position_ids=open_position_ids,
        )
        symbols = tuple({row.symbol for row in positions if row.symbol})
        offset_seconds = decalage_serveur(
            mt5,
            symbols + _CLOCK_SYMBOLS,
        )
        window_end = until + timedelta(minutes=1)
        positions = [
            row for row in positions
            if since <= datetime.fromisoformat(
                _server_iso_to_utc(row.closed_at, offset_seconds),
            ) <= window_end
        ]
    except Exception as exc:  # noqa: BLE001 - observation fail-closed
        report["reason"] = f"HISTORY_ERROR:{type(exc).__name__}"
        return report

    report["scanned"] = len(positions)
    report["mt5_closed"] = len(positions)
    rejected_path = journal_path.parent / "journal_rejets.ndjson"
    edge_tickets = _tickets(journal_path)
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
                    "ts_open": _server_iso_to_utc(
                        row.opened_at, offset_seconds),
                    "ts_exit": _server_iso_to_utc(
                        row.closed_at, offset_seconds),
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
