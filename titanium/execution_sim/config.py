from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class DryRunSafetyError(ValueError):
    """Raised whenever a simulated execution configuration requests live access."""


DEFAULT_CONFIG: dict[str, Any] = {
    "execution": {
        "enabled": True,
        "live_enabled": False,
        "policy": "adaptive",
        "fees": {"maker_bps": 1.0, "taker_bps": 3.0},
        "latency": {
            "market_data_ms": 0,
            "decision_ms": 0,
            "submit_ms": 5,
            "acknowledge_ms": 5,
            "cancel_ms": 10,
            "hedge_ms": 10,
        },
        "spread": {"source": "orderbook_or_synthetic", "fallback_bps": 4.0},
        "slippage": {
            "model": "orderbook_or_volume_volatility",
            "base_bps": 1.0,
            "volatility_multiplier": 0.10,
            "participation_multiplier": 5.0,
        },
        "passive": {
            "offset_ticks": 0,
            "max_age_ms": 30_000,
            "refresh_threshold_ticks": 2,
            "queue_model": "central",
        },
        "post_only": {"cross_behavior": "reject"},
        "iceberg": {"visible_ratio": 0.20, "replenish_delay_ms": 100},
        "twap": {"duration_seconds": 60, "slices": 5, "catchup_policy": "cancel"},
        "vwap": {
            "profile_lookback_sessions": 5,
            "max_participation": 0.10,
            "catchup_policy": "cancel",
        },
        "pov": {"participation_rate": 0.05, "min_slice": 0.01, "max_slice": 1.0},
        "adaptive": {
            "deadline_seconds": 30,
            "stages": ["post_only", "passive", "midpoint", "aggressive_limit", "ioc"],
            "max_reprices": 4,
        },
    },
    "risk": {
        "max_order_size": 10.0,
        "max_gross_exposure": 100_000.0,
        "max_net_exposure": 50_000.0,
        "max_inventory": 10.0,
        "max_open_orders": 20,
        "max_daily_loss": 1_000.0,
        "max_drawdown": 2_000.0,
        "max_slippage_bps": 25.0,
        "max_leg_imbalance": 2.0,
        "max_hedge_delay_ms": 500,
        "kill_switch": False,
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    override: dict[str, Any] = {}
    if path is not None:
        override = json.loads(Path(path).read_text(encoding="utf-8"))
    config = _merge(DEFAULT_CONFIG, override)
    execution = config.get("execution") or {}
    if execution.get("live_enabled") is not False:
        raise DryRunSafetyError("execution.live_enabled must remain false in execution_sim")
    if not execution.get("enabled", False):
        raise ValueError("execution.enabled must be true for a simulation")
    return config
