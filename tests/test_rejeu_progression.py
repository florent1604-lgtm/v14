"""Tests de la vue de progression du rejeu (lecture seule, aucun processus lance)."""
from __future__ import annotations

import json

import pytest

from tools import rejeu_progression as rp


def test_barre_bornes():
    assert rp.barre(0.0, 10) == "-" * 10
    assert rp.barre(1.0, 10) == "#" * 10
    assert rp.barre(0.5, 10).count("#") == 5


def test_symbole_en_cours_lit_la_derniere_annonce(tmp_path):
    log = tmp_path / "part0.log"
    log.write_text("lot 1/6 : 3 symboles\nEURUSD       deja fait\nGBPUSD       en cours...\n",
                   encoding="utf-8")
    assert rp._symbole_en_cours(log) == "GBPUSD"


def test_symbole_en_cours_vide_quand_le_symbole_est_termine(tmp_path):
    """Un lot entre deux symboles ne doit pas afficher le symbole precedent."""
    log = tmp_path / "part0.log"
    log.write_text("GBPUSD       en cours...\nGBPUSD       n= 5000 esp +0.0100 R  win 50.0%  "
                   "| verif n=1000 esp +0.0100 R  [3000.0s]\n", encoding="utf-8")
    assert rp._symbole_en_cours(log) == ""


@pytest.fixture()
def faux_depot(tmp_path, monkeypatch):
    archive = tmp_path / "results" / "barres" / "M15"
    archive.mkdir(parents=True)
    for symbole in ("AAA", "BBB", "CCC", "DDD"):
        (archive / f"{symbole}.parquet").write_bytes(b"")
    dest = tmp_path / "results" / "rejeu_univers"
    dest.mkdir(parents=True)
    monkeypatch.setattr(rp, "ARCHIVE", tmp_path / "results" / "barres")
    monkeypatch.setattr(rp, "DEST", dest)
    monkeypatch.setattr(rp, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(rp, "lots_actifs", lambda: [])
    return dest


def _sortie(esp_cal: float, esp_ver: float, secondes: float = 600.0) -> str:
    return json.dumps({
        "secondes": secondes,
        "calibration": {"esperance_r": esp_cal},
        "verification": {"esperance_r": esp_ver},
    })


def test_etat_compte_avancement_et_positifs(faux_depot):
    (faux_depot / "AAA.json").write_text(_sortie(0.1, 0.2), encoding="utf-8")
    (faux_depot / "BBB.json").write_text(_sortie(-0.1, 0.2), encoding="utf-8")
    e = rp.etat("M15")
    assert (e["univers"], e["faits"], e["restants"]) == (4, 2, 2)
    assert e["part"] == pytest.approx(0.5)
    assert e["symboles_restants"] == ["CCC", "DDD"]
    assert e["positifs_verification"] == 2
    assert e["positifs_calibration_et_verification"] == 1


def test_etat_ignore_les_sorties_illisibles(faux_depot):
    """Un JSON tronque par un lot tue ne doit pas faire tomber la vue."""
    (faux_depot / "AAA.json").write_text("{ tronque", encoding="utf-8")
    e = rp.etat("M15")
    assert e["faits"] == 1
    assert e["positifs_verification"] == 0


def test_etat_sans_lot_actif_n_invente_pas_de_fin(faux_depot):
    e = rp.etat("M15")
    assert e["faits"] == 0
    assert e["eta_s"] == 0
    assert e["fin_estimee"] == ""


def test_rendu_reste_lisible_sans_donnees(faux_depot):
    texte = rp.rendu(rp.etat("M15"))
    assert "0/4 symboles" in texte
