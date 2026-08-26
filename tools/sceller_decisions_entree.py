"""Scelle les décisions MARCHE/LIMITE, y compris celles encore ouvertes."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.sceller_cohorte_p1a import (
    COHORT_IDENTITY_FIELDS,
    atomic_write,
    canonical_bytes,
    parse_ndjson,
    placed_identity,
    stable_read,
)

SCHEMA_VERSION = "v14.entry-decisions.sealed-cohort.v1"


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("horodatage sans fuseau")
    return parsed.astimezone(timezone.utc)


def _unique(rows: list[tuple[int, dict]], event: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for line, row in rows:
        if row.get("event") != event:
            continue
        decision_id = str(row.get("decision_id", "") or "")
        if not decision_id:
            raise ValueError(f"decision_id absent ligne {line}")
        if decision_id in indexed:
            raise ValueError(f"{event} dupliqué pour {decision_id}")
        indexed[decision_id] = row
    return indexed


def build_sealed_decisions(
    registry_path: Path,
    *,
    decision_cutoff: str,
    policy_epoch: str | None = None,
    expected_count: int | None = None,
) -> tuple[dict, dict]:
    """Fige l'univers décidé avant cutoff, puis joint les résolutions connues."""
    registry_bytes = stable_read(registry_path)
    events = parse_ndjson(registry_bytes, registry_path)
    decided = _unique(events, "decided")
    resolved = _unique(events, "resolved")
    unknown = sorted(set(resolved) - set(decided))
    if unknown:
        raise ValueError(f"résolutions orphelines: {unknown[:3]}")
    cutoff = _utc(decision_cutoff)

    eligible: list[tuple[dict, dict[str, str]]] = []
    for decision_id, row in decided.items():
        decided_at = _utc(str(row.get("decision_at") or row.get("at") or ""))
        if decided_at > cutoff:
            continue
        identity = placed_identity(row)
        if identity is None:
            raise ValueError(f"identité absente pour {decision_id}")
        if policy_epoch is not None and identity["policy_epoch"] != str(policy_epoch):
            continue
        eligible.append((row, identity))
    if not eligible:
        raise ValueError("aucune décision éligible")
    identities = {canonical_bytes(identity) for _, identity in eligible}
    if len(identities) > 1:
        raise ValueError("plusieurs politiques; utiliser --policy-epoch")
    cohort_identity = eligible[0][1]
    if expected_count is not None and len(eligible) != expected_count:
        raise ValueError(
            f"cohorte inattendue: {len(eligible)} décisions, attendu {expected_count}",
        )

    cohort: list[dict] = []
    open_ids: list[str] = []
    closed_ids: list[str] = []
    for decision, _ in sorted(eligible, key=lambda item: item[0]["decision_id"]):
        decision_id = str(decision["decision_id"])
        outcome = resolved.get(decision_id)
        if outcome is None:
            open_ids.append(decision_id)
        else:
            closed_ids.append(decision_id)
        pnl_r = None if outcome is None else outcome.get("pnl_r")
        cohort.append({
            "decision_id": decision_id,
            "position_ticket": str(decision.get("execution_ticket", "")),
            "symbol": decision.get("symbol"),
            "side": decision.get("side"),
            "asset_class": decision.get("asset_class"),
            "context": decision.get("context"),
            "mode": decision.get("execution_mode"),
            "timeframe": decision.get("timeframe"),
            "quorum": decision.get("quorum"),
            "support_pillars": decision.get("support_pillars"),
            "placed_at": decision.get("decision_at"),
            "ts_open": decision.get("decision_at"),
            "closed_at": None if outcome is None else outcome.get("closed_at"),
            "ts_exit": None if outcome is None else outcome.get("ts_exit"),
            "pnl_r": pnl_r,
            "exit_reason": None if outcome is None else outcome.get("exit_reason"),
            "giveback_r": None if outcome is None else outcome.get("giveback_r"),
            "mae_r": None if outcome is None else outcome.get("mae_r"),
            "mfe_r": None if outcome is None else outcome.get("mfe_r"),
            "is_negative": None if pnl_r is None else float(pnl_r) < 0.0,
        })

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "decision_cutoff": cutoff.isoformat(),
        "cohort_count": len(cohort),
        "cohort": cohort,
    }
    artifact_bytes = canonical_bytes(artifact)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact": {
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "cohort_count": len(cohort),
        },
        "cutoff": {"decision_cutoff": cutoff.isoformat()},
        "cohort_identity": cohort_identity,
        "counts": {
            "eligible": len(cohort),
            "closed": len(closed_ids),
            "open": len(open_ids),
        },
        "decision_ids": {
            "closed": closed_ids,
            "open": open_ids,
        },
        "source_seals": {
            "decision_registry": {
                "path": Path(registry_path).as_posix(),
                "sha256": hashlib.sha256(registry_bytes).hexdigest(),
                "events": len(events),
            },
        },
        "identity_fields": list(COHORT_IDENTITY_FIELDS),
    }
    return artifact, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("results/decision_registry.ndjson"))
    parser.add_argument("--decision-cutoff", required=True)
    parser.add_argument("--policy-epoch")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--cohort-out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    args = parser.parse_args()
    artifact, manifest = build_sealed_decisions(
        args.registry, decision_cutoff=args.decision_cutoff,
        policy_epoch=args.policy_epoch, expected_count=args.expected_count,
    )
    atomic_write(args.cohort_out, canonical_bytes(artifact))
    atomic_write(args.manifest_out, canonical_bytes(manifest))
    print(json.dumps({
        "cohort": str(args.cohort_out),
        "manifest": str(args.manifest_out),
        **manifest["counts"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
