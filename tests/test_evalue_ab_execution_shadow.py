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


def _hypotheses() -> dict:
    return {
        "schema_version": 1,
        "latency_ms": {
            "market": 50,
            "limit_passive": 50,
            "adaptive": 50,
        },
        "expiry_ms": {
            "market": 100,
            "limit_passive": 1_000,
            "adaptive": 1_000,
        },
        "fees_bps": {"maker": 0.5, "taker": 1.0},
        "fallback": {
            "market": "immediate_or_expire",
            "limit_passive": "expire_unfilled",
            "adaptive": "cross_at_expiry",
        },
        "markout_horizons_ms": [1_000],
        "max_quote_gap_ms": 1_100,
    }


def _ecrire_quote(
    racine: Path,
    symbole: str = "EURUSD",
    *,
    complet: bool = False,
    deuxieme_invalide: bool = False,
    couverture_complete: bool = True,
    sequence_invalide: bool = False,
    sparse: bool = False,
) -> None:
    dossier = racine / symbole
    dossier.mkdir(parents=True)
    base = {
        "symbole": symbole,
        "bid": 1.16875,
        "ask": 1.16881,
        "spread": 0.00006,
        "last": 0.0,
        "volume": 0.0,
        "flags": 134,
        "horloge": "utc",
        "decalage_serveur_s": 10_800,
    }
    if complet:
        base.update({
            "bid_size": 1.0,
            "ask_size": 1.0,
            "trade_price": 1.1688,
            "trade_size": 0.1,
            "aggressor_side": "buy",
            "sequence": 1,
        })
    timestamps = [
        1_787_270_399_900.0,
        1_787_270_400_100.0,
        1_787_270_400_500.0,
        1_787_270_401_050.0,
        1_787_270_402_100.0 if couverture_complete else 1_787_270_401_500.0,
    ]
    if sparse:
        timestamps = [timestamps[0], timestamps[-1]]
    lignes = []
    for index, ts_ms in enumerate(timestamps):
        ligne = {**base, "ts_ms": ts_ms}
        if complet:
            ligne["sequence"] = (
                len(timestamps) - index if sequence_invalide else index + 1
            )
        if deuxieme_invalide and index == 1:
            ligne["ask"] = -1
        lignes.append(_canonique(ligne))
    (dossier / "2026-08-21.ndjson").write_bytes(b"".join(lignes))


def _ecrire_artefact_scelle(
    racine: Path,
    resumes: Path,
    *,
    complet: bool = False,
    zero: bool = False,
    quantity_unit: str = "risk_unit",
) -> None:
    dossier = racine / "EURUSD"
    dossier.mkdir(parents=True)
    trade = {
        "schema_version": 2,
        "trade_id": "bt:v2:abc",
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
    if complet:
        trade.update({
            "decision_at": "2026-08-21T00:00:00+00:00",
            "quantity": 1.0,
            "quantity_unit": quantity_unit,
            "asset_class": "fx",
        })
    brut = b"" if zero else _canonique(trade)
    resume = b'{"symbole":"EURUSD"}'
    resumes.mkdir(parents=True)
    (resumes / "EURUSD.json").write_bytes(resume)
    manifeste = {
        "schema_version": 2,
        "artifact_type": "v14.offline_replay.trades",
        "symbol": "EURUSD",
        "snapshot": {"snapshot_id": "a" * 64},
        "counts": {
            "trades": 0 if zero else 1,
            "calibration": 0,
            "verification": 0 if zero else 1,
        },
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

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

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

    premier = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )
    second = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

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

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert rapport["status"] == "BLOCKED"
    assert "RAW_ARTIFACT_INVALID" in {r["code"] for r in rapport["blockers"]}
    assert rapport["inventory"]["sealed_raw_symbols"] == 0
    assert rapport["simulation_performed"] is False


def test_refuse_zero_intention_meme_si_artefact_scelle(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True)
    _ecrire_artefact_scelle(bruts, resumes, complet=True, zero=True)

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert rapport["status"] == "BLOCKED"
    assert "NO_EXECUTION_INTENTIONS" in {
        blocage["code"] for blocage in rapport["blockers"]
    }


def test_valide_toutes_les_quotes_pas_seulement_la_premiere(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True, deuxieme_invalide=True)
    _ecrire_artefact_scelle(bruts, resumes, complet=True)

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert rapport["status"] == "BLOCKED"
    assert "BROKER_QUOTES_INVALID" in {
        blocage["code"] for blocage in rapport["blockers"]
    }


def test_refuse_une_couverture_incomplete_du_markout(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True, couverture_complete=False)
    _ecrire_artefact_scelle(bruts, resumes, complet=True)

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert rapport["status"] == "BLOCKED"
    assert "QUOTE_COVERAGE_INCOMPLETE" in {
        blocage["code"] for blocage in rapport["blockers"]
    }


def test_hypotheses_absentes_bloquent_latence_frais_et_fallback(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True)
    _ecrire_artefact_scelle(bruts, resumes, complet=True)

    rapport = ab.auditer_disponibilite(bruts, quotes, resumes)

    assert rapport["status"] == "BLOCKED"
    assert "EXECUTION_ASSUMPTIONS_INVALID" in {
        blocage["code"] for blocage in rapport["blockers"]
    }
    assert rapport["snapshot"]["execution_assumptions"] is None


def test_sequence_passive_doit_etre_strictement_croissante(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True, sequence_invalide=True)
    _ecrire_artefact_scelle(bruts, resumes, complet=True)

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert rapport["status"] == "BLOCKED"
    assert "BROKER_QUOTES_INVALID" in {
        blocage["code"] for blocage in rapport["blockers"]
    }


def test_deux_quotes_aux_bornes_ne_prouvent_pas_une_couverture(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True, sparse=True)
    _ecrire_artefact_scelle(bruts, resumes, complet=True)

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert rapport["status"] == "BLOCKED"
    assert "QUOTE_COVERAGE_INCOMPLETE" in {
        blocage["code"] for blocage in rapport["blockers"]
    }


def test_fallback_inconnu_est_refuse(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True)
    _ecrire_artefact_scelle(bruts, resumes, complet=True)
    hypotheses = _hypotheses()
    hypotheses["fallback"]["adaptive"] = "banana"

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, hypotheses,
    )

    assert rapport["status"] == "BLOCKED"
    assert "EXECUTION_ASSUMPTIONS_INVALID" in {
        blocage["code"] for blocage in rapport["blockers"]
    }


def test_quantite_normalisee_ne_se_fait_pas_passer_pour_un_lot(tmp_path: Path):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True)
    _ecrire_artefact_scelle(
        bruts, resumes, complet=True, quantity_unit="broker_lot",
    )

    rapport = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert rapport["status"] == "BLOCKED"
    assert "INTENT_VALUES_INVALID" in {
        blocage["code"] for blocage in rapport["blockers"]
    }


def test_ready_exige_intentions_quotes_couverture_et_hypotheses_valides(
    tmp_path: Path,
):
    quotes = tmp_path / "quotes"
    bruts = tmp_path / "bruts"
    resumes = tmp_path / "resumes"
    _ecrire_quote(quotes, complet=True)
    _ecrire_artefact_scelle(bruts, resumes, complet=True)

    premier = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )
    second = ab.auditer_disponibilite(
        bruts, quotes, resumes, _hypotheses(),
    )

    assert premier == second
    assert premier["status"] == "READY_FOR_EVALUATOR"
    assert premier["blockers"] == []
    assert premier["inventory"]["intentions"] == 1
    assert premier["inventory"]["quote_observations_validated"] == 5
    assert premier["simulation_performed"] is False
