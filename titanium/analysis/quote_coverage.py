"""Inventaire leger de couverture; ne certifie ni les ticks ni l'ouverture du marche."""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path


def coverage_for(symbols: list[str], root: Path, *, now_ms: float | None = None) -> dict:
    now = time.time() * 1000 if now_ms is None else now_ms
    assets = []
    for symbol in dict.fromkeys(symbols):
        if not symbol or symbol in (".", "..") or any(
            not (c.isalnum() or c in "-_.&") for c in symbol
        ):
            raise ValueError("Unsafe archive symbol")
        paths = sorted((root / symbol).glob("*.ndjson"))
        asset = {"symbol": symbol, "files": len(paths), "status": "NO_ARCHIVE",
                 "last_archived_ms": None, "archive_age_ms": None,
                 "quality": "NOT_AUDITED", "market_session": "NOT_CHECKED"}
        if paths:
            asset["latest_file"] = paths[-1].name
            asset["status"] = "UNREADABLE_TAIL"
            try:
                with paths[-1].open("rb") as handle:
                    size = handle.seek(0, 2)
                    start = max(0, size - 65_536)
                    handle.seek(start)
                    tail = handle.read(65_536)
                # Ne considere ni le fragment initial ni une fin non terminee.
                lines = tail.splitlines(keepends=True)
                if start and lines:
                    lines = lines[1:]
                for raw in reversed(lines):
                    if not raw.endswith(b"\n"):
                        continue
                    try:
                        row = json.loads(raw)
                        ts = row["ts_ms"]
                        if (isinstance(ts, bool) or not isinstance(ts, (int, float))
                                or not math.isfinite(ts) or ts <= 0
                                or row.get("horloge") != "utc" or row.get("symbole") != symbol):
                            continue
                        asset.update(status="ARCHIVE_PRESENT", last_archived_ms=ts,
                                     archive_age_ms=now - ts)
                        if ts > now:
                            asset["status"] = "FUTURE_TIMESTAMP"
                        break
                    except (TypeError, ValueError, KeyError):
                        continue
            except OSError:
                pass
        assets.append(asset)
    observed = {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()
    return {"schema": "v14.quote-coverage.v1", "observed_ms": now,
            "expected_symbols": len(assets), "assets": assets,
            "counts": dict(Counter(a["status"] for a in assets)),
            "archive_symbols_outside_catalogue": sorted(observed - set(symbols)),
            "ready_for_exit_optimization": False, "trading_changes": False}
