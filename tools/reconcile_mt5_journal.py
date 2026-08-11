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
    from titanium.data.mt5_vendor import account_snapshot, mt5_session
    from titanium.edge import TradeJournal

    now = datetime.now(timezone.utc)
    until = args.until or now
    since = args.since or (until - timedelta(days=max(1, args.days)))
    if since >= until:
        parser.error("--since doit être antérieur à --until")
    with mt5_session():
        account = account_snapshot()
        deals = list(
            mt5.history_deals_get(since, until + timedelta(minutes=1)) or ()
        )
        open_ids = {
            str(int(getattr(position, "ticket", 0) or 0))
            for position in (mt5.positions_get() or ())
        }

    positions = aggregate_mt5_deals(
        deals,
        magic=args.magic,
        open_position_ids=open_ids,
    )
    journal = TradeJournal(ROOT / "results" / "trades.ndjson").read_all()
    report = {
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "account": _masked(account.login),
        "magic": args.magic,
        **reconcile(positions, journal),
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
