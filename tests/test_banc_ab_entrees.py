from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools import banc_ab_entrees as banc

UTC = timezone.utc


def _identity(**overrides):
    identity = {
        "entry_policy": "LIMITE",
        "execution_mode": "explore",
        "policy_epoch": "epoch-a",
        "config_sha256": "a" * 64,
        "code_sha256": "b" * 64,
    }
    identity.update(overrides)
    return identity


def _row(index: int, *, four_p: bool = False, open_decision: bool = False):
    day = index % 20
    opened = datetime(2026, 1, 1, 10, tzinfo=UTC) + timedelta(days=day, seconds=index)
    row = {
        "asset_class": "crypto",
        "context": f"SYM{index % 30}|long|continuation|{'4p' if four_p else '3p'}",
        "mode": "explore",
        "placed_at": (opened + timedelta(seconds=2)).isoformat(),
        "position_ticket": str(100_000 + index),
        "quorum": 2,
        "side": 1,
        "support_pillars": 3 if four_p else 2,
        "symbol": f"SYM{index % 30}",
        "timeframe": "M15",
        "ts_open": opened.isoformat(),
        "pnl_r": 1.0 if index % 2 else -1.0,
        "mae_r": -0.5,
        "mfe_r": 0.8,
    }
    if not open_decision:
        row["closed_at"] = (opened + timedelta(hours=1)).isoformat()
        row["ts_exit"] = row["closed_at"]
    return row


def _powered_rows():
    return [_row(i, four_p=i < 125) for i in range(814)]


def test_specification_is_sealed_and_has_no_secondary_family():
    spec = banc.load_spec()

    assert banc.spec_sha256(spec) == banc.spec_file_sha256()
    assert spec["primary"]["gate"]["mde_r"] == pytest.approx(0.487)
    assert spec["primary"]["gate"]["variance_model"] == (
        "two_way_symbol_x_decision_day_bootstrap"
    )
    assert spec["secondary_hypotheses"] == {
        "enabled": False,
        "bh_alpha": 0.05,
        "cardinality": 0,
        "items": [],
    }


@pytest.mark.parametrize(
    ("r", "expected"),
    [
        (1.35, (1.0 - 1.35) / 3.0),
        (2.81, (1.0 - 2.81) / 3.0),
    ],
)
def test_b_r_uses_fixed_decision_denominator(r, expected):
    outcomes = [1.0, -1.0, 2.0]
    pillars = [2, 3, 3]

    assert banc.relative_b_delta(outcomes, pillars, r=r) == pytest.approx(expected)


def test_state_priority_is_exact():
    assert banc.select_state(
        integrity_ok=False, identifiable=False, powered=False, resolved=False,
    ) == "ANALYSIS_BLOCKED"
    assert banc.select_state(
        integrity_ok=True, identifiable=False, powered=False, resolved=True,
    ) == "NOT_IDENTIFIABLE"
    assert banc.select_state(
        integrity_ok=True, identifiable=True, powered=False, resolved=True,
    ) == "NOT_POWERED"
    assert banc.select_state(
        integrity_ok=True, identifiable=True, powered=True, resolved=True,
    ) == "EXPLORATORY_MEASURED"


def test_phase_one_refuses_outcome_access():
    guarded = banc.PhaseOneRow(_row(1), banc.load_spec())

    with pytest.raises(banc.OutcomeAccessError, match="phase 1"):
        _ = guarded["pnl_r"]


def test_open_decision_blocks_before_outcomes():
    rows = [_row(0), _row(1, open_decision=True)]

    frozen = banc.prepare_phase_one(rows, banc.load_spec(), _identity())

    assert frozen["state"] == "ANALYSIS_BLOCKED"
    assert frozen["counts"] == {"eligible": 2, "closed": 1, "open": 1}
    with pytest.raises(banc.OutcomeAccessError):
        banc.evaluate_phase_two(frozen, rows, banc.load_spec())


