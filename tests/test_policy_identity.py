from __future__ import annotations

from pathlib import Path

import pytest

from titanium.execution.policy_identity import build_policy_identity


def _identity(tmp_path: Path, *, value: int = 1) -> dict[str, str]:
    source = tmp_path / "engine.py"
    source.write_text(f"VALUE = {value}\n", encoding="utf-8")
    return build_policy_identity(
        entry_policy="limite",
        execution_mode="explore",
        config={"risk_cap": 6.0, "rr_ratio": 2.0},
        root=tmp_path,
        code_paths=["engine.py"],
    )


def test_identite_est_deterministe_et_complete(tmp_path: Path) -> None:
    first = _identity(tmp_path)
    second = _identity(tmp_path)

    assert first == second
    assert first["entry_policy"] == "LIMITE"
    assert first["execution_mode"] == "explore"
    assert len(first["policy_epoch"]) == 16
    assert len(first["config_sha256"]) == 64
    assert len(first["code_sha256"]) == 64


def test_code_ou_config_differents_ouvrent_une_nouvelle_epoque(
    tmp_path: Path,
) -> None:
    base = _identity(tmp_path, value=1)
    changed_code = _identity(tmp_path, value=2)
    changed_config = build_policy_identity(
        entry_policy="LIMITE",
        execution_mode="explore",
        config={"risk_cap": 5.0, "rr_ratio": 2.0},
        root=tmp_path,
        code_paths=["engine.py"],
    )

    assert changed_code["code_sha256"] != base["code_sha256"]
    assert changed_code["policy_epoch"] != base["policy_epoch"]
    assert changed_config["config_sha256"] != changed_code["config_sha256"]
    assert changed_config["policy_epoch"] != changed_code["policy_epoch"]


def test_identite_refuse_un_fichier_absent_ou_hors_depot(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absent"):
        build_policy_identity(
            entry_policy="MARCHE", execution_mode="explore", config={"x": 1},
            root=tmp_path, code_paths=["missing.py"],
        )
    outside = tmp_path.parent / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hors depot"):
        build_policy_identity(
            entry_policy="MARCHE", execution_mode="explore", config={"x": 1},
            root=tmp_path, code_paths=[outside],
        )
