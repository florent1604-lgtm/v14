from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.analyse_pertes import (
    aggregate_clean_cohort,
    build_report,
    load_sealed_artifact,
    seal_excursions,
    write_json_deterministic,
)

GAP = {
    "start": "2026-08-12T22:15:25.770935+00:00",
    "end": "2026-08-13T05:17:17.751153+00:00",
}


def _row(
    ticket: str,
    reason: str,
    pnl_r: float,
    mfe_r: float,
    opened: str,
    closed: str,
    *,
    censored: bool = False,
) -> dict:
    return {
        "ticket": ticket,
        "exit_reason": reason,
        "pnl_r": pnl_r,
        "mfe_r": mfe_r,
        "ts_open": opened,
        "ts_exit": closed,
        "censored": censored,
    }


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_sceau_exclut_les_positions_traversant_un_trou_sans_reutiliser_censored(
    tmp_path: Path,
) -> None:
    source = tmp_path / "excursions.ndjson"
    _write_rows(source, [
        _row(
            "clean",
            "init",
            -1.0,
            0.04,
            "2026-08-12T20:00:00+00:00",
            "2026-08-12T21:00:00+00:00",
            censored=True,
        ),
        _row(
            "crosses",
            "trailing",
            0.5,
            1.3,
            "2026-08-12T20:00:00+00:00",
            "2026-08-13T06:00:00+00:00",
            censored=False,
        ),
    ])

    artifact = seal_excursions(source, runtime_gaps=[GAP])

    assert artifact["cohort"][0]["censored"] is True
    assert artifact["cohort"][0]["runtime_gap_censored"] is False
    assert artifact["excluded"][0]["ticket"] == "crosses"
    assert artifact["excluded"][0]["runtime_gap_censored"] is True
    assert artifact["counts"] == {
        "source": 2,
        "clean": 1,
        "runtime_gap_censored": 1,
    }


def test_agregats_et_bins_sont_calcules_uniquement_sur_la_cohorte_propre(
    tmp_path: Path,
) -> None:
    source = tmp_path / "excursions.ndjson"
    _write_rows(source, [
        _row("i1", "init", -1.0, 0.04, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
        _row("i2", "init", -0.8, 0.10, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
        _row("i3", "init", -0.9, 0.30, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
        _row("i4", "init", -1.1, 0.70, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
        _row("b1", "breakeven", 0.0, 0.90, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
        _row("t1", "trailing", 1.2, 1.50, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
    ])
    artifact = seal_excursions(source, runtime_gaps=[])

    analysis = aggregate_clean_cohort(artifact)

    assert analysis["by_exit_reason"] == {
        "init": {"n": 4, "sum_pnl_r": -3.8, "mean_pnl_r": -0.95},
        "breakeven": {"n": 1, "sum_pnl_r": 0.0, "mean_pnl_r": 0.0},
        "trailing": {"n": 1, "sum_pnl_r": 1.2, "mean_pnl_r": 1.2},
    }
    assert [item["n"] for item in analysis["init_mfe_bins"]] == [1, 1, 1, 1]
    assert sum(item["n"] for item in analysis["init_mfe_bins"]) == analysis["by_exit_reason"]["init"]["n"]


def test_artefact_deterministe_est_refuse_apres_modification(tmp_path: Path) -> None:
    source = tmp_path / "excursions.ndjson"
    _write_rows(source, [
        _row("i1", "init", -1.0, 0.0, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
    ])
    first = seal_excursions(source, runtime_gaps=[])
    second = seal_excursions(source, runtime_gaps=[])
    assert first == second

    artifact_path = tmp_path / "sealed.json"
    write_json_deterministic(artifact_path, first)
    assert load_sealed_artifact(artifact_path) == first

    tampered = copy.deepcopy(first)
    tampered["cohort"][0]["pnl_r"] = 42
    write_json_deterministic(artifact_path, tampered)
    with pytest.raises(ValueError, match="SHA-256"):
        load_sealed_artifact(artifact_path)


def test_sceau_peut_figer_la_cohorte_a_un_ticket_sans_supprimer_la_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "excursions.ndjson"
    rows = [
        _row("first", "init", -1.0, 0.0, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
        _row("seal-here", "init", -1.0, 0.1, "2026-08-12T11:00:00Z", "2026-08-12T12:00:00Z"),
        _row("later", "trailing", 1.0, 1.0, "2026-08-12T12:00:00Z", "2026-08-12T13:00:00Z"),
    ]
    _write_rows(source, rows)

    artifact = seal_excursions(source, runtime_gaps=[], through_ticket="seal-here")
    _write_rows(source, rows + [
        _row("even-later", "init", -1.0, 0.0, "2026-08-12T13:00:00Z", "2026-08-12T14:00:00Z"),
    ])
    regenerated = seal_excursions(source, runtime_gaps=[], through_ticket="seal-here")

    assert artifact["counts"]["source"] == 2
    assert [row["ticket"] for row in artifact["cohort"]] == ["first", "seal-here"]
    assert regenerated == artifact


def test_rapport_formule_une_conclusion_descriptive_non_causale(tmp_path: Path) -> None:
    source = tmp_path / "excursions.ndjson"
    _write_rows(source, [
        _row("i1", "init", -1.0, 0.0, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
        _row("t1", "trailing", 0.5, 1.0, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
    ])
    artifact = seal_excursions(source, runtime_gaps=[])

    report = build_report(artifact)

    conclusion = (
        "Les pertes observées sont concentrées dans les stops initiaux ; "
        "l’hypothèse prioritaire concerne la sélection/entrée."
    )
    assert conclusion in report
    assert "La gestion de sortie fonctionne" not in report
    assert artifact["seal_sha256"] in report


def test_rapport_manifeste_les_gaps_et_la_borne_du_sceau(tmp_path: Path) -> None:
    source = tmp_path / "excursions.ndjson"
    _write_rows(source, [
        _row("seal-here", "init", -1.0, 0.0, "2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z"),
    ])
    artifact = seal_excursions(
        source,
        runtime_gaps=[GAP],
        through_ticket="seal-here",
    )

    report = build_report(artifact)

    assert "seal-here" in report
    assert GAP["start"] in report
    assert GAP["end"] in report
