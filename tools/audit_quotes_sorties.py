"""Verifie les archives bid/ask avant le laboratoire P2; aucune execution de trade."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from titanium.analysis.quote_quality import audit_quote_files  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symboles", nargs="+", required=True)
    parser.add_argument("--quotes", type=Path, default=ROOT / "results/quotes")
    parser.add_argument("--max-gap-ms", type=int, default=60_000)
    parser.add_argument("--json", type=Path, required=True,
                        help="nouveau rapport; ne remplace jamais un rapport existant")
    args = parser.parse_args(argv)
    if args.max_gap_ms <= 0 or any(
        not symbol or not all(c.isalnum() or c in "-_." for c in symbol)
        or symbol in (".", "..") for symbol in args.symboles
    ):
        parser.error("symboles explicites et seuil de trou positif requis")
    if args.json.suffix.lower() != ".json" or args.json.resolve().is_relative_to(args.quotes.resolve()):
        parser.error("rapport JSON requis en dehors des archives quotes")
    # Creation exclusive avant analyse : aucun ancien resultat ne sera ecrase.
    with args.json.open("x", encoding="utf-8") as output:
        assets = [audit_quote_files(sorted((args.quotes / symbol).glob("*.ndjson")),
                                   symbol=symbol, max_gap_ms=args.max_gap_ms)
                  for symbol in dict.fromkeys(args.symboles)]
        report = {"schema": "v14.exit-lab-input-audit.v1", "assets": assets,
                  "ready_for_exit_optimization": False, "trading_changes": False}
        json.dump(report, output, ensure_ascii=False, allow_nan=False, indent=2)
    for asset in assets:
        print(f"{asset['symbol']}: {asset['verdict']}, {asset['valid_quotes']} quotes, "
              f"{asset['gaps']} trous, {sum(asset['errors'].values())} anomalies")
    return 0 if all(a["verdict"] == "STRUCTURALLY_VALID" for a in assets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
