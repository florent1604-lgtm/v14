from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.sceller_cohorte_p1a import (
    atomic_write,
    build_sealed_cohort,
    canonical_bytes,
    verify_sealed_cohort,
)


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def sources(
    tmp_path: Path,
    *,
    mismatch: bool = False,
    identities: tuple[dict | None, dict | None] = (None, None),
) -> tuple[Path, Path, Path]:
    lifecycle = tmp_path / "limit_lifecycle.ndjson"
    trades = tmp_path / "trades.ndjson"
    excursions = tmp_path / "excursions.ndjson"
    write_rows(lifecycle, [
        {
            "event": "placed", "event_id": "101:placed", "order_ticket": 101, "symbol": "BTCUSD",
            "side": 1, "context": "BTCUSD|long|trend|3p", "at": "2026-08-01T00:00:00Z",
            "asset_class": "crypto", "mode": "explore", "regime": "trend", "spread_r": 0.02,
            **(identities[0] or {}),
        },
        {
            "event": "closed", "event_id": "101:closed", "order_ticket": 101, "position_ticket": 101,
            "symbol": "BTCUSD", "side": 1, "context": "BTCUSD|long|trend|3p", "pnl_r": -1.0,
            "closed_at": "2026-08-01T01:00:00Z", "at": "2026-08-01T01:01:00Z",
            "asset_class": "crypto", "mode": "explore", "regime": "trend",
        },
        {
            "event": "placed", "event_id": "102:placed", "order_ticket": 102, "symbol": "ETHUSD",
            "side": -1, "context": "ETHUSD|short|trend|4p", "at": "2026-08-01T02:00:00Z",
            "asset_class": "crypto", "mode": "explore", "regime": "trend", "spread_r": 0.03,
            **(identities[1] or {}),
        },
        {
            "event": "closed", "event_id": "102:closed", "order_ticket": 102, "position_ticket": 102,
            "symbol": "ETHUSD", "side": -1, "context": "ETHUSD|short|trend|4p", "pnl_r": 1.5,
            "closed_at": "2026-08-01T03:00:00Z", "at": "2026-08-01T03:01:00Z",
            "asset_class": "crypto", "mode": "explore", "regime": "trend",
        },
    ])
    write_rows(trades, [
        {"ticket": "live:101", "context": "BTCUSD|long|trend|3p", "pnl_r": -1.0, "exit_reason": "init", "support_pillars": 2, "quorum": 2, "timeframe": "H1"},
        {"ticket": "live:102", "context": "ETHUSD|short|trend|4p", "pnl_r": 1.5, "exit_reason": "trailing", "support_pillars": 3 if not mismatch else 2, "quorum": 2, "timeframe": "H1"},
    ])
    write_rows(excursions, [
        {"ticket": "live:101", "symbol": "BTCUSD", "side": 1, "context": "BTCUSD|long|trend|3p", "pnl_r": -1.0, "exit_reason": "init", "entry": 100, "exit": 99, "sl_initial": 99, "tp_initial": 102, "r_unit": 1, "mfe_r": 0.1, "mae_r": -1, "giveback_r": 1.1, "ts_open": "2026-08-01T00:00:00Z", "ts_exit": "2026-08-01T01:00:00Z"},
        {"ticket": "live:102", "symbol": "ETHUSD", "side": -1, "context": "ETHUSD|short|trend|4p", "pnl_r": 1.5, "exit_reason": "trailing", "entry": 100, "exit": 98.5, "sl_initial": 101, "tp_initial": 98, "r_unit": 1, "mfe_r": 2, "mae_r": -0.2, "giveback_r": 0.5, "ts_open": "2026-08-01T02:00:00Z", "ts_exit": "2026-08-01T03:00:00Z"},
    ])
    return lifecycle, trades, excursions


def identity(epoch: str) -> dict:
    return {
        "entry_policy": "LIMITE",
        "execution_mode": "explore",
        "policy_epoch": epoch,
        "config_sha256": "a" * 64,
        "code_sha256": "b" * 64,
    }


