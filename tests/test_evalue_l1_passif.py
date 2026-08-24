"""Contrat causal et fail-closed de l'evaluation L1 passive."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import evalue_l1_passif as l1


def _decision(ts_ms: int = 1_000_000, *, side: int = 1) -> dict:
    return {
        "symbol": "TEST",
        "decision_id": "d1",
        "decision_at": l1.iso_utc_ms(ts_ms),
        "decision_at_ms": ts_ms,
        "side": side,
        "r_unit": 10.0,
    }


def _spec() -> dict:
    return {"point": 0.01, "tick_size": 0.01, "digits": 2}


def _quotes(*points: tuple[int, float, float]) -> list[l1.Quote]:
    return [l1.Quote(ts, bid, ask) for ts, bid, ask in points]


def test_contact_inclusif_et_franchissement_sont_separes():
    debut = 1_000_000
    lignes = l1.evaluer_flux_symbole(
        "TEST", [_decision(debut)], _quotes(
            (debut, 100.0, 100.2),
            (debut + 1_000, 99.8, 100.0),  # contact exact de la limite BUY=100
            (debut + 2_000, 99.7, 99.9),  # franchissement strict
            (debut + 601_000, 100.0, 100.2),
        ), _spec(), cutoff_ms=debut + 700_000, seuil_gap_ms=700_000,
    )
    best120 = next(ligne for ligne in lignes
                   if ligne["politique"] == "best_passive"
                   and ligne["ttl_seconds"] == 120)
    assert best120["coverage_ok"] is True
    assert best120["contact_prix"] is True
    assert best120["franchissement_prix"] is True
    assert best120["premier_contact_delay_ms"] == 1_000
    assert best120["premier_franchissement_delay_ms"] == 2_000


def test_un_contact_exact_n_est_pas_un_franchissement():
    debut = 2_000_000
    lignes = l1.evaluer_flux_symbole(
        "TEST", [_decision(debut)], _quotes(
            (debut, 100.0, 100.2),
            (debut + 1_000, 99.8, 100.0),
            (debut + 601_000, 100.0, 100.2),
        ), _spec(), cutoff_ms=debut + 700_000, seuil_gap_ms=700_000,
    )
    ligne = next(element for element in lignes
                 if element["politique"] == "best_passive"
                 and element["ttl_seconds"] == 120)
    assert ligne["contact_prix"] is True
    assert ligne["franchissement_prix"] is False


def test_vente_se_lit_sur_le_bid():
    debut = 3_000_000
    lignes = l1.evaluer_flux_symbole(
        "TEST", [_decision(debut, side=-1)], _quotes(
            (debut, 100.0, 100.2),
            (debut + 1_000, 100.2, 100.4),
            (debut + 601_000, 100.0, 100.2),
        ), _spec(), cutoff_ms=debut + 700_000, seuil_gap_ms=700_000,
    )
    ligne = next(element for element in lignes
                 if element["politique"] == "best_passive"
                 and element["ttl_seconds"] == 120)
    assert ligne["prix_limite"] == pytest.approx(100.2)
    assert ligne["contact_prix"] is True
    assert ligne["franchissement_prix"] is False


def test_ttl_120_300_600_sont_fermes_independamment():
    debut = 4_000_000
    points = [(debut + i * 1_000, 100.0, 100.2) for i in range(602)]
    lignes = l1.evaluer_flux_symbole(
        "TEST", [_decision(debut)], _quotes(*points), _spec(),
        cutoff_ms=debut + 700_000, seuil_gap_ms=5_000,
    )
    best = [ligne for ligne in lignes if ligne["politique"] == "best_passive"]
    assert {
        ligne["ttl_seconds"] for ligne in best if ligne["coverage_ok"]
    } == {120, 300, 600}


def test_un_trou_superieur_au_seuil_invalide_la_fenetre():
    debut = 5_000_000
    points = [(debut, 100.0, 100.2)]
    points += [(debut + i * 1_000, 100.0, 100.2) for i in range(1, 10)]
    points += [(debut + 16_000, 100.0, 100.2), (debut + 601_000, 100.0, 100.2)]
    lignes = l1.evaluer_flux_symbole(
        "TEST", [_decision(debut)], _quotes(*points), _spec(),
        cutoff_ms=debut + 700_000, seuil_gap_ms=5_000,
    )
    assert not any(ligne["coverage_ok"] for ligne in lignes)
    assert {ligne["coverage_reason"] for ligne in lignes} == {
        "TROU_QUOTES_SUPERIEUR_AU_SEUIL"
    }


def test_cutoff_avant_expiration_ne_devient_jamais_absence_de_contact():
    debut = 6_000_000
    lignes = l1.evaluer_flux_symbole(
        "TEST", [_decision(debut)], _quotes(
            (debut, 100.0, 100.2),
            (debut + 1_000, 99.8, 100.0),
            (debut + 121_000, 100.0, 100.2),
        ), _spec(), cutoff_ms=debut + 200_000, seuil_gap_ms=200_000,
    )
    ttl600 = next(ligne for ligne in lignes
                  if ligne["politique"] == "best_passive"
                  and ligne["ttl_seconds"] == 600)
    assert ttl600["coverage_ok"] is False
    assert ttl600["contact_prix"] is None
    assert ttl600["coverage_reason"] == "CUTOFF_AVANT_FERMETURE"


def test_aucun_service_passif_n_est_invente():
    debut = 7_000_000
    lignes = l1.evaluer_flux_symbole(
        "TEST", [_decision(debut)], _quotes(
            (debut, 100.0, 100.2),
            (debut + 1_000, 99.0, 99.2),
            (debut + 601_000, 100.0, 100.2),
        ), _spec(), cutoff_ms=debut + 700_000, seuil_gap_ms=700_000,
    )
    assert all(ligne["service_observable"] is False for ligne in lignes)
    assert all(ligne["service"] is None for ligne in lignes)


def test_quote_invalide_est_refusee():
    with pytest.raises(ValueError, match="carnet inverse"):
        l1.quote_validee({
            "symbole": "TEST", "ts_ms": 1_000, "bid": 101, "ask": 100,
            "horloge": "utc",
        }, "TEST")


def test_agregation_exclut_les_fenetres_non_couvertes():
    base = {
        "politique": "best_passive", "ttl_seconds": 120,
        "premier_contact_delay_ms": None,
    }
    lignes = [
        {**base, "coverage_ok": True, "contact_prix": True,
         "franchissement_prix": False, "premier_contact_delay_ms": 10},
        {**base, "coverage_ok": True, "contact_prix": False,
         "franchissement_prix": False},
        {**base, "coverage_ok": False, "contact_prix": None,
         "franchissement_prix": None},
    ]
    cellule = l1.agreger(lignes)["best_passive|120"]
    assert cellule["decisions_candidates"] == 3
    assert cellule["coverage_ok"] == 2
    assert cellule["taux_contact_prix"] == pytest.approx(0.5)
    assert cellule["taux_service"] is None


def test_sceau_de_sortie_couvre_exactement_les_details(tmp_path: Path):
    rapport = {"schema_version": 1, "source_snapshot": {"snapshot_id": "a" * 64}}
    details = [{"a": 1}, {"a": 2}]
    sortie, chemin_details = tmp_path / "rapport.json", tmp_path / "details.ndjson"
    scelle = l1.sceller_sorties(
        rapport, details, sortie=sortie, details_path=chemin_details,
    )
    contenu = chemin_details.read_bytes()
    assert scelle["details"]["sha256"] == hashlib.sha256(contenu).hexdigest()
    corps = dict(scelle)
    sceau = corps.pop("manifest_sha256")
    assert sceau == hashlib.sha256(l1._canonique(corps)).hexdigest()
    assert json.loads(sortie.read_text(encoding="utf-8"))["manifest_sha256"] == sceau
