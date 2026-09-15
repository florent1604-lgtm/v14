"""Reproducible, offline audit; descriptive evidence is never a trading policy."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from titanium.analysis.discriminants import analyser


def read_prefix(path: Path, snapshot: dict | None = None) -> tuple[list[dict], dict]:
    """Read a fixed byte prefix. A partial final append is counted, not parsed."""
    with path.open("rb") as stream:
        size = snapshot["bytes"] if snapshot else path.stat().st_size
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("invalid snapshot length")
        raw = stream.read(size)
    sha = hashlib.sha256(raw).hexdigest()
    if len(raw) != size or (snapshot and sha != snapshot["sha256"]):
        raise ValueError(f"snapshot changed: {path.name}")
    lines = raw.split(b"\n")
    tail = len(lines.pop())
    rows = [json.loads(line) for line in lines if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"non-object record: {path.name}")
    return rows, {"bytes": size, "sha256": sha, "records": len(rows), "partial_tail_bytes": tail}


def ticket(value) -> str:
    return str(value or "").removeprefix("live:")


def unique(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for row in rows:
        key = ticket(row.get("ticket"))
        if not key:
            raise ValueError("missing ticket")
        if key in out and row != out[key]:
            raise ValueError(f"conflicting duplicate: {key}")
        out[key] = row
    return out


def finite(value) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def summary(rows: list[dict]) -> dict:
    if any(not finite(row.get("pnl_r")) for row in rows):
        raise ValueError("invalid pnl_r")
    values = np.array([row["pnl_r"] for row in rows], dtype=float)
    if not len(values):
        return {"n": 0, "mean_r": None, "profit_factor": None}
    positive, negative = values[values > 0].sum(), -values[values < 0].sum()
    costs = [row["cost_r"] for row in rows if finite(row.get("cost_r"))]
    return {
        "n": len(values),
        "mean_r": float(values.mean()),
        "sum_r": float(values.sum()),
        "winrate": float((values > 0).mean()),
        "profit_factor": float(positive / negative) if negative else None,
        "cost_known_n": len(costs),
        "cost_estimated_mean_r": float(np.mean(costs)) if costs else None,
        "cost_exact_n": sum(row.get("exact_cost") is True for row in rows),
        "net_explicitly_exact_n": sum(row.get("exact_net") is True for row in rows),
        "gross_proxy_mean_r": float(
            np.mean([row["pnl_r"] + row["cost_r"] for row in rows if finite(row.get("cost_r"))])
        )
        if costs
        else None,
    }


def daily_bootstrap(rows: list[dict], *, repetitions: int = 4000, seed: int = 601) -> dict:
    """Resample whole closing days, preserving same-day cross-asset dependence."""
    days = defaultdict(list)
    for row in rows:
        days[row["closed_at"][:10]].append(row["pnl_r"])
    if len(days) < 2:
        return {"days": len(days), "ci95": None}
    buckets = list(days.values())
    sums = np.array([sum(v) for v in buckets])
    counts = np.array([len(v) for v in buckets])
    indices = np.random.default_rng(seed).integers(0, len(days), (repetitions, len(days)))
    means = sums[indices].sum(axis=1) / counts[indices].sum(axis=1)
    return {
        "days": len(days),
        "ci95": np.quantile(means, [0.025, 0.975]).tolist(),
        "repetitions": repetitions,
        "seed": seed,
        "limitation": "closing-day clusters; dependence across days remains possible",
    }


def pillar_comparison(rows: list[dict], *, by_symbol: bool, repetitions: int = 2000) -> dict:
    """Within class/symbol and ISO week contrast, exploratory, not causal."""
    groups = defaultdict(list)
    for row in rows:
        if row.get("support_pillars") not in (2, 3):
            continue
        week = datetime.fromisoformat(row["closed_at"]).isocalendar()[:2]
        asset = row["context"].split("|")[0] if by_symbol else row.get("asset_class", "unknown")
        groups[(asset, *week)].append(row)
    strata = []
    for group in groups.values():
        a = [r["pnl_r"] for r in group if r["support_pillars"] == 3]
        b = [r["pnl_r"] for r in group if r["support_pillars"] == 2]
        if a and b:
            strata.append((np.array(a + b), len(a), len(a) * len(b) / (len(a) + len(b))))
    if not strata:
        return {"strata": 0, "matched_n": 0, "delta_3_minus_2": None, "p_exploratory": None}
    weight = sum(s[2] for s in strata)
    observed = sum(w * (v[:n].mean() - v[n:].mean()) for v, n, w in strata) / weight
    rng = np.random.default_rng(601)
    exceed = 0
    for _ in range(repetitions):
        delta = 0.0
        for values, n, w in strata:
            shuffled = rng.permutation(values)
            delta += w * (shuffled[:n].mean() - shuffled[n:].mean())
        exceed += abs(delta / weight) >= abs(observed)
    return {
        "strata": len(strata),
        "matched_n": sum(len(v) for v, _, _ in strata),
        "delta_3_minus_2": float(observed),
        "p_exploratory": (exceed + 1) / (repetitions + 1),
        "seed": 601,
        "repetitions": repetitions,
        "limitation": "not randomized; no control for side, policy, selection or serial dependence",
    }


def audit(
    trades: list[dict], excursions: list[dict], lifecycle: list[dict], decisions: list[dict]
) -> dict:
    live = [row for row in trades if row.get("source") == "live"]
    normalized = unique(live)
    rows = sorted(normalized.values(), key=lambda row: (row["closed_at"], ticket(row["ticket"])))
    if not rows:
        raise ValueError("no live trades: not a zero-performance result")
    joined = unique(excursions)
    matched = [
        (row, joined[ticket(row["ticket"])]) for row in rows if ticket(row["ticket"]) in joined
    ]
    contexts = Counter(row["context"] for row in rows)
    by_pillars = {
        str(n): summary([r for r in rows if r.get("support_pillars") == n])
        for n in sorted({r.get("support_pillars", -1) for r in rows})
    }
    placed = {str(r["order_ticket"]): r for r in lifecycle if r.get("event") == "placed"}
    filled = {str(r["order_ticket"]): r for r in lifecycle if r.get("event") == "filled"}
    expired = {str(r["order_ticket"]): r for r in lifecycle if r.get("event") == "expired"}
    closed = [r for r in lifecycle if r.get("event") == "closed"]
    overlaps = sum(ticket(r.get("position_ticket")) in normalized for r in closed)
    complete = sum(
        ticket(r.get("position_ticket")) in normalized and str(r["order_ticket"]) in placed
        for r in closed
    )
    panel_keys = ("ltf_adx", "htf_adx", "ltf_chop", "htf_chop")
    # Select a small prespecified family; reuse the repository's permutation/BH engine.
    observable = [(r, e) for r, e in matched if e.get("censored") is False]
    samples = [
        (
            {k: e.get("indicators", {}).get(k) for k in panel_keys},
            1.0 if e.get("mfe_r", 0) >= 0.8 else -1.0,
        )
        for _, e in matched
        if finite(e.get("mfe_r"))
    ]
    discriminants = analyser(samples, n_permutations=2000, graine=601).to_dict()
    cut = len(rows) * 2 // 3
    recent_ids = {ticket(r["ticket"]) for r in rows[cut:]}
    recent = [(r, e) for r, e in matched if ticket(r["ticket"]) in recent_ids]
    temporal = analyser(
        [
            (
                {k: e.get("indicators", {}).get(k) for k in panel_keys},
                1.0 if e.get("mfe_r", 0) >= 0.8 else -1.0,
            )
            for _, e in recent
            if finite(e.get("mfe_r"))
        ],
        n_permutations=2000,
        graine=601,
    ).to_dict()
    rr = [
        abs(e["tp_initial"] - e["entry"]) / e["r_unit"]
        for _, e in matched
        if all(finite(e.get(k)) for k in ("entry", "tp_initial", "r_unit")) and e["r_unit"] > 0
    ]
    resolved_ids = {
        ticket(r.get("execution_ticket")) for r in decisions if r.get("event") == "resolved"
    }
    pnl_mismatch = sum(abs(r["pnl_r"] - e["pnl_r"]) > 0.001 for r, e in matched)
    return {
        "kind": "descriptive_live_history_not_oos",
        "period": [rows[0]["closed_at"], rows[-1]["closed_at"]],
        "performance": summary(rows),
        "closing_day_bootstrap": daily_bootstrap(rows),
        "raw_live_records": len(live),
        "duplicate_identical_records": len(live) - len(rows),
        "by_support_pillars": by_pillars,
        "pillars_class_week": pillar_comparison(rows, by_symbol=False),
        "pillars_symbol_week": pillar_comparison(rows, by_symbol=True),
        "contexts": {
            "n": len(contexts),
            "at_least_20": sum(n >= 20 for n in contexts.values()),
            "symbols": len({r["context"].split("|")[0] for r in rows}),
        },
        "joins": {
            "excursions": len(matched),
            "missing_excursions": len(rows) - len(matched),
            "pnl_mismatches": pnl_mismatch,
            "decision_resolved": len(set(normalized) & resolved_ids),
            "limit_closed": len(closed),
            "limit_closed_joined": overlaps,
            "limit_closed_with_placed": complete,
        },
        "limits": {
            "placed": len(placed),
            "filled": len(filled),
            "expired": len(expired),
            "fill_ratio": len(filled) / len(placed) if placed else None,
        },
        "excursions": {
            "censored": sum(bool(e.get("censored")) for _, e in matched),
            "uncensored": len(observable),
            "regime_analysis_population": "all joined trades with measured MFE",
            "censored_definition": "phase != trailing AND pnl_r <= 0; outcome-derived, NOT missing-data flag",
            "mfe_ge_08_uncensored": sum(e.get("mfe_r", 0) >= 0.8 for _, e in observable),
            "mfe_ge_08": sum(e.get("mfe_r", 0) >= 0.8 for _, e in matched),
            "mfe_max": max((e.get("mfe_r", 0) for _, e in matched), default=None),
            "planned_rr_rounded_counts": dict(Counter(round(v, 1) for v in rr)),
            "counterfactual_exits": "NOT_IDENTIFIABLE_FROM_EXTREMA_WITHOUT_ORDERED_QUOTES_AND_ATR",
        },
        "regime_vs_mfe": discriminants,
        "regime_recent_third": temporal,
        "regime_uncensored_sensitivity": analyser(
            [
                (
                    {k: e.get("indicators", {}).get(k) for k in panel_keys},
                    1.0 if e["mfe_r"] >= 0.8 else -1.0,
                )
                for _, e in observable
                if finite(e.get("mfe_r"))
            ],
            n_permutations=2000,
            graine=601,
        ).to_dict(),
        "regime_limitations": "4 prespecified features; FDR within each analysis, not across repeated looks; not OOS",
        "promotion": "NONE: descriptive selection and policy-confounded history",
    }
