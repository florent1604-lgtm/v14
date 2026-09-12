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
        # Parametres par defaut de la famille adaptative. Ils sont ici, et non
        # caches dans le code, pour qu'un balayage soit un fichier de config
        # versionne et non une modification du moteur.
        "adapt": {
            # Valeurs partagees, injectees dans chaque section de technique par
            # ``runner._policy_config``. La reference de spread absolue est un
            # parametre, pas une constante cachee : sans elle
            # ``adapt_spread_expansion`` echoue ferme au lieu d'inventer un seuil.
            "_shared": {"baseline_spread_bps": 3.0, "max_inventory": 10.0},
            "adapt_spread_budget": {"spread_budget_bps": 6.0, "ttl_seconds": 30.0},
            "adapt_volatility_scale": {
                "offset_k": 0.35,
                "max_offset_bps": 8.0,
                "ttl_seconds": 30.0,
            },
            "adapt_depth_guard": {"min_depth_ratio": 1.5, "ttl_seconds": 30.0},
            "adapt_urgency_ladder": {
                "high_urgency": 0.66,
                "medium_urgency": 0.33,
                "default_urgency": 0.5,
                "horizon_reference_ms": 60_000.0,
                "ttl_seconds": 30.0,
            },
            "adapt_deadline_ladder": {"slices": 3, "horizon_ms": 20_000},
            "adapt_microprice_anchor": {"ttl_seconds": 30.0},
            "adapt_inventory_skew": {"skew_bps": 6.0, "max_inventory": 10.0, "ttl_seconds": 30.0},
            "adapt_cost_benefit": {
                "horizon_ms": 5_000,
                "adverse_vol_factor": 0.5,
                "ttl_seconds": 30.0,
            },
            "adapt_volatility_abort": {"warn_bps": 8.0, "hard_bps": 25.0, "ttl_seconds": 30.0},
            "adapt_depth_slice": {
                "depth_fraction": 0.25,
                "interval_ms": 800,
                "max_slices": 6,
                "min_slice": 0.01,
            },
            "adapt_improve_touch": {"improve_ticks": 1, "ttl_seconds": 30.0},
            "adapt_join_touch": {"ttl_seconds": 30.0},
            # Remplace adapt_spread_expansion : son seuil (3,0 x 2,0 = 6,0 bps)
            # etait identique a ``spread_budget_bps``, donc les deux techniques
            # etaient indiscernables au bit pres sur 864/864 scenarios.
            "adapt_spread_participation": {
                "expansion_factor": 2.0,
                "max_slices": 5,
                "interval_ms": 700,
            },
            "adapt_size_patience": {
                "small_size": 2.0,
                "depth_fraction": 0.25,
                "interval_ms": 800,
                "max_slices": 6,
                "min_slice": 0.01,
            },
            "adapt_midpoint_aggressive": {"wide_ticks": 4.0, "ttl_seconds": 30.0},
            "adapt_ladder_maker_taker": {"slices": 2, "horizon_ms": 10_000},
            "adapt_selector": {
                "min_depth_ratio": 1.5,
                "high_urgency": 0.6,
                "abort_volatility_bps": 25.0,
            },
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
