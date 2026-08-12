"""Mesure read-only de l'edge directionnel des entrées V14.

Compare les entrées de la porte à des instants aléatoires du même actif,
même heure UTC et même quartile de volatilité. Aucun seuil de trading n'est
modifié et aucun ordre n'est envoyé.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = [
    "EURUSD", "AUDUSD", "USDJPY", "USDCAD",
    "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD",
    "NAS100.fs", "US500", "UKOIL", "BRENT.fs",
]


def stable_seed(*parts) -> int:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big")


@dataclass(frozen=True)
class BarrierOutcome:
    outcome: int                 # +1, -1, 0=censure
    mfe_r: float
    mae_r: float
    bars: int
    entry_at: str = ""
    entry_hour: int = -1
    vol_bucket: int = -1
    side: int = 0


def barrier_outcome(frame: pd.DataFrame, entry_pos: int, *, side: int,
                    entry: float, r_unit: float, horizon: int = 200,
                    vol_bucket: int = -1) -> BarrierOutcome:
    """Premier passage ±1R ; double touche dans une barre = perte prudente."""
    if side not in (-1, 1) or not math.isfinite(r_unit) or r_unit <= 0:
        raise ValueError("side/r_unit invalide")
    mfe, mae = 0.0, 0.0
    end = min(len(frame) - 1, int(entry_pos) + int(horizon))
    outcome, used = 0, 0
    for pos in range(int(entry_pos) + 1, end + 1):
        high = float(frame["high"].iloc[pos])
        low = float(frame["low"].iloc[pos])
        fav = ((high - entry) if side > 0 else (entry - low)) / r_unit
        adv = ((low - entry) if side > 0 else (entry - high)) / r_unit
        mfe, mae, used = max(mfe, fav), min(mae, adv), pos - int(entry_pos)
        hit_plus = fav >= 1.0
        hit_minus = adv <= -1.0
        if hit_minus:            # inclut la double touche conservatrice
            outcome = -1
            break
        if hit_plus:
            outcome = 1
            break
    ts = frame.index[int(entry_pos)]
    hour = int(getattr(ts, "hour", -1))
    return BarrierOutcome(
        outcome=outcome, mfe_r=round(mfe, 6), mae_r=round(mae, 6), bars=used,
        entry_at=str(ts), entry_hour=hour, vol_bucket=int(vol_bucket), side=int(side),
    )


def volatility_buckets(frame: pd.DataFrame, window: int = 20) -> pd.Series:
    prev = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - prev).abs(),
        (frame["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    rel = tr.rolling(window, min_periods=window).mean() / frame["close"].abs()
    ranked = rel.rank(method="first", pct=True)
    # Remplir les NaN avant ``ceil`` : math.ceil(float("nan")) lève. Les
    # premières barres restent explicitement hors strate avec le seau -1.
    scaled = (ranked * 4).fillna(0).apply(math.ceil) - 1
    return scaled.clip(-1, 3).astype(int)


def matched_random_controls(frame: pd.DataFrame, *, signal_index,
                            signal_hour: int, signal_vol_bucket: int,
                            vol_bucket: pd.Series, r_unit: float,
                            draws: int = 20, seed: int = 0,
                            horizon: int = 200,
                            excluded_positions: set[int] | None = None,
                            atr: pd.Series | None = None) -> list[BarrierOutcome]:
    """Contrôles de même heure/quartile ; côtés équilibrés, tirage reproductible."""
    excluded = excluded_positions or set()
    candidates = [
        i for i, ts in enumerate(frame.index)
        if i not in excluded and i >= 20 and i + horizon < len(frame)
        and int(getattr(ts, "hour", -2)) == int(signal_hour)
        and int(vol_bucket.iloc[i]) == int(signal_vol_bucket)
        and ts != signal_index
    ]
    if not candidates:
        return []
    rng = random.Random(seed)
    out = []
    for j in range(int(draws)):
        pos = rng.choice(candidates)
        side = 1 if j % 2 == 0 else -1
        local_r = float(r_unit)
        if atr is not None and math.isfinite(float(atr.iloc[pos])) and float(atr.iloc[pos]) > 0:
            local_r = 1.5 * float(atr.iloc[pos])
        out.append(barrier_outcome(
            frame, pos, side=side, entry=float(frame["close"].iloc[pos]),
            r_unit=local_r, horizon=horizon,
            vol_bucket=signal_vol_bucket,
        ))
    return out


def summarize(outcomes: list[BarrierOutcome]) -> dict:
    n = len(outcomes)
    resolved = [o for o in outcomes if o.outcome]
    mfe = [o.mfe_r for o in outcomes]
    mae = [o.mae_r for o in outcomes]
    def q(values, p):
        if not values:
            return None
        return float(pd.Series(values).quantile(p))
    return {
        "n": n,
        "resolved": len(resolved),
        "plus_first": sum(o.outcome == 1 for o in resolved),
        "minus_first": sum(o.outcome == -1 for o in resolved),
        "p_plus_before_minus": (
            sum(o.outcome == 1 for o in resolved) / len(resolved) if resolved else None
        ),
        "censored_rate": (n - len(resolved)) / n if n else None,
        "mfe_r": {"q25": q(mfe, .25), "median": q(mfe, .5), "q75": q(mfe, .75)},
        "mae_r": {"q25": q(mae, .25), "median": q(mae, .5), "q75": q(mae, .75)},
    }


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    prev = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - prev).abs(),
        (frame["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window).mean()


def paired_comparison(rows: list[dict], *, seed: int,
                      samples: int = 5000) -> dict:
    """Bootstrap par signal, afin de ne pas pseudo-répliquer ses contrôles."""
    differences = []
    for row in rows:
        signal = row["signal"]
        controls = [o for o in row["controls"] if o.outcome]
        if not signal.outcome or not controls:
            continue
        p_signal = 1.0 if signal.outcome == 1 else 0.0
        p_control = sum(o.outcome == 1 for o in controls) / len(controls)
        differences.append(p_signal - p_control)
    if not differences:
        return {"pairs": 0, "delta": None, "ci95": [None, None],
                "p_le_zero": None}
    rng = random.Random(seed)
    deltas = []
    for _ in range(samples):
        deltas.append(statistics.fmean(
            rng.choice(differences) for _ in differences))
    deltas.sort()
    observed = statistics.fmean(differences)
    return {
        "pairs": len(differences),
        "delta": observed,
        "ci95": [deltas[int(.025 * samples)], deltas[int(.975 * samples)]],
        "p_le_zero": sum(d <= 0 for d in deltas) / samples,
    }


def _group(rows: list[dict], field: str) -> dict:
    keys = sorted({str(r[field]) for r in rows})
    return {
        key: {
            "signal": summarize([r["signal"] for r in rows if str(r[field]) == key]),
            "random": summarize([o for r in rows if str(r[field]) == key for o in r["controls"]]),
            "comparison": paired_comparison(
                [r for r in rows if str(r[field]) == key],
                seed=1000 + sum(ord(c) for c in key),
            ),
        } for key in keys
    }


def _cross_group(rows: list[dict]) -> dict:
    keys = sorted({f"{r['asset_class']}|{r['pillars']}p" for r in rows})
    return {
        key: {
            "signal": summarize([
                r["signal"] for r in rows
                if f"{r['asset_class']}|{r['pillars']}p" == key]),
            "random": summarize([
                o for r in rows
                if f"{r['asset_class']}|{r['pillars']}p" == key
                for o in r["controls"]]),
            "comparison": paired_comparison(
                [r for r in rows
                 if f"{r['asset_class']}|{r['pillars']}p" == key],
                seed=stable_seed("cross", key)),
        } for key in keys
    }


def analyse(symbols: list[str], *, bars: int, step: int, draws: int,
            horizon: int) -> dict:
    from titanium.backtest import rejouer
    from titanium.data.mt5_vendor import ensure_symbol, get_rates, shutdown
    from titanium.edge import asset_class_of

    rows, failures = [], []
    try:
        for symbol in symbols:
            try:
                spec = ensure_symbol(symbol)
                frame = get_rates(symbol, "M15", bars)
                htf = get_rates(symbol, "H4", max(1500, bars // 3))
                result = rejouer(
                    symbol, frame, htf,
                    spread=float(spec.spread) * float(spec.point),
                    pas=step, avec_indicateurs=False,
                )
                buckets, atr = volatility_buckets(frame), _atr(frame)
                positions = {ts: i for i, ts in enumerate(frame.index)}
                signal_positions = {
                    positions.get(pd.Timestamp(t.bar_entree)) for t in result.trades
                }
                signal_positions.discard(None)
                for k, trade in enumerate(result.trades):
                    pos = positions.get(pd.Timestamp(trade.bar_entree))
                    if pos is None or int(buckets.iloc[pos]) < 0:
                        continue
                    sig = barrier_outcome(
                        frame, pos, side=trade.side, entry=trade.prix_entree,
                        r_unit=trade.r_unit, horizon=horizon,
                        vol_bucket=int(buckets.iloc[pos]),
                    )
                    controls = matched_random_controls(
                        frame, signal_index=frame.index[pos],
                        signal_hour=frame.index[pos].hour,
                        signal_vol_bucket=int(buckets.iloc[pos]),
                        vol_bucket=buckets, r_unit=trade.r_unit,
                        draws=draws, seed=stable_seed(symbol, k),
                        horizon=horizon, excluded_positions=signal_positions,
                        atr=atr,
                    )
                    if controls:
                        rows.append({
                            "symbol": symbol,
                            "asset_class": asset_class_of(symbol) or "inconnue",
                            "pillars": int(trade.pillars),
                            "family": trade.family,
                            "signal": sig,
                            "controls": controls,
                        })
            except Exception as exc:  # un actif ne bloque pas l'étude
                failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        shutdown()

    signals = [r["signal"] for r in rows]
    controls = [o for r in rows for o in r["controls"]]
    return {
        "schema_version": 1,
        "parameters": {"symbols": symbols, "bars": bars, "step": step,
                       "draws_per_signal": draws, "horizon_bars": horizon,
                       "barrier": "first +1R vs -1R; same-bar double touch=-1R"},
        "overall": {
            "signal": summarize(signals),
            "random": summarize(controls),
            "comparison": paired_comparison(rows, seed=20260812),
        },
        "by_pillars": _group(rows, "pillars"),
        "by_asset_class": _group(rows, "asset_class"),
        "by_asset_class_and_pillars": _cross_group(rows),
        "signal_count": len(rows),
        "control_count": len(controls),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbols", nargs="*", default=None)
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--draws", type=int, default=20)
    ap.add_argument("--horizon", type=int, default=200)
    ap.add_argument("--output", default="results/edge_directionnel.json")
    args = ap.parse_args(argv)
    report = analyse(args.symbols or DEFAULT_SYMBOLS, bars=args.bars, step=args.step,
                     draws=args.draws, horizon=args.horizon)
    target = Path(args.output)
    if not target.is_absolute():
        target = RACINE / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["overall"], ensure_ascii=False, indent=2))
    print(f"signaux={report['signal_count']} controles={report['control_count']} -> {target}")
    return 0 if report["signal_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
