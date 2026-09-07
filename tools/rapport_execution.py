"""Etat du registre d'execution, lecture seule et sans connexion MT5."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from titanium.execution.execution_ledger import DEFAULT_PATH, ledger_summary  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)
    result = ledger_summary(args.ledger)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 1 if result["status"] in ("WAIT", "UNAVAILABLE") else 0


if __name__ == "__main__":
    raise SystemExit(main())
