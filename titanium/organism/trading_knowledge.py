"""Versioned decision playbook; conditional hypotheses, never promised edge."""

from __future__ import annotations

import math

KNOWLEDGE_VERSION = "v14-trading-playbook-2"
_STRATEGIES = {
    "trend_pullback": ("trend", "HTF alignment, pullback holds, renewed momentum",
                       "structure break or adverse flow; avoid chasing extension"),
    "breakout_retest": ("compression then expansion", "closed break, retest, participation",
                        "return into range or thin book; price move alone insufficient"),
    "range_reversion": ("stable range", "range edge, rejection, no active macro shock",
                        "trend acceleration or failed mean return; no averaging losses"),
    "liquidity_sweep": ("failed breakout", "sweep then reclaim plus independent confirmation",
                        "continued acceptance beyond swept level; a wick alone is insufficient"),
    "event_repricing": ("new information", "dated primary release and measurable market reaction",
                        "missing publication time, spread spike, or conflicting releases"),
    "relative_value": ("stable cross-asset relation", "aligned returns, stationary spread, cost model",
                       "correlation instability; correlated returns are not causality"),
    "carry_context": ("slow macro regime", "rates, funding/swap and instrument-specific costs",
                      "policy shock or financing dominates expected return"),
}
_BY_CLASS = {
    "crypto": ("trend_pullback", "breakout_retest", "liquidity_sweep", "range_reversion"),
    "fx": ("trend_pullback", "range_reversion", "event_repricing", "carry_context"),
    "metaux": ("trend_pullback", "breakout_retest", "event_repricing"),
    "energie": ("trend_pullback", "range_reversion", "event_repricing"),
    "indices": ("trend_pullback", "breakout_retest", "event_repricing"),
    "actions": ("trend_pullback", "breakout_retest", "event_repricing"),
    "agricole": ("trend_pullback", "range_reversion", "event_repricing"),
}
_CONTEXT = {
    "crypto": "Broker ticks execute CFDs; venue spot books inform flow, not broker fills. Funding/OI require derivatives data.",
    "fx": "Central-bank rates and dated macro surprises; ECB reference rates are daily, not execution prices.",
    "metaux": "Real rates/USD and CFTC positioning; weekly COT cannot time a scalp.",
    "energie": "EIA stocks/releases, curve and contract rollover; spot, futures and CFDs differ.",
    "indices": "Cash/futures session, breadth, rates and scheduled releases; account for contract basis.",
    "actions": "Issuer filings, earnings calendar and corporate actions; no invented fundamentals.",
    "agricole": "Dated crop reports, weather and seasonality; verify contract month, rollover and broker session. Seasonal patterns alone do not time an entry.",
}


def knowledge_for(symbol: str, asset_class: str = "") -> dict:
    if not asset_class:
        # Meme resolution que les moteurs : les groupes du courtier couvrent
        # les actifs absents de la petite liste historique (FX, futures, crypto...).
        from titanium.edge import asset_class_of
        asset_class = asset_class_of(symbol)
    asset_class = str(asset_class).strip().lower()
    known = asset_class in _BY_CLASS
    strategies = _BY_CLASS.get(asset_class, ("trend_pullback", "range_reversion"))
    return {
        "version": KNOWLEDGE_VERSION,
        "asset_class": asset_class or "unknown",
        "classification_status": "KNOWN" if known else "UNKNOWN",
        "strategies": [{"id": key, "regime": _STRATEGIES[key][0],
                        "requires": _STRATEGIES[key][1], "invalidate": _STRATEGIES[key][2]}
                       for key in strategies],
        "market_context": _CONTEXT.get(asset_class, "Instrument mapping and session must be known."),
        "decision": "Choose among supplied candidates. State the supporting evidence and invalidation; missing data stays unknown. Unknown instrument classification requires WAIT.",
        "learning": "Evaluate reconciled net R, sample count, drawdown, costs and out-of-sample stability by asset/regime/horizon.",
        "limits": "Confidence is not a calibrated win probability. No guaranteed profit. Do not change SL or increase risk to recover losses.",
    }


def compact_observations(values: dict) -> dict[str, float]:
    """Keep computed evidence, not hundreds of redundant indicator values."""
    wanted = (
        "execution_spread_stop_pct", "edge_samples", "edge_expectancy_r", "edge_profit_factor",
        "micro_depth_imbalance_10bps", "micro_taker_imbalance_recent", "micro_spread_bps",
        "micro_venues", "micro_age_ms", "jepa_available", "jepa_vol_ratio", "jepa_impulse_r",
        "ltf_adx", "ltf_atr_14_pct", "ltf_rsi_14", "ltf_vol_ratio", "ltf_chop",
        "htf_adx", "htf_rsi_14", "htf_chop", "ltf_close_20_sma_atr", "htf_close_20_sma_atr",
    )
    result = {}
    for key in wanted:
        value = values.get(key)
        if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
            result[key] = round(float(value), 6)
    return result
