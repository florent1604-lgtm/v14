"""Scelle la cohorte P1a sans dépendre de la fin mouvante des journaux.

Le cutoff est un ``event_id`` de clôture explicite. Les jointures sont strictes :
``position_ticket`` pour trades/excursions et ``order_ticket`` pour placed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "v14.p1a.sealed-cohort.v1"
SUPPORTED_EXIT_REASONS = {"init", "breakeven", "trailing"}
CONTEXT_PILLARS = re.compile(r"\|(\d+)p$")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_ticket(value: Any) -> str:
    ticket = str(value or "").strip().split(":")[-1]
    if not ticket:
        raise ValueError("ticket absent")
    return ticket


def stable_read(path: Path) -> bytes:
    """Lit une fois et refuse un fichier modifié pendant la lecture."""
    before = path.stat()
    with path.open("rb") as stream:
        data = stream.read()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"source modifiée pendant la lecture: {path}")
    if len(data) != before.st_size:
        raise RuntimeError(f"lecture incomplète: {path}")
    return data


def parse_ndjson(data: bytes, source: Path) -> list[tuple[int, dict]]:
    rows: list[tuple[int, dict]] = []
    for line_number, raw_line in enumerate(data.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"NDJSON invalide: {source}, ligne {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"objet JSON attendu: {source}, ligne {line_number}")
        rows.append((line_number, row))
    return rows


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def unique_index(rows: list[tuple[int, dict]], field: str, source: Path) -> dict[str, tuple[int, dict]]:
    index: dict[str, tuple[int, dict]] = {}
    for line_number, row in rows:
        ticket = normalize_ticket(row.get(field))
        if ticket in index:
            raise ValueError(f"{field} dupliqué {ticket}: {source}, lignes {index[ticket][0]} et {line_number}")
        index[ticket] = (line_number, row)
    return index


def same_float(left: Any, right: Any, field: str, ticket: str, *, tolerance: float = 1e-9) -> None:
    try:
        matches = math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} non numérique pour {ticket}") from exc
    if not matches:
        raise ValueError(f"{field} incohérent pour {ticket}: {left!r} != {right!r}")


def same_value(values: dict[str, Any], field: str, ticket: str) -> Any:
    normalized = {source: str(value) for source, value in values.items()}
    if len(set(normalized.values())) != 1:
        raise ValueError(f"{field} incohérent pour {ticket}: {normalized}")
    return next(iter(values.values()))


def selected_rows_hash(rows: list[dict], *, ticket_field: str) -> str:
    ordered = sorted(rows, key=lambda row: normalize_ticket(row.get(ticket_field)))
    return sha256_bytes(canonical_bytes(ordered))


def lifecycle_prefix_bytes(data: bytes, through_line: int) -> bytes:
    lines = data.splitlines(keepends=True)
    if through_line > len(lines):
        raise ValueError("cutoff lifecycle hors fichier")
    return b"".join(lines[:through_line])


def build_sealed_cohort(
    lifecycle_path: Path,
    trades_path: Path,
    excursions_path: Path,
    *,
    through_closed_event_id: str,
    expected_count: int | None = None,
) -> tuple[dict, dict]:
    lifecycle_data = stable_read(lifecycle_path)
    trades_data = stable_read(trades_path)
    excursions_data = stable_read(excursions_path)
    lifecycle_rows = parse_ndjson(lifecycle_data, lifecycle_path)
    trade_rows = parse_ndjson(trades_data, trades_path)
    excursion_rows = parse_ndjson(excursions_data, excursions_path)

    cutoff_matches = [item for item in lifecycle_rows if item[1].get("event_id") == through_closed_event_id]
    if len(cutoff_matches) != 1 or cutoff_matches[0][1].get("event") != "closed":
        raise ValueError("through_closed_event_id doit désigner exactement un événement closed")
    through_line, cutoff_row = cutoff_matches[0]
    lifecycle_prefix = [item for item in lifecycle_rows if item[0] <= through_line]
    closed_rows = [item for item in lifecycle_prefix if item[1].get("event") == "closed"]
    placed_rows = [item for item in lifecycle_prefix if item[1].get("event") == "placed"]
    if expected_count is not None and len(closed_rows) != expected_count:
        raise ValueError(f"cohorte inattendue: {len(closed_rows)} clôtures, attendu {expected_count}")

    unique_index(closed_rows, "position_ticket", lifecycle_path)
    placed_index = unique_index(placed_rows, "order_ticket", lifecycle_path)
    trade_index = unique_index(trade_rows, "ticket", trades_path)
    excursion_index = unique_index(excursion_rows, "ticket", excursions_path)

    cohort: list[dict] = []
    selected_placed: list[dict] = []
    selected_trades: list[dict] = []
    selected_excursions: list[dict] = []
    for _, closed in closed_rows:
        position_ticket = normalize_ticket(closed.get("position_ticket"))
        order_ticket = normalize_ticket(closed.get("order_ticket"))
        if order_ticket not in placed_index:
            raise ValueError(f"placed manquant pour order_ticket {order_ticket}")
        if position_ticket not in trade_index:
            raise ValueError(f"trade manquant pour position_ticket {position_ticket}")
        if position_ticket not in excursion_index:
            raise ValueError(f"excursion manquante pour position_ticket {position_ticket}")
        placed = placed_index[order_ticket][1]
        trade = trade_index[position_ticket][1]
        excursion = excursion_index[position_ticket][1]

        symbol = same_value(
            {"closed": closed.get("symbol"), "placed": placed.get("symbol"), "excursion": excursion.get("symbol")},
            "symbol",
            position_ticket,
        )
        side = same_value(
            {"closed": closed.get("side"), "placed": placed.get("side"), "excursion": excursion.get("side")},
            "side",
            position_ticket,
        )
        context = same_value(
            {
                "closed": closed.get("context"),
                "placed": placed.get("context"),
                "trade": trade.get("context"),
                "excursion": excursion.get("context"),
            },
            "context",
            position_ticket,
        )
        same_float(closed.get("pnl_r"), trade.get("pnl_r"), "pnl_r closed/trade", position_ticket)
        same_float(closed.get("pnl_r"), excursion.get("pnl_r"), "pnl_r closed/excursion", position_ticket)
        exit_reason = same_value(
            {"trade": trade.get("exit_reason"), "excursion": excursion.get("exit_reason")},
            "exit_reason",
            position_ticket,
        )
        if exit_reason not in SUPPORTED_EXIT_REASONS:
            raise ValueError(f"exit_reason non pris en charge pour {position_ticket}: {exit_reason!r}")
        match = CONTEXT_PILLARS.search(str(context))
        if not match:
            raise ValueError(f"suffixe de contexte absent pour {position_ticket}: {context!r}")
        context_pillars = int(match.group(1))
        support_pillars = int(trade.get("support_pillars"))
        if context_pillars != support_pillars + 1:
            raise ValueError(
                f"mapping contexte/support incohérent pour {position_ticket}: {context_pillars}p != {support_pillars}+1"
            )

        pnl_r = float(closed["pnl_r"])
        row = {
            "position_ticket": position_ticket,
            "order_ticket": order_ticket,
            "closed_event_id": str(closed["event_id"]),
            "symbol": symbol,
            "side": int(side),
            "asset_class": closed.get("asset_class"),
            "regime": closed.get("regime"),
            "mode": closed.get("mode"),
            "timeframe": trade.get("timeframe"),
            "context": context,
            "context_pillars": context_pillars,
            "support_pillars": support_pillars,
            "diagnostic_support_label": f"{support_pillars} piliers de support",
            "quorum": int(trade.get("quorum")),
            "placed_at": placed.get("at"),
            "closed_at": closed.get("closed_at"),
            "ts_open": excursion.get("ts_open"),
            "ts_exit": excursion.get("ts_exit"),
            "planned_price": closed.get("planned_price"),
            "market_reference_price": closed.get("market_reference_price"),
            "fill_price": closed.get("fill_price"),
            "entry_price": excursion.get("entry"),
            "exit_price": closed.get("exit_price"),
            "sl_initial": excursion.get("sl_initial"),
            "tp_initial": excursion.get("tp_initial"),
            "r_unit": excursion.get("r_unit"),
            "spread_r": placed.get("spread_r"),
            "cost_r": closed.get("cost_r"),
            "target_saving_r": closed.get("target_saving_r"),
            "realized_saving_r": closed.get("realized_saving_r"),
            "slippage_r": closed.get("slippage_r"),
            "pnl_r": pnl_r,
            "exit_reason": exit_reason,
            "mfe_r": excursion.get("mfe_r"),
            "mae_r": excursion.get("mae_r"),
            "giveback_r": excursion.get("giveback_r"),
            "is_negative": pnl_r < 0.0,
            "is_exit_reason_init": exit_reason == "init",
        }
        cohort.append(row)
        selected_placed.append(placed)
        selected_trades.append(trade)
        selected_excursions.append(excursion)

    reason_counts = Counter(row["exit_reason"] for row in cohort)
    reason_sign_counts = Counter(
        f"{row['exit_reason']}_{'negative' if row['pnl_r'] < 0 else 'positive' if row['pnl_r'] > 0 else 'zero'}"
        for row in cohort
    )
    context_counts = Counter(f"{row['context_pillars']}p" for row in cohort)
    support_counts = Counter(str(row["support_pillars"]) for row in cohort)
    quorum_counts = Counter(str(row["quorum"]) for row in cohort)

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "through_closed_event_id": through_closed_event_id,
        "cohort_count": len(cohort),
        "cohort": cohort,
    }
    artifact_bytes = canonical_bytes(artifact)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact": {
            "sha256": sha256_bytes(artifact_bytes),
            "cohort_count": len(cohort),
        },
        "cutoff": {
            "through_closed_event_id": through_closed_event_id,
            "lifecycle_line": through_line,
            "event_at": cutoff_row.get("at"),
            "closed_at": cutoff_row.get("closed_at"),
        },
        "joins": {
            "closed_to_trade": "normalize(closed.position_ticket) == normalize(trade.ticket)",
            "closed_to_excursion": "normalize(closed.position_ticket) == normalize(excursion.ticket)",
            "closed_to_placed": "normalize(closed.order_ticket) == normalize(placed.order_ticket)",
            "heuristic_join_allowed": False,
        },
        "source_seals": {
            "lifecycle_prefix": {
                "path": lifecycle_path.as_posix(),
                "through_line": through_line,
                "sha256": sha256_bytes(lifecycle_prefix_bytes(lifecycle_data, through_line)),
                "closed_rows": len(closed_rows),
                "placed_rows_selected": len(selected_placed),
                "placed_rows_sha256": selected_rows_hash(selected_placed, ticket_field="order_ticket"),
            },
            "trades_selected": {
                "path": trades_path.as_posix(),
                "rows": len(selected_trades),
                "sha256": selected_rows_hash(selected_trades, ticket_field="ticket"),
            },
            "excursions_selected": {
                "path": excursions_path.as_posix(),
                "rows": len(selected_excursions),
                "sha256": selected_rows_hash(selected_excursions, ticket_field="ticket"),
            },
        },
        "semantics": {
            "context_suffix": "N p = piliers totaux encodés dans context",
            "support_pillars": "piliers de support diagnostiques; pour cette cohorte N p = support_pillars + 1",
            "exit_reason_init": "gestion de stop restée au stade init; ne signifie pas automatiquement pnl négatif",
            "all_negative": "toutes les clôtures avec pnl_r < 0, tous exit_reason confondus",
        },
        "counts": {
            "exit_reason": dict(sorted(reason_counts.items())),
            "exit_reason_by_sign": dict(sorted(reason_sign_counts.items())),
            "all_negative": sum(row["is_negative"] for row in cohort),
            "init_negative": sum(row["is_negative"] and row["is_exit_reason_init"] for row in cohort),
            "context_suffix": dict(sorted(context_counts.items())),
            "support_pillars": dict(sorted(support_counts.items())),
            "quorum": dict(sorted(quorum_counts.items())),
        },
    }
    return artifact, manifest


def verify_sealed_cohort(cohort_path: Path, manifest_path: Path) -> tuple[dict, dict]:
    artifact_bytes = cohort_path.read_bytes()
    artifact = json.loads(artifact_bytes)
    manifest = json.loads(manifest_path.read_bytes())
    if sha256_bytes(artifact_bytes) != manifest.get("artifact", {}).get("sha256"):
        raise ValueError("SHA-256 de la cohorte invalide")
    if artifact.get("schema_version") != SCHEMA_VERSION or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("version de schéma incompatible")
    if len(artifact.get("cohort", [])) != manifest.get("artifact", {}).get("cohort_count"):
        raise ValueError("cardinalité de cohorte invalide")
    if artifact.get("through_closed_event_id") != manifest.get("cutoff", {}).get("through_closed_event_id"):
        raise ValueError("cutoff incohérent")
    return artifact, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle", type=Path, default=Path("results/limit_lifecycle.ndjson"))
    parser.add_argument("--trades", type=Path, default=Path("results/trades.ndjson"))
    parser.add_argument("--excursions", type=Path, default=Path("results/excursions.ndjson"))
    parser.add_argument("--through-closed-event-id", required=True)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--cohort-out", type=Path, default=Path("results/p1a/cohorte_373.json"))
    parser.add_argument("--manifest-out", type=Path, default=Path("results/p1a/cohorte_373.manifest.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact, manifest = build_sealed_cohort(
        args.lifecycle,
        args.trades,
        args.excursions,
        through_closed_event_id=args.through_closed_event_id,
        expected_count=args.expected_count,
    )
    atomic_write(args.cohort_out, canonical_bytes(artifact))
    atomic_write(args.manifest_out, canonical_bytes(manifest))
    verify_sealed_cohort(args.cohort_out, args.manifest_out)
    print(json.dumps({"cohort": str(args.cohort_out), "manifest": str(args.manifest_out), **manifest["artifact"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
