"""Audit streaming des quotes archivees, sans MT5 ni hypothese de rentabilite."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _positive(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def audit_quote_files(paths: list[Path], *, symbol: str, max_gap_ms: int = 60_000) -> dict:
    """Ordre des fichiers fourni conserve; aucun tri/reparation silencieux des ticks.

    Lit la taille initiale de chaque fichier et signale toute mutation observee.
    Les trous sont des obstacles au rejeu, pas necessairement des pannes (seances).
    """
    if isinstance(max_gap_ms, bool) or not isinstance(max_gap_ms, int) or max_gap_ms <= 0:
        raise ValueError("max_gap_ms must be a positive integer")
    errors: Counter = Counter()
    files = []
    previous = None
    first_ms = last_ms = None
    valid = lines = gaps = duplicates = max_gap = 0
    gap_examples = []
    if not paths:
        errors["NO_FILES"] += 1
    for path in paths:
        digest = hashlib.sha256()
        count = 0
        try:
            before = path.stat()
            with path.open("rb") as handle:
                remaining = before.st_size
                while remaining:
                    # Une ligne malformee ne doit pas allouer des Go de RAM.
                    raw = handle.readline(min(remaining, 65_537))
                    if not raw:
                        errors["TRUNCATED_DURING_READ"] += 1
                        break
                    remaining -= len(raw)
                    digest.update(raw)
                    count += 1
                    lines += 1
                    if len(raw) > 65_536:
                        errors["OVERSIZED_LINE"] += 1
                        while remaining and not raw.endswith(b"\n"):
                            raw = handle.readline(min(remaining, 65_537))
                            if not raw:
                                break
                            remaining -= len(raw)
                            digest.update(raw)
                        continue
                    if not raw.endswith(b"\n"):
                        errors["INCOMPLETE_LINE"] += 1
                        continue
                    try:
                        row = json.loads(raw)
                        if not isinstance(row, dict):
                            raise ValueError("INVALID_RECORD")
                        ts, bid, ask = row["ts_ms"], row["bid"], row["ask"]
                        if not all(_positive(v) for v in (ts, bid, ask)) or int(ts) != ts:
                            raise ValueError("INVALID_NUMBERS")
                        if row.get("symbole") != symbol:
                            raise ValueError("SYMBOL_MISMATCH")
                        if row.get("horloge") != "utc":
                            raise ValueError("UNKNOWN_CLOCK")
                        if ask < bid:
                            raise ValueError("CROSSED_QUOTE")
                        day = datetime.fromtimestamp(ts / 1000, timezone.utc).date().isoformat()
                        if path.stem != day:
                            raise ValueError("DAY_MISMATCH")
                    except (KeyError, ValueError, TypeError, OverflowError, OSError):
                        errors["INVALID_RECORD"] += 1
                        continue
                    valid += 1
                    first_ms = ts if first_ms is None else min(first_ms, ts)
                    last_ms = ts if last_ms is None else max(last_ms, ts)
                    if previous is not None:
                        delta = ts - previous[0]
                        if delta < 0:
                            errors["OUT_OF_ORDER"] += 1
                        max_gap = max(max_gap, delta)
                        if delta > max_gap_ms:
                            gaps += 1
                            if len(gap_examples) < 20:
                                gap_examples.append({"from_ms": previous[0], "to_ms": ts,
                                                     "gap_ms": delta})
                        duplicates += (ts, bid, ask) == previous
                    previous = (ts, bid, ask)
            after = path.stat()
            stable = (before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
                      and before.st_ino == after.st_ino)
            if not stable:
                errors["FILE_CHANGED_DURING_READ"] += 1
            if count == 0:
                errors["EMPTY_FILE"] += 1
            files.append({"name": path.name, "bytes": before.st_size, "lines": count,
                          "sha256": digest.hexdigest(), "stable": stable})
        except OSError as exc:
            errors["UNREADABLE_FILE"] += 1
            files.append({"name": path.name, "error": type(exc).__name__})
    verdict = "INVALID" if errors else "GAPPED" if gaps else "STRUCTURALLY_VALID"
    return {
        "schema": "v14.quote-quality.v1", "symbol": symbol, "verdict": verdict,
        "files": files, "lines": lines, "valid_quotes": valid, "errors": dict(errors),
        "first_ms": first_ms, "last_ms": last_ms, "max_gap_ms": max_gap,
        "gap_threshold_ms": max_gap_ms, "gaps": gaps, "gap_examples": gap_examples,
        "consecutive_identical_quotes": duplicates,
        "ready_for_exit_optimization": False,
        "not_validated": ["broker_completeness", "session_calendar", "causal_atr",
                          "admissions_and_reentries", "fees_and_slippage", "baseline_reproduction"],
    }
