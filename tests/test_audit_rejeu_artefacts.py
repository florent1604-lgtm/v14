"""Controle semantique des artefacts : un sceau vide n'est pas un succes."""

import json
from pathlib import Path

from titanium.backtest import Trade
from tools import audit_rejeu_artefacts as audit
from tools import rejeu_univers as ru


def _snapshot(symbole: str) -> dict:
    return {
        "snapshot_id": (symbole.lower() * 64)[:64],
        "schema_version": 2,
        "asset_class": "fx",
        "protocol": {"ltf": "M15"},
        "engine": [{"name": "backtest.py", "sha256": "a" * 64}],
    }


def _trade(symbole: str) -> Trade:
    return Trade(
        symbol=symbole, side=1,
        bar_entree="2026-01-01T00:00:00+00:00",
        bar_sortie="2026-01-01T01:00:00+00:00",
        prix_entree=1.0, prix_sortie=1.1, sl=0.9, tp=1.2,
        r_unit=0.1, pnl_r=1.0, cost_r=0.1, mae_r=-0.2, mfe_r=1.1,
        barres=4, motif="tp", contexte="fx|continuation|3p",
        pillars=3, family="continuation", indicators={},
    )


def _publier(root: Path, symbole: str, trades: list[Trade]) -> None:
    resumes = root / "results" / "rejeu_univers"
    resumes.mkdir(parents=True, exist_ok=True)
    n = len(trades)
    resume = {
        "symbole": symbole,
        "global": {"n": n},
        "calibration": {"n": n},
        "verification": {"n": 0},
    }
    resume_bytes = json.dumps(resume).encode("utf-8")
    resume_path = resumes / f"{symbole}.json"
    brut, manifeste = ru.construire_artefact_brut(
        symbole, trades, coupure="2026-02-01T00:00:00+00:00",
        snapshot=_snapshot(symbole),
    )
    manifeste = ru.lier_resume_au_manifeste(manifeste, symbole, resume_bytes)
    ru.persister_artefact_brut(
        root / "results" / "rejeu_univers_brut", symbole, brut, manifeste,
        resume_path=resume_path, resume=resume_bytes,
    )


def test_audit_distingue_accepte_zero_legacy_et_manquant(tmp_path):
    archives = tmp_path / "results" / "barres" / "M15"
    archives.mkdir(parents=True)
    for symbole in ("OK", "ZERO", "LEGACY", "MISSING"):
        (archives / f"{symbole}.parquet").write_bytes(b"")

    _publier(tmp_path, "OK", [_trade("OK")])
    _publier(tmp_path, "ZERO", [])
    resumes = tmp_path / "results" / "rejeu_univers"
    (resumes / "LEGACY.json").write_text(
        json.dumps({"global": {"n": 12}}), encoding="utf-8")

    rapport = audit.auditer(tmp_path)

    assert rapport["counts"] == {
        "accepted": 1, "legacy": 1, "invalid": 1, "missing": 1,
    }
    zero = next(x for x in rapport["details"] if x["symbol"] == "ZERO")
    assert zero["status"] == "invalid"
    assert "zero_trades" in zero["reasons"]

