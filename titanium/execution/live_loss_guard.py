"""Coupe-circuit de pertes pour les nouvelles entrées DEMO.

Le journal live est la preuve comptable de référence. Une preuve absente ou
illisible interdit une nouvelle entrée, sans empêcher la gestion protectrice
des positions déjà ouvertes.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from titanium.edge import PNL_R_MAX

DAILY_LOSS_LIMIT_R = 2.0
ROLLING_7D_LOSS_LIMIT_R = 6.0


@dataclass(frozen=True)
class LiveLossVerdict:
    """Décision reproductible du coupe-circuit de pertes live."""

    action: str
    reason: str
    daily_trades: int = 0
    daily_net_r: float = 0.0
    rolling_trades: int = 0
    rolling_net_r: float = 0.0

    def to_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


def _invalid() -> LiveLossVerdict:
    return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_JOURNAL_INVALID")


def _parse_closed_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def evaluate_live_loss_guard(
    journal_path: str | Path,
    *,
    account: str,
    now: datetime | None = None,
) -> LiveLossVerdict:
    """Autorise ou bloque les nouvelles entrées selon le PnL live en R.

    Les limites sont de -2 R sur le jour UTC et -6 R sur sept jours glissants.
    Les doublons sont résolus par l'état le plus récent du journal. Toute
    donnée live pertinente qui n'est pas une preuve UTC nette fait attendre.
    """

    path = Path(journal_path)
    if not path.is_file():
        return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_JOURNAL_MISSING")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return _invalid()
    current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(days=7)
    expected_account = str(account)
    relevant_account_seen = False
    by_ticket: dict[str, tuple[datetime, float]] = {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _invalid()
    if not lines:
        return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_JOURNAL_MISSING")

    for raw in lines:
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            return _invalid()
        if not isinstance(row, dict):
            return _invalid()
        if row.get("source") != "live" or str(row.get("account", "")) != expected_account:
            continue

        relevant_account_seen = True
        closed_at = _parse_closed_at(row.get("closed_at"))
        if closed_at is None or closed_at > current:
            return _invalid()
        if closed_at < cutoff:
            continue

        ticket = row.get("ticket")
        try:
            pnl_r = float(row.get("pnl_r"))
        except (TypeError, ValueError):
            return _invalid()
        if (
            row.get("horloge") != "utc"
            or row.get("exact_net") is not True
            or not isinstance(ticket, str)
            or not ticket.strip()
            or not math.isfinite(pnl_r)
            or abs(pnl_r) > PNL_R_MAX
        ):
            return _invalid()
        by_ticket[ticket.strip()] = (closed_at, pnl_r)

    if not relevant_account_seen:
        return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_ACCOUNT_HISTORY_MISSING")

    rolling = [item for item in by_ticket.values() if cutoff <= item[0] <= current]
    daily = [item for item in rolling if item[0].date() == current.date()]
    daily_net_r = round(sum(pnl_r for _, pnl_r in daily), 4)
    rolling_net_r = round(sum(pnl_r for _, pnl_r in rolling), 4)
    fields = {
        "daily_trades": len(daily),
        "daily_net_r": daily_net_r,
        "rolling_trades": len(rolling),
        "rolling_net_r": rolling_net_r,
    }
    if daily_net_r <= -DAILY_LOSS_LIMIT_R:
        return LiveLossVerdict(action="BLOCK", reason="DAILY_LOSS_LIMIT", **fields)
    if rolling_net_r <= -ROLLING_7D_LOSS_LIMIT_R:
        return LiveLossVerdict(action="BLOCK", reason="ROLLING_7D_LOSS_LIMIT", **fields)
    return LiveLossVerdict(action="ALLOW", reason="WITHIN_LOSS_LIMITS", **fields)
