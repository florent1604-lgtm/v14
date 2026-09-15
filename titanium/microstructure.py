"""Microstructure multi-place pour les actifs crypto suivis par V14.

Le module ne contacte jamais le reseau. Il normalise des instantanes produits
par le collecteur public, les relit de facon atomique et rend un veto prudent.
Une donnee absente ou perimee reste ``UNKNOWN`` : elle ne devient jamais une
preuve artificielle en faveur ou en defaveur d'une entree.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "v14.microstructure.v1"
# One collection begins every five seconds. The oldest of three venues can be
# another 1-3 seconds behind, so three full cycles cover the measured p90/max
# without accepting a stopped collector indefinitely.
COLLECTOR_INTERVAL_MS = 5_000.0
VENUE_FRESHNESS_MS = COLLECTOR_INTERVAL_MS + 3_000.0
FRESHNESS_MS = 3 * COLLECTOR_INTERVAL_MS
TRADE_WINDOW_MS = 60_000.0
MIN_CONFIRMING_VENUES = 2
ADVERSE_PRESSURE = 0.35
SYMBOL_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


@dataclass(frozen=True)
class VenueSnapshot:
    venue: str
    symbol: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    trades: tuple[tuple[float, str, float, float], ...]
    event_ms: float
    received_ms: float


@dataclass(frozen=True)
class MicrostructureGate:
    action: str
    reason: str
    directional_pressure: float = 0.0


def _finite_positive(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def _imbalance(buy: float, sell: float) -> float:
    total = buy + sell
    return (buy - sell) / total if total > 0.0 else 0.0


def _wall(levels: list[tuple[float, float]], mid: float) -> tuple[float, float]:
    if not levels:
        return 0.0, 0.0
    notionals = [(price, price * quantity) for price, quantity in levels]
    price, notional = max(notionals, key=lambda row: row[1])
    median = statistics.median(value for _, value in notionals)
    distance = abs(price - mid) / mid * 10_000.0
    return distance, notional / median if median > 0.0 else 0.0


def aggregate_snapshots(snapshots: Iterable[VenueSnapshot], *,
                        now_ms: float | None = None) -> dict:
    """Agrege plusieurs carnets spot sans melanger des donnees invalides."""
    now = float(now_ms if now_ms is not None else time.time() * 1000.0)
    valid: list[tuple[VenueSnapshot, float, float, float]] = []
    seen: set[str] = set()
    for venue in snapshots:
        received = float(venue.received_ms)
        event = float(venue.event_ms)
        if (not math.isfinite(received) or not math.isfinite(event)
                or not -1_000.0 <= now - received <= VENUE_FRESHNESS_MS
                or not -1_000.0 <= now - event <= VENUE_FRESHNESS_MS
                or venue.venue in seen):
            continue
        if valid and venue.symbol != valid[0][0].symbol:
            raise ValueError("mixed symbols in microstructure snapshot")
        bids = [(float(p), float(q)) for p, q in venue.bids
                if _finite_positive(p) and _finite_positive(q)]
        asks = [(float(p), float(q)) for p, q in venue.asks
                if _finite_positive(p) and _finite_positive(q)]
        if not bids or not asks:
            continue
        best_bid = max(p for p, _ in bids)
        best_ask = min(p for p, _ in asks)
        if best_bid >= best_ask:
            continue
        mid = (best_bid + best_ask) / 2.0
        valid.append((venue, best_bid, best_ask, mid))
        seen.add(venue.venue)
    if not valid:
        raise ValueError("aucun carnet exploitable")

    symbol = valid[0][0].symbol
    reference_mid = statistics.median(row[3] for row in valid)
    bid_10 = ask_10 = bid_25 = ask_25 = 0.0
    buy_flow = sell_flow = 0.0
    all_bids: list[tuple[float, float]] = []
    all_asks: list[tuple[float, float]] = []
    spreads = []
    event_ms = min(float(row[0].event_ms) for row in valid)
    received_ms = min(float(row[0].received_ms) for row in valid)
    sources = []

    for venue, best_bid, best_ask, mid in valid:
        sources.append(venue.venue)
        spreads.append((best_ask - best_bid) / mid * 10_000.0)
        for price, quantity in venue.bids:
            p, q = float(price), float(quantity)
            if not (_finite_positive(p) and _finite_positive(q)):
                continue
            distance = (mid - p) / mid * 10_000.0
            if 0.0 <= distance <= 25.0:
                all_bids.append((p, q))
                bid_25 += p * q
                if distance <= 10.0:
                    bid_10 += p * q
        for price, quantity in venue.asks:
            p, q = float(price), float(quantity)
            if not (_finite_positive(p) and _finite_positive(q)):
                continue
            distance = (p - mid) / mid * 10_000.0
            if 0.0 <= distance <= 25.0:
                all_asks.append((p, q))
                ask_25 += p * q
                if distance <= 10.0:
                    ask_10 += p * q
        for trade_ms, side, quantity, price in venue.trades:
            if not (now - TRADE_WINDOW_MS <= float(trade_ms) <= now + 1_000.0):
                continue
            notional = float(quantity) * float(price)
            if not math.isfinite(notional) or notional <= 0.0:
                continue
            if str(side).lower() == "buy":
                buy_flow += notional
            elif str(side).lower() == "sell":
                sell_flow += notional

    bid_wall_bps, bid_wall_ratio = _wall(all_bids, reference_mid)
    ask_wall_bps, ask_wall_ratio = _wall(all_asks, reference_mid)
    age_ms = max(0.0, now - received_ms, now - event_ms)
    return {
        "schema": SCHEMA,
        "symbol": symbol,
        "event_ms": event_ms,
        "received_ms": received_ms,
        "age_ms": age_ms,
        "fresh": age_ms <= FRESHNESS_MS,
        "venue_count": len(valid),
        "venues": sorted(set(sources)),
        "source_times": {row[0].venue: {"event_ms": row[0].event_ms,
                                       "received_ms": row[0].received_ms} for row in valid},
        "mid": reference_mid,
        "spread_bps": statistics.median(spreads),
        "depth_imbalance_10bps": _imbalance(bid_10, ask_10),
        "depth_imbalance_25bps": _imbalance(bid_25, ask_25),
        "taker_imbalance_recent": _imbalance(buy_flow, sell_flow),
        "taker_buy_usd_recent": buy_flow,
        "taker_sell_usd_recent": sell_flow,
        "cvd_usd_recent": buy_flow - sell_flow,
        "bid_wall_distance_bps": bid_wall_bps,
        "ask_wall_distance_bps": ask_wall_bps,
        "bid_wall_ratio": bid_wall_ratio,
        "ask_wall_ratio": ask_wall_ratio,
    }


def microstructure_gate(snapshot: dict | None, *, side: int) -> MicrostructureGate:
    """Bloque seulement une opposition confirmee par profondeur ET flux."""
    if side not in (-1, 1):
        return MicrostructureGate("UNKNOWN", "sens invalide")
    if not isinstance(snapshot, dict) or not snapshot.get("fresh"):
        return MicrostructureGate("UNKNOWN", "microstructure absente ou perimee")
    if int(snapshot.get("venue_count", 0) or 0) < MIN_CONFIRMING_VENUES:
        return MicrostructureGate("UNKNOWN", "moins de deux places fraiches")
    try:
        depth = float(snapshot["depth_imbalance_10bps"])
        flow = float(snapshot["taker_imbalance_recent"])
    except (KeyError, TypeError, ValueError):
        return MicrostructureGate("UNKNOWN", "mesures incompletes")
    if not (math.isfinite(depth) and math.isfinite(flow)):
        return MicrostructureGate("UNKNOWN", "mesures non finies")
    directional = min(side * depth, side * flow)
    if side * depth <= -ADVERSE_PRESSURE and side * flow <= -ADVERSE_PRESSURE:
        return MicrostructureGate(
            "BLOCK",
            f"pression adverse confirmee depth={depth:+.2f} flow={flow:+.2f}",
            directional,
        )
    return MicrostructureGate(
        "ALLOW",
        f"pas d opposition conjointe depth={depth:+.2f} flow={flow:+.2f}",
        directional,
    )


def _market_symbol(mt5_symbol: str) -> str | None:
    normalized = "".join(char for char in str(mt5_symbol).upper() if char.isalnum())
    return next((market for base, market in SYMBOL_MAP.items()
                 if normalized.startswith(base)), None)


def attach_live_microstructure(mt5_symbol: str, features: dict, *, root: Path,
                               now_ms: float | None = None,
                               max_age_ms: float = FRESHNESS_MS) -> dict | None:
    """Joint un instantane frais aux features et le place en tete du brief LLM."""
    market_symbol = _market_symbol(mt5_symbol)
    if market_symbol is None:
        return None
    try:
        path = Path(root) / "results" / "microstructure" / f"{market_symbol}.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        now = float(now_ms if now_ms is not None else time.time() * 1000.0)
        received = float(snapshot["received_ms"])
        event = float(snapshot["event_ms"])
        age = max(0.0, now - received, now - event)
        if (snapshot.get("schema") != SCHEMA
                or snapshot.get("symbol") != market_symbol
                or not snapshot.get("fresh")
                or not math.isfinite(received)
                or not math.isfinite(event)
                or received > now + 1_000.0
                or event > now + 1_000.0
                or age > float(max_age_ms)):
            return None
        numeric = {
            "micro_depth_imbalance_10bps": float(snapshot["depth_imbalance_10bps"]),
            "micro_taker_imbalance_recent": float(snapshot["taker_imbalance_recent"]),
            "micro_spread_bps": float(snapshot["spread_bps"]),
            "micro_bid_wall_bps": float(snapshot["bid_wall_distance_bps"]),
            "micro_ask_wall_bps": float(snapshot["ask_wall_distance_bps"]),
            "micro_venues": float(snapshot["venue_count"]),
            "micro_age_ms": age,
        }
        if not all(math.isfinite(value) for value in numeric.values()):
            return None
        trace = features.setdefault("_trace", {})
        previous = dict(trace.get("indicators") or {})
        trace["indicators"] = {**numeric, **previous}
        trace["microstructure"] = snapshot
        features["microstructure"] = snapshot
        return snapshot
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def entry_microstructure_guard(mt5_symbol: str, *, side: int, root: Path,
                               now_ms: float | None = None) -> MicrostructureGate:
    """Final local-file check, after deliberation. Required for mapped BTC/ETH only.

    No fake quorum for unsupported instruments; those keep their existing gates.
    UNKNOWN on a supported instrument means WAIT, never implicit permission.
    """
    if _market_symbol(mt5_symbol) is None:
        return MicrostructureGate("ALLOW", "source crypto non applicable")
    snapshot = attach_live_microstructure(mt5_symbol, {}, root=root, now_ms=now_ms)
    return microstructure_gate(snapshot, side=side)
