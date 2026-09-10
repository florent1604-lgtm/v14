"""Shared contract for the operator-authorized DEMO execution cohort."""

from datetime import datetime, timezone

DEMO_COHORT_SYMBOLS = (
    "USOIL",
    "XAGUSD",
    "COFFEE.fs",
    "BTCUSD",
    "SOL-USD",
)

DEMO_COHORT_START_UTC = datetime(2026, 9, 10, 14, 55, tzinfo=timezone.utc)
