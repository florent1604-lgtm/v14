r"""Rapport read-only MT5 ↔ journal V14.

    .venv\Scripts\python.exe tools\reconcile_mt5_journal.py
    .venv\Scripts\python.exe tools\reconcile_mt5_journal.py --days 30 --strict

Le script ne répare et n'importe rien. Il écrit uniquement un rapport JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _masked(login) -> str:
    text = str(login or "")
    return ("***" + text[-4:]) if text else ""


def _utc_datetime(value: str) -> datetime:
    """Parse une date ISO et la normalise en UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _history_bounds(
    since: datetime,
    until: datetime,
    server_offset_seconds: int,
) -> tuple[datetime, datetime]:
    """Traduit la fenêtre UTC dans l'horloge attendue par ce terminal MT5."""
    offset = timedelta(seconds=int(server_offset_seconds))
    return since + offset, until + offset + timedelta(minutes=1)


def _positions_in_window(positions, since: datetime, until: datetime) -> list:
    """Filtre les positions après conversion canonique de leur clôture en UTC."""
    window_end = until + timedelta(minutes=1)
    return [
        row for row in positions
        if since <= datetime.fromisoformat(row.closed_at) <= window_end
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--since",
        type=_utc_datetime,
        help="début ISO inclusif, par exemple 2026-08-07T15:00:00Z",
    )
    parser.add_argument(
        "--until",
        type=_utc_datetime,
        help="fin ISO inclusive (UTC si aucun fuseau n'est indiqué)",
    )
    parser.add_argument("--magic", type=int, default=14_000)
    parser.add_argument(
        "--output",
        default=str(ROOT / "results" / "reconciliation_mt5.json"),
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    import MetaTrader5 as mt5  # noqa: N813

    from titanium.analysis.reconciliation import aggregate_mt5_deals, reconcile
    from titanium.data.mt5_vendor import (
        account_snapshot,
        decalage_serveur,
        mt5_session,
    )
    from titanium.edge import TradeJournal

    now = datetime.now(timezone.utc)
    until = args.until or now
    since = args.since or (until - timedelta(days=max(1, args.days)))
    if since >= until:
        parser.error("--since doit être antérieur à --until")
    with mt5_session():
        account = account_snapshot()
        server_offset_seconds = decalage_serveur(
            mt5,
            ("BTCUSD", "ETHUSD", "EURUSD", "XAUUSD"),
        )
        history_since, history_until = _history_bounds(
            since,
            until,
            server_offset_seconds,
        )
        deals = list(
            mt5.history_deals_get(history_since, history_until) or ()
        )
        open_ids = {
            str(int(getattr(position, "ticket", 0) or 0))
            for position in (mt5.positions_get() or ())
        }

    positions = aggregate_mt5_deals(
        deals,
        magic=args.magic,
        open_position_ids=open_ids,
        server_offset_seconds=server_offset_seconds,
    )
    positions = _positions_in_window(positions, since, until)
    journal = TradeJournal(ROOT / "results" / "trades.ndjson").read_all()
    rejected_path = ROOT / "results" / "journal_rejets.ndjson"
    rejections = []
    if rejected_path.exists():
        for line in rejected_path.read_text(encoding="utf-8").splitlines():
            try:
                rejections.append(json.loads(line))
            except (json.JSONDecodeError, AttributeError):
                continue
    report = {
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "account": _masked(account.login),
        "magic": args.magic,
        **reconcile(positions, journal, rejections),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in report.items()
                      if key != "positions"}, ensure_ascii=False, indent=2))
    print(f"rapport: {output}")
    return 2 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
