"""Compare the 15 V14 execution policies in deterministic dry-run scenarios."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from titanium.execution_sim.config import load_config  # noqa: E402
from titanium.execution_sim.runner import (  # noqa: E402
    ALL_POLICIES,
    MatrixSpec,
    reproducible_run_id,
    run_matrix,
    write_reports,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtest-v14-execution-matrix",
        description="Matrice d'exécution V14 — exclusivement backtest/dry-run",
    )
    parser.add_argument("--config", default=str(ROOT / "config" / "execution_backtest.json"))
    parser.add_argument("--output", default=str(ROOT / "results" / "execution_matrix"))
    parser.add_argument(
        "--policy",
        choices=("all", *ALL_POLICIES),
        default="all",
        help="une politique ou les quinze",
    )
    parser.add_argument("--seed", type=int, default=14_082_026)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--quick", action="store_true", help="matrice réduite de validation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    policies = ALL_POLICIES if args.policy == "all" else (args.policy,)
    spec = MatrixSpec(
        policies=tuple(policies),
        seed=args.seed,
        quick=args.quick,
        jobs=max(1, args.jobs),
    )
    run_id = reproducible_run_id(spec, config)
    rows = run_matrix(spec, config)
    outputs = write_reports(rows, args.output, run_id=run_id, config=config)
    print(f"run_id={run_id} policies={len(policies)} cases={len(rows)} live_enabled=false")
    for name, path in outputs.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
