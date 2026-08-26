from __future__ import annotations

import copy
import hashlib
import json

from tools import banc_ab_entrees as banc
from tools.sceller_cohorte_p1a import atomic_write, canonical_bytes
from tools.sceller_decisions_entree import SCHEMA_VERSION, build_sealed_decisions


def _identity(epoch="epoch-a"):
    return {
        "entry_policy": "MARCHE",
        "execution_mode": "explore",
        "policy_epoch": epoch,
        "config_sha256": "a" * 64,
        "code_sha256": "b" * 64,
    }


def _decided(decision_id, at, **overrides):
    row = {
        "event": "decided", "decision_id": decision_id,
        "decision_at": at, "execution_ticket": decision_id.split(":")[-1],
        "symbol": "BTCUSD", "side": 1, "asset_class": "crypto",
        "context": "BTCUSD|long|trend|4p", "timeframe": "M15",
        "quorum": 2, "support_pillars": 3,
        **_identity(),
    }
    row.update(overrides)
    return row


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )


def test_scelleur_inclut_les_ouvertes_et_bloque_la_phase_un(tmp_path):
    registry = tmp_path / "decision_registry.ndjson"
    _write(registry, [
        _decided("epoch-a:1", "2026-08-26T08:00:00+00:00"),
        {"event": "resolved", "decision_id": "epoch-a:1", "pnl_r": 1.0,
         "closed_at": "2026-08-26T09:00:00+00:00",
         "ts_exit": "2026-08-26T09:00:00+00:00", "exit_reason": "trailing",
         "giveback_r": 0.2, "mae_r": -0.1, "mfe_r": 1.2},
        _decided("epoch-a:2", "2026-08-26T08:15:00+00:00"),
        _decided("epoch-a:3", "2026-08-26T11:00:00+00:00"),
    ])
    cutoff = "2026-08-26T10:00:00+00:00"
    artifact, manifest = build_sealed_decisions(
        registry, decision_cutoff=cutoff, expected_count=2,
    )

    assert manifest["counts"] == {"eligible": 2, "closed": 1, "open": 1}
    assert manifest["decision_ids"]["open"] == ["epoch-a:2"]
    cohort_path = tmp_path / "cohort.json"
    manifest_path = tmp_path / "manifest.json"
    atomic_write(cohort_path, canonical_bytes(artifact))
    atomic_write(manifest_path, canonical_bytes(manifest))
    spec = copy.deepcopy(banc.load_spec())
    spec["source"] = {
        "artifact": cohort_path.name,
        "artifact_schema": SCHEMA_VERSION,
        "artifact_sha256": hashlib.sha256(cohort_path.read_bytes()).hexdigest(),
        "manifest": manifest_path.name,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "decision_cutoff": cutoff,
    }
    rows, identity, _ = banc.load_sealed_cohort(spec, root=tmp_path)
    frozen = banc.prepare_phase_one(rows, spec, identity, cutoff=cutoff)

    assert frozen["state"] == "ANALYSIS_BLOCKED"
    assert frozen["reason"] == "OPEN_DECISIONS"
    assert frozen["counts"] == {"eligible": 2, "closed": 1, "open": 1}


def test_scelleur_ne_pool_jamais_deux_epoques(tmp_path):
    registry = tmp_path / "decision_registry.ndjson"
    _write(registry, [
        _decided("epoch-a:1", "2026-08-26T08:00:00+00:00"),
        _decided(
            "epoch-b:2", "2026-08-26T08:15:00+00:00",
            policy_epoch="epoch-b",
        ),
    ])

    import pytest
    with pytest.raises(ValueError, match="plusieurs politiques"):
        build_sealed_decisions(
            registry, decision_cutoff="2026-08-26T10:00:00+00:00",
        )
