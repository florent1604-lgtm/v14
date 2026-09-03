"""Pure decision helpers for V14 adaptive micro-baskets.

A micro-basket is two or three positions on one symbol.  It is not a grid:
every reinforcement must improve the executable price by a volatility- and
cost-aware distance, the symbol owns a fixed risk budget, and the whole basket
can be closed when its aggregate advantage is materially given back.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BasketParams:
    """Aggregate exit thresholds expressed in initial-risk units."""

    min_positions: int = 2
    arm_r: float = 0.60
    min_lock_r: float = 0.10
    max_giveback_r: float = 0.45
    min_retention: float = 0.45


@dataclass(frozen=True)
class BasketMember:
    """Current contribution of one ticket to its symbol basket."""

    ticket: str
    fav_r: float
    risk_money: float = 0.0


@dataclass(frozen=True)
class BasketDecision:
    """Pure aggregate decision; sending orders remains the manager's job."""

    should_exit: bool = False
    reason: str = ""
    current_r: float = 0.0
    peak_r: float = 0.0
    floor_r: float = 0.0
    giveback_r: float = 0.0
    members: int = 0


def required_improvement_r(
    *,
    stop_distance: float,
    atr: float = 0.0,
    spread: float = 0.0,
    base_r: float = 0.10,
    atr_multiple: float = 0.25,
    spread_multiple: float = 2.0,
) -> float | None:
    """Minimum price improvement, normalized by the candidate's stop.

    Missing ATR/spread values do not invent a refusal: the deterministic base
    remains active. Invalid negative/non-finite inputs fail closed.
    """
    values = (stop_distance, atr, spread, base_r, atr_multiple, spread_multiple)
    if not all(math.isfinite(float(value)) for value in values):
        return None
    if stop_distance <= 0 or min(atr, spread, base_r, atr_multiple, spread_multiple) < 0:
        return None
    return max(
        base_r,
        atr_multiple * atr / stop_distance,
        spread_multiple * spread / stop_distance,
    )


def decide_basket_exit(
    members: list[BasketMember],
    *,
    previous_peak_r: float = 0.0,
    params: BasketParams | None = None,
) -> BasketDecision:
    """Close an armed basket after a meaningful aggregate giveback.

    The aggregate R is risk-weighted when every member has a measured monetary
    risk. Otherwise equal weights are deliberately used rather than pretending
    that partial risk metadata is exact.
    """
    p = params or BasketParams()
    if len(members) < p.min_positions:
        return BasketDecision(reason="PANIER_INCOMPLET", members=len(members))
    thresholds = (p.arm_r, p.min_lock_r, p.max_giveback_r, p.min_retention)
    if (
        not all(math.isfinite(value) for value in thresholds)
        or p.min_positions < 2
        or p.arm_r < 0
        or p.min_lock_r < 0
        or p.max_giveback_r <= 0
        or not 0 <= p.min_retention <= 1
    ):
        return BasketDecision(reason="PARAMS_PANIER_INVALIDES", members=len(members))
    if not all(math.isfinite(member.fav_r) for member in members):
        return BasketDecision(reason="PANIER_INVALIDE", members=len(members))

    measured = all(
        math.isfinite(member.risk_money) and member.risk_money > 0 for member in members
    )
    weights = [member.risk_money if measured else 1.0 for member in members]
    total_weight = sum(weights)
    current_r = sum(
        member.fav_r * weight for member, weight in zip(members, weights, strict=True)
    ) / total_weight
    peak_r = max(float(previous_peak_r or 0.0), current_r)
    giveback_r = max(0.0, peak_r - current_r)

    if peak_r < p.arm_r:
        return BasketDecision(
            reason="ATTENTE_ARMEMENT_PANIER",
            current_r=current_r,
            peak_r=peak_r,
            giveback_r=giveback_r,
            members=len(members),
        )

    floor_r = max(
        p.min_lock_r,
        peak_r * p.min_retention,
        peak_r - p.max_giveback_r,
    )
    should_exit = current_r <= floor_r
    return BasketDecision(
        should_exit=should_exit,
        reason="AVANTAGE_PANIER_RESTITUE" if should_exit else "AVANTAGE_PANIER_CONSERVE",
        current_r=current_r,
        peak_r=peak_r,
        floor_r=floor_r,
        giveback_r=giveback_r,
        members=len(members),
    )


def load_basket_peaks(path: Path) -> dict[str, float]:
    """Read the tiny peak state fail-soft; malformed entries are ignored."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    peaks: dict[str, float] = {}
    for symbol, value in raw.items():
        try:
            peak = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(peak):
            peaks[str(symbol)] = peak
    return peaks


def save_basket_peaks(path: Path, peaks: dict[str, float]) -> None:
    """Atomically persist active basket peaks."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        str(symbol): float(peak)
        for symbol, peak in peaks.items()
        if math.isfinite(float(peak))
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(clean, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(target)
