"""Audit offline; never imports MT5, live_demo, provider config or an execution path."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from titanium.analysis.modernisation import audit, read_prefix  # noqa: E402

FILES = ("trades.ndjson", "excursions.ndjson", "limit_lifecycle.ndjson", "decision_registry.ndjson")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument(
        "--snapshot", type=Path, help="Reuse prefix lengths and SHA-256 from a prior report"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new name, never overwrite evidence")
    previous = (
        json.loads(args.snapshot.read_text(encoding="utf-8"))["snapshot"] if args.snapshot else {}
    )
    if args.snapshot and set(previous) != set(FILES):
        parser.error(
            "snapshot must contain all four journal prefixes; mixing snapshots is forbidden"
        )
    data, proofs = [], {}
    for name in FILES:
        rows, proof = read_prefix(args.results / name, previous.get(name))
        data.append(rows)
        proofs[name] = proof
    report = {"schema": "v14.modernisation.audit.v1", "snapshot": proofs, **audit(*data)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if not k.startswith("regime_")},
            ensure_ascii=False,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
