"""Read-only ledger diagnostics; no imports from execution, MT5 or providers."""

import sqlite3
from pathlib import Path


def ledger_summary(path):
    """No DB creation; missing evidence is not zero observed executions."""
    if not Path(path).exists():
        return {"status": "NOT_STARTED", "intents": 0, "deals": 0}
    try:
        db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            states = dict(db.execute("SELECT state,count(*) FROM intents GROUP BY state"))
            count = db.execute("SELECT count(*) FROM deals").fetchone()[0]
        finally:
            db.close()
        unresolved = sum(states.get(k, 0) for k in ("SUBMITTING", "UNKNOWN"))
        return {"status": "WAIT" if unresolved else "RECORDED", "intents": sum(states.values()),
                "states": states, "unresolved": unresolved, "deals": count,
                "scope": "live_demo entries since activation; no historical backfill",
                "exact_total_cost": False}
    except Exception:
        return {"status": "UNAVAILABLE"}
