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

_REASON = "CONTEXTE_ABSENT_CLOTURE_HISTORIQUE"


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
    report = {"recovered": 0, "scanned": 0, "reason": ""}
    history = getattr(mt5, "history_deals_get", None)
    if not callable(history):
        report["reason"] = "HISTORY_UNAVAILABLE"
        return report

    until = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = until - timedelta(days=max(1, int(lookback_days)))
    try:
        deals = history(since, until + timedelta(minutes=1))
        if deals is None:
            report["reason"] = "HISTORY_UNAVAILABLE"
            return report
        positions = aggregate_mt5_deals(
            deals,
            magic=magic,
            open_position_ids=open_position_ids,
        )
    except Exception as exc:  # noqa: BLE001 - observation fail-closed
        report["reason"] = f"HISTORY_ERROR:{type(exc).__name__}"
        return report

    report["scanned"] = len(positions)
    rejected_path = journal_path.parent / "journal_rejets.ndjson"
    known = _tickets(journal_path) | _tickets(rejected_path)
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
