"""Contre-mesure indépendante Hermes: rejeu V14 et lifecycle limites.

Lecture seule. Usage depuis la racine V14:
  .venv/Scripts/python.exe collab/HERMES_AUDIT_V14_20260821.py
"""
from __future__ import annotations

import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

from scipy.stats import t as tdist

ROOT = Path(__file__).resolve().parent.parent


def wilson(k: int, n: int) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    z = 1.95996398454
    den = 1 + z * z / n
    centre = (k + z * z / 2) / n / den
    demi = z * math.sqrt(k * (n - k) / n + z * z / 4) / n / den
    return centre - demi, centre + demi


def replay_rows() -> list[dict]:
    rows = []
    for path in sorted((ROOT / "results/rejeu_univers").glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cal = obj.get("calibration") or {}
        ver = obj.get("verification") or {}
        glob = obj.get("global") or {}
        if not isinstance(ver.get("esperance_r"), (int, float)) or not ver.get("n"):
            continue
        n = int(ver["n"])
        mean = float(ver["esperance_r"])
        sd = float(ver.get("ecart_type_r") or 0.0)
        se = sd / math.sqrt(n) if n > 1 and sd > 0 else math.inf
        critical = float(tdist.ppf(0.975, n - 1)) if n > 1 else math.inf
        p_one_sided = float(tdist.sf(mean / se, n - 1)) if math.isfinite(se) else 1.0
        rows.append({
            "symbol": obj.get("symbole") or path.stem,
            "cal": cal.get("esperance_r"), "n_cal": cal.get("n"),
            "ver": mean, "n_ver": n, "sd_ver": sd,
            "ci95_lo": mean - critical * se, "ci95_hi": mean + critical * se,
            "p_one_sided": p_one_sided,
            "global": glob.get("esperance_r"), "n_global": glob.get("n"),
            "winrate_ver": ver.get("winrate"), "pf_ver": ver.get("profit_factor"),
            "spread_points": obj.get("spread_points"), "written_at": obj.get("ecrit_le"),
        })
    return rows


def add_bh(rows: list[dict]) -> None:
    """Benjamini-Hochberg sur tous les actifs terminés, test unilatéral E[R] > 0."""
    m = len(rows)
    order = sorted(range(m), key=lambda i: rows[i]["p_one_sided"])
    q = [1.0] * m
    previous = 1.0
    for rank, idx in reversed(list(enumerate(order, 1))):
        previous = min(previous, rows[idx]["p_one_sided"] * m / rank)
        q[idx] = previous
    for idx, row in enumerate(rows):
        row["q_bh"] = q[idx]


def limit_stats() -> dict:
    events = []
    path = ROOT / "results/limit_lifecycle.ndjson"
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    placed = {e["order_ticket"]: e for e in events if e.get("event") == "placed"}
    event_types: dict[int, set[str]] = defaultdict(set)
    for event in events:
        if event.get("order_ticket") is not None:
            event_types[event["order_ticket"]].add(str(event.get("event")))
    by_class = defaultdict(lambda: {"placed": 0, "filled": 0})
    for ticket, event in placed.items():
        row = by_class[event.get("asset_class") or "unknown"]
        row["placed"] += 1
        if {"filled", "filled_metrics"} & event_types[ticket]:
            row["filled"] += 1
    for row in by_class.values():
        row["fill_rate"] = row["filled"] / row["placed"] if row["placed"] else None
        row["wilson95"] = wilson(row["filled"], row["placed"])
    total_filled = sum(r["filled"] for r in by_class.values())
    return {
        "events": len(events), "placed": len(placed), "filled": total_filled,
        "fill_rate": total_filled / len(placed) if placed else None,
        "wilson95": wilson(total_filled, len(placed)), "by_class": dict(by_class),
    }


def main() -> None:
    rows = replay_rows()
    add_bh(rows)
    robust = [r for r in rows if (
        isinstance(r["cal"], (int, float)) and r["cal"] > 0
        and int(r["n_cal"] or 0) >= 60 and r["ver"] > 0 and r["n_ver"] >= 60
        and r["ci95_lo"] > 0 and r["q_bh"] <= 0.05
    )]
    degradation = [r["ver"] - r["cal"] for r in rows
                   if isinstance(r["cal"], (int, float)) and r["cal"] > 0]
    corr = json.loads((ROOT / "results/correlations/structure_H1.json").read_text(encoding="utf-8"))
    out = {
        "coverage": {"completed": len(rows), "universe": 149},
        "median_verification_r": st.median(r["ver"] for r in rows),
        "positive_verification": sum(r["ver"] > 0 for r in rows),
        "calibration_positive": sum(isinstance(r["cal"], (int, float)) and r["cal"] > 0 for r in rows),
        "median_degradation_among_calibration_positive": st.median(degradation) if degradation else None,
        "robust_candidates": sorted(robust, key=lambda r: -r["ver"]),
        "limit_lifecycle": limit_stats(),
        "correlations": {
            "symbols": len(corr.get("symboles", [])), "bars": corr.get("barres"),
            "blocks": len(corr.get("blocs", [])), "factors": corr.get("facteurs"),
            "lead_lag": corr.get("avance_retard"),
        },
        "warning": "Les tests t utilisent les résumés; sans trades bruts, pas de bootstrap par blocs ni contrôle de dépendance sérielle. Les candidats restent SHADOW/PAPER.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
