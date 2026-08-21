"""Contrat fail-closed de l'evaluation A/B SHADOW d'execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools import evalue_ab_execution_shadow as ab


def _canonique(objet: dict) -> bytes:
    return (json.dumps(
        objet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")


def _sha256(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()


def _ecrire_quote(racine: Path, symbole: str = "EURUSD") -> None:
    dossier = racine / symbole
    dossier.mkdir(parents=True)
    ligne = {
        "symbole": symbole,
        "ts_ms": 1_787_270_400_116.0,
        "bid": 1.16875,
        "ask": 1.16881,
        "spread": 0.00006,
        "last": 0.0,
        "volume": 0.0,
        "flags": 134,
        "horloge": "utc",
        "decalage_serveur_s": 10_800,
    }
    (dossier / "2026-08-21.ndjson").write_bytes(_canonique(ligne))


def _ecrire_artefact_scelle(racine: Path, resumes: Path) -> None:
    dossier = racine / "EURUSD"
    dossier.mkdir(parents=True)
    trade = {
        "schema_version": 1,
        "trade_id": "bt:v1:abc",
        "ordinal": 0,
        "symbol": "EURUSD",
        "split": "verification",
        "side": 1,
        # bar_entree n'est pas l'instant causal de decision/arrivee.
        "bar_entree": "2026-08-21T00:00:00+00:00",
        "bar_sortie": "2026-08-21T01:00:00+00:00",
        "r_unit": 0.001,
        "gross_r": 1.0,
        "net_r": 0.8,
        "cost_r": 0.2,
    }
    brut = _canonique(trade)
    resume = b'{"symbole":"EURUSD"}'
    resumes.mkdir(parents=True)
    (resumes / "EURUSD.json").write_bytes(resume)
    manifeste = {
        "schema_version": 1,
        "artifact_type": "v14.offline_replay.trades",
        "symbol": "EURUSD",
        "snapshot": {"snapshot_id": "a" * 64},
        "counts": {"trades": 1, "calibration": 0, "verification": 1},
        "trades": {
            "name": "trades.ndjson",
            "sha256": _sha256(brut),
            "bytes": len(brut),
        },
        "summary": {
            "name": "EURUSD.json",
            "sha256": _sha256(resume),
            "bytes": len(resume),
        },
    }
    manifeste["manifest_sha256"] = _sha256(_canonique(manifeste))
    (dossier / "trades.ndjson").write_bytes(brut)
    (dossier / "manifest.json").write_bytes(_canonique(manifeste))


def test_absence_de_brut_bloque_sans_simuler_de_fill(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    bruts.mkdir()
    _ecrire_quote(quotes)

    rapport = ab.auditer_disponibilite(bruts, quotes, resumes)

    assert rapport["status"] == "BLOCKED"
    assert rapport["simulation_performed"] is False
    assert rapport["inventory"]["raw_symbols"] == 0
    assert {r["code"] for r in rapport["blockers"]} == {"NO_RAW_ARTIFACTS"}
    assert all(valeur is None for valeur in rapport["metrics"].values())


def test_brut_scelle_mais_temps_et_fill_passif_inobservables(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes)
    _ecrire_artefact_scelle(bruts, resumes)

    premier = ab.auditer_disponibilite(bruts, quotes, resumes)
    second = ab.auditer_disponibilite(bruts, quotes, resumes)

    assert premier == second
    assert premier["status"] == "BLOCKED"
    codes = {r["code"] for r in premier["blockers"]}
    assert "INTENT_TIMESTAMP_UNOBSERVABLE" in codes
    assert "PASSIVE_FILL_UNOBSERVABLE" in codes
    assert premier["inventory"]["sealed_raw_symbols"] == 1
    assert premier["inventory"]["intentions"] == 1
    assert len(premier["snapshot"]["snapshot_id"]) == 64
    sceau = premier["manifest_sha256"]
    corps = dict(premier)
    corps.pop("manifest_sha256")
    assert sceau == _sha256(_canonique(corps))


def test_manifeste_altere_est_refuse_fail_closed(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes)
    _ecrire_artefact_scelle(bruts, resumes)
    manifeste = bruts / "EURUSD" / "manifest.json"
    charge = json.loads(manifeste.read_text(encoding="utf-8"))
    charge["counts"]["trades"] = 2
    manifeste.write_bytes(_canonique(charge))

    rapport = ab.auditer_disponibilite(bruts, quotes, resumes)

    assert rapport["status"] == "BLOCKED"
    assert "RAW_ARTIFACT_INVALID" in {r["code"] for r in rapport["blockers"]}
    assert rapport["inventory"]["sealed_raw_symbols"] == 0
    assert rapport["simulation_performed"] is False
