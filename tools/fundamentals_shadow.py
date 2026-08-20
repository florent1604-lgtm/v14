"""Produit des scores fondamentaux SHADOW depuis un journal NDJSON causal.

Exemple :
    .venv\\Scripts\\python.exe tools\\fundamentals_shadow.py \
        --symbols EURUSD USTECH XAUUSD

L'outil ne telecharge aucune donnee et ne touche ni MT5 ni RiskGate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from titanium.fundamentals import (  # noqa: E402
    SCHEMA_VERSION,
    SHADOW_MODE,
    FundamentalRecord,
    score_fundamentals,
)

DEFAULT_INPUT = ROOT / "results" / "fundamentals_observations.ndjson"
DEFAULT_OUTPUT = ROOT / "results" / "fundamentals_shadow.json"


def load_records(path: Path) -> list[FundamentalRecord]:
    """Charge un journal append-only ; une ligne invalide est une erreur visible."""
    if not path.is_file():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if int(raw.get("schema_version", 0)) != SCHEMA_VERSION:
                raise ValueError("schema_version non supporte")
            records.append(FundamentalRecord.from_dict(raw))
        except Exception as exc:  # noqa: BLE001 - le numero de ligne est la preuve
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return records


def write_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--observations", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--as-of", default=None, help="ISO-8601, UTC par defaut")
    args = parser.parse_args(argv)

    as_of = args.as_of or datetime.now(timezone.utc).isoformat()
    records = load_records(args.observations)
    snapshots = [
        score_fundamentals(symbol, records, as_of=as_of).to_dict() for symbol in args.symbols
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": SHADOW_MODE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": snapshots[0]["as_of"],
        "observations": len(records),
        "snapshots": snapshots,
    }
    write_snapshot(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
