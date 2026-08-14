from __future__ import annotations

import json

from tools.backtest_execution_matrix import main


def test_cli_runs_one_policy_and_writes_all_reports(tmp_path):
    code = main(
        [
            "--config",
            "config/execution_backtest.json",
            "--output",
            str(tmp_path),
            "--policy",
            "market",
            "--quick",
            "--seed",
            "123",
        ]
    )
    assert code == 0
    payload = json.loads((tmp_path / "execution_matrix.json").read_text(encoding="utf-8"))
    assert {row["policy"] for row in payload["rows"]} == {"market"}
    assert payload["safety"] == {"live_enabled": False, "mode": "backtest/dry-run"}


def test_cli_all_selects_fifteen_policies(tmp_path):
    assert main(["--output", str(tmp_path), "--policy", "all", "--quick"]) == 0
    payload = json.loads((tmp_path / "execution_matrix.json").read_text(encoding="utf-8"))
    assert len({row["policy"] for row in payload["rows"]}) == 15
