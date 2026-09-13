"""Shared contract for the operator-authorized DEMO execution cohort."""

from datetime import datetime, timezone

DEMO_COHORT_SYMBOLS = (
    "USOIL",
    "XAGUSD",
    "COFFEE.fs",
    "BTCUSD",
    "SOL-USD",
    "XAUUSD",
    # Distinct groups retained by the rearmament plan for forward observation.
    # US30 stays excluded by that plan; SWI20's current exact context is BLOCK.
    "HK50",
    "FRA40",
)

DEMO_COHORT_START_UTC = datetime(2026, 9, 10, 14, 55, tzinfo=timezone.utc)
