"""Deterministic execution research layer.

This package is deliberately disconnected from MT5 and every live executor.
It accepts immutable market observations and produces simulated order events.
"""

from titanium.execution_sim.alpha import AlphaSource, LegacyTradeAlphaAdapter, StaticAlpha
from titanium.execution_sim.config import load_config
from titanium.execution_sim.engine import BacktestExecutionEngine
from titanium.execution_sim.policies import POLICY_REGISTRY, get_policy

__all__ = [
    "AlphaSource",
    "BacktestExecutionEngine",
    "LegacyTradeAlphaAdapter",
    "POLICY_REGISTRY",
    "StaticAlpha",
    "get_policy",
    "load_config",
]
