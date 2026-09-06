"""Read-only accounting of cumulative raw ENTER evaluations, not fills."""

from __future__ import annotations


def entry_balance(stats: dict) -> dict:
    """Expose gaps instead of inventing orders from ENTER minus refusals.

    All counters must share the same process lifetime. Coalesced timeframes
    are terminal non-execution outcomes; pre-ENTER portability is excluded.
    Interrupted tours can legitimately leave an unresolved gap.
    """
    tunnel = stats.get("tunnel") or {}
    post = tunnel.get("post_enter_refusal") or {}
    counters = [stats.get(k) for k in ("enter", "envoyes", "simules")]
    values = counters + list(post.values())
    valid = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)
    if not valid:
        return {
            "status": "UNAVAILABLE",
            "unaccounted": None,
            "unit": "raw_ENTER_evaluations",
            "fills": None,
        }
    enter, sent, simulated = counters
    refused = sum(post.values())
    gap = enter - sent - simulated - refused
    return {
        "status": "BALANCED" if gap == 0 else ("INCOMPLETE" if gap > 0 else "OVERCOUNTED"),
        "unit": "raw_ENTER_evaluations",
        "enter": enter,
        "orders_sent": sent,
        "simulated": simulated,
        "non_executed": refused,
        "unaccounted": gap,
        "coalesced": post.get("MULTITIMEFRAME_COALESCED", 0),
        "fills": None,
    }