def test_cutoff_explicite_ignore_les_clotures_suivantes(tmp_path: Path) -> None:
    lifecycle, trades, excursions = sources(tmp_path)
    artifact, manifest = build_sealed_cohort(
        lifecycle, trades, excursions, through_closed_event_id="101:closed", expected_count=1,
    )

    assert artifact["cohort_count"] == 1
    assert artifact["cohort"][0]["position_ticket"] == "101"
    assert manifest["cutoff"]["lifecycle_line"] == 2
    assert manifest["counts"]["exit_reason"] == {"init": 1}
    assert manifest["counts"]["all_negative"] == 1
    assert manifest["counts"]["init_negative"] == 1
    assert manifest["counts"]["context_suffix"] == {"3p": 1}
    assert manifest["counts"]["support_pillars"] == {"2": 1}


def test_mapping_context_support_incoherent_est_refuse(tmp_path: Path) -> None:
    lifecycle, trades, excursions = sources(tmp_path, mismatch=True)
    with pytest.raises(ValueError, match="mapping contexte/support incohérent"):
        build_sealed_cohort(lifecycle, trades, excursions, through_closed_event_id="102:closed")


def test_verification_refuse_une_cohorte_modifiee(tmp_path: Path) -> None:
    lifecycle, trades, excursions = sources(tmp_path)
    artifact, manifest = build_sealed_cohort(
        lifecycle, trades, excursions, through_closed_event_id="102:closed", expected_count=2,
    )
    cohort_path = tmp_path / "cohort.json"
    manifest_path = tmp_path / "manifest.json"
    atomic_write(cohort_path, canonical_bytes(artifact))
    atomic_write(manifest_path, canonical_bytes(manifest))
    verify_sealed_cohort(cohort_path, manifest_path)

    artifact["cohort"][0]["pnl_r"] = 42
    atomic_write(cohort_path, canonical_bytes(artifact))
    with pytest.raises(ValueError, match="SHA-256"):
        verify_sealed_cohort(cohort_path, manifest_path)


def test_jointure_manquante_est_refusee(tmp_path: Path) -> None:
    lifecycle, trades, excursions = sources(tmp_path)
    write_rows(trades, [])
    with pytest.raises(ValueError, match="trade manquant"):
        build_sealed_cohort(lifecycle, trades, excursions, through_closed_event_id="101:closed")


def test_identite_complete_est_scellee_dans_le_manifeste(tmp_path: Path) -> None:
    policy = identity("epoch-a")
    lifecycle, trades, excursions = sources(
        tmp_path, identities=(policy, policy),
    )
    _, manifest = build_sealed_cohort(
        lifecycle, trades, excursions,
        through_closed_event_id="102:closed", expected_count=2,
    )

    assert manifest["cohort_identity"] == policy


def test_plusieurs_epoques_sont_refusees_ou_filtrees(tmp_path: Path) -> None:
    first = identity("epoch-a")
    second = identity("epoch-b")
    lifecycle, trades, excursions = sources(
        tmp_path, identities=(first, second),
    )
    with pytest.raises(ValueError, match="plusieurs politiques"):
        build_sealed_cohort(
            lifecycle, trades, excursions, through_closed_event_id="102:closed",
        )

    artifact, manifest = build_sealed_cohort(
        lifecycle, trades, excursions,
        through_closed_event_id="102:closed", expected_count=1,
        policy_epoch="epoch-b",
    )
    assert artifact["cohort_count"] == 1
    assert artifact["cohort"][0]["position_ticket"] == "102"
    assert manifest["cohort_identity"] == second


def test_legacy_et_identite_ne_sont_jamais_pooles(tmp_path: Path) -> None:
    lifecycle, trades, excursions = sources(
        tmp_path, identities=(None, identity("epoch-a")),
    )
    with pytest.raises(ValueError, match="legacy"):
        build_sealed_cohort(
            lifecycle, trades, excursions, through_closed_event_id="102:closed",
        )
