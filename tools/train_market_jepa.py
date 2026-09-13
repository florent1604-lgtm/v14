"""Entraine l'organe Market-JEPA V14 sur les archives M15, hors MT5 live."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from titanium.data.archive_barres import charger_barres  # noqa: E402
from titanium.organism.market_jepa import (  # noqa: E402
    DEFAULT_BLOCKS,
    DEFAULT_CONTEXT,
    PREDICTION_NAMES,
    SCHEMA_VERSION,
    fit_ridge,
    training_examples,
)

DEFAULT_SYMBOLS = (
    "USTECH", "NAS100.fs", "US500", "S&P.fs", "DJ30.fs", "FRA40",
    "UKOIL", "BRENT.fs", "COFFEE.fs", "BTCUSD", "ETHUSD", "BTC-JPY",
    "BNB-USD",
)


def train(symbols, *, context=DEFAULT_CONTEXT, horizon=4,
          blocks=DEFAULT_BLOCKS, max_bars=30_000) -> dict:
    import numpy as np

    all_x = []
    all_y = []
    by_asset = {}
    skipped = {}
    for symbol in symbols:
        try:
            frame = charger_barres(symbol, "M15", count=max_bars)
            x, y = training_examples(
                frame, context=context, horizon=horizon, blocks=blocks,
            )
            if len(x) < 100:
                raise ValueError(f"seulement {len(x)} exemples")
            by_asset[symbol.upper()] = fit_ridge(x, y)
            all_x.append(x)
            all_y.append(y)
        except Exception as exc:  # noqa: BLE001 - rapport explicite par actif
            skipped[symbol] = f"{type(exc).__name__}: {str(exc)[:120]}"
    if not all_x:
        raise RuntimeError("aucun actif exploitable pour Market-JEPA")
    return {
        "schema_version": SCHEMA_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": "M15",
        "context": int(context),
        "horizon": int(horizon),
        "blocks": int(blocks),
        "predictions": list(PREDICTION_NAMES),
        "directional_output": False,
        "global": fit_ridge(np.vstack(all_x), np.vstack(all_y)),
        "assets": by_asset,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results" / "market_jepa" / "model.json")
    parser.add_argument("--max-bars", type=int, default=30_000)
    args = parser.parse_args()
    model = train(args.symbols, max_bars=args.max_bars)
    raw = (json.dumps(model, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)
    manifest = {
        "schema_version": 1,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "model": args.output.name,
        "trained_at": model["trained_at"],
        "assets": sorted(model["assets"]),
        "samples": int(model["global"]["samples"]),
        "directional_output": False,
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