def test_incomplete_historical_seals_block_instead_of_being_invented():
    identity = _identity(config_sha256=None, code_sha256=None)

    frozen = banc.prepare_phase_one([_row(0)], banc.load_spec(), identity)

    assert frozen["state"] == "ANALYSIS_BLOCKED"
    assert frozen["reason"] == "COHORT_IDENTITY_INCOMPLETE"


def test_mode_or_epoch_change_creates_a_distinct_cohort_id():
    base = banc.cohort_id(_identity())

    assert banc.cohort_id(_identity(execution_mode="market")) != base
    assert banc.cohort_id(_identity(policy_epoch="epoch-b")) != base


def test_primary_gate_is_computed_without_reading_outcomes():
    rows = _powered_rows()
    first = banc.prepare_phase_one(rows, banc.load_spec(), _identity())
    for row in rows:
        row["pnl_r"] *= -999.0
    second = banc.prepare_phase_one(rows, banc.load_spec(), _identity())

    assert first["state"] == "EXPLORATORY_MEASURED"
    assert first["gate"] == {
        "four_p": 125,
        "total": 814,
        "decision_days": 20,
        "symbols": 30,
        "powered": True,
    }
    assert second["mask_sha256"] == first["mask_sha256"]
    assert second["gate"] == first["gate"]


def test_underpowered_primary_never_loads_an_outcome():
    rows = [_row(i, four_p=i < 5) for i in range(40)]
    frozen = banc.prepare_phase_one(rows, banc.load_spec(), _identity())

    assert frozen["state"] == "NOT_POWERED"
    with pytest.raises(banc.OutcomeAccessError, match="NOT_POWERED"):
        banc.evaluate_phase_two(frozen, rows, banc.load_spec())


def test_duplicate_composite_identity_blocks_analysis():
    rows = [_row(0), _row(0)]

    frozen = banc.prepare_phase_one(rows, banc.load_spec(), _identity())

    assert frozen["state"] == "ANALYSIS_BLOCKED"
    assert frozen["reason"] == "DUPLICATE_DECISION_ID"


def test_exact_overlap_purge_keeps_no_intersecting_training_interval():
    validation_start = datetime(2026, 1, 3, tzinfo=UTC)
    validation_end = datetime(2026, 1, 4, tzinfo=UTC)
    training = [
        {
            "decision_proxy_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "ts_exit": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
        },
        {
            "decision_proxy_at": datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
            "ts_exit": datetime(2026, 1, 3, 1, tzinfo=UTC).isoformat(),
        },
        {
            "decision_proxy_at": datetime(2026, 1, 5, tzinfo=UTC).isoformat(),
            "ts_exit": datetime(2026, 1, 6, tzinfo=UTC).isoformat(),
        },
    ]

    kept, counts = banc.purge_overlaps(
        training, validation_start=validation_start, validation_end=validation_end,
    )

    assert len(kept) == 2
    assert counts == {"before": 3, "after": 2, "purged": 1}
    assert all(
        banc.parse_utc(row["ts_exit"]) < validation_start
        or banc.parse_utc(row["decision_proxy_at"]) > validation_end
        for row in kept
    )


def test_phase_two_has_no_secondary_outcome_path():
    rows = _powered_rows()
    frozen = banc.prepare_phase_one(rows, banc.load_spec(), _identity())

    result = banc.evaluate_phase_two(frozen, rows, banc.load_spec())

    assert result["secondary"] == []
    assert result["status"] == "EXPLORATORY_MEASURED"


def test_phase_two_refuses_a_mask_or_spec_changed_after_freeze():
    rows = _powered_rows()
    spec = banc.load_spec()
    frozen = banc.prepare_phase_one(rows, spec, _identity())
    rows[0]["position_ticket"] = "tampered"

    with pytest.raises(banc.ContractError, match="masque"):
        banc.evaluate_phase_two(frozen, rows, spec)

    rows = _powered_rows()
    frozen = banc.prepare_phase_one(rows, spec, _identity())
    changed = {**spec, "schema_version": "changed-after-freeze"}
    with pytest.raises(banc.ContractError, match="specification"):
        banc.evaluate_phase_two(frozen, rows, changed)
