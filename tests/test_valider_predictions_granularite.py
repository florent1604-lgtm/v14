"""Une prediction non confrontee a la mesure n'est qu'une intention.

Ces tests fixent ce que la validation de la porte de granularite doit trancher
et, surtout, ce qu'elle ne doit PAS compter comme un ecart : la fenetre lue est
justement ce que la porte deplace.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def _module():
    chemin = RACINE / "tools" / "valider_predictions_granularite.py"
    spec = importlib.util.spec_from_file_location(
        "valider_predictions_granularite", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["valider_predictions_granularite"] = module
    spec.loader.exec_module(module)
    return module


MOTEUR_A = [{"name": "moteur.py", "bytes": 1, "sha256": "aa"}]
MOTEUR_B = [{"name": "moteur.py", "bytes": 2, "sha256": "bb"}]


def _resume(dossier: Path, symbole: str, *, n: int = 100,
            esperance: float = 0.10, debut: str = "2020-01-01") -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{symbole}.json").write_text(json.dumps({
        "symbole": symbole,
        "n_enter": n,
        "barres_evaluees": n * 10,
        "erreurs": 0,
        "coupure": "2024-01-01T00:00:00+00:00",
        "debut": debut,
        "fin": "2026-08-19",
        "barres_ltf": 100000,
        "global": {"n": n, "esperance_r": esperance, "ecart_type_r": 1.0,
                   "winrate": 0.45, "profit_factor": 1.0, "somme_r": n * esperance},
        "calibration": {"n": n // 2, "esperance_r": esperance},
        "verification": {"n": n // 2, "esperance_r": esperance},
    }), encoding="utf-8")


def _manifeste(dossier: Path, symbole: str, engine: list) -> None:
    (dossier / symbole).mkdir(parents=True, exist_ok=True)
    (dossier / symbole / "manifest.json").write_text(
        json.dumps({"snapshot": {"engine": engine, "snapshot_id": "x"}}),
        encoding="utf-8")


@pytest.fixture()
def univers(tmp_path):
    module = _module()
    reference, rejeu, brut = tmp_path / "ref", tmp_path / "rejeu", tmp_path / "brut"
    for dossier in (reference, rejeu, brut):
        dossier.mkdir()
    return module, reference, rejeu, brut


def _valider(module, reference, rejeu, brut, **kwargs):
    return module.valider(
        reference=reference, rejeu=rejeu, brut=brut,
        hors_univers=brut / "_HORS_UNIVERS.json",
        empreinte_courante=module.epoque_rejeu.empreinte(MOTEUR_A),
        **kwargs)


def test_symbole_retombe_identique(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "AUDUSD")
    _resume(rejeu, "AUDUSD")
    _manifeste(brut, "AUDUSD", MOTEUR_A)
    rapport = _valider(module, reference, rejeu, brut, attendus=())
    assert rapport["verdict"] == "CONFORME"
    assert rapport["comptes"] == {"identique": 1}


def test_une_fenetre_deplacee_n_est_pas_un_ecart_de_resultat(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "AUDUSD", debut="2015-01-01")
    _resume(rejeu, "AUDUSD", debut="2020-05-10")
    _manifeste(brut, "AUDUSD", MOTEUR_A)
    rapport = _valider(module, reference, rejeu, brut, attendus=())
    assert rapport["verdict"] == "CONFORME"
    detail = rapport["details"][0]
    assert detail["statut"] == "identique"
    assert [e["champ"] for e in detail["fenetre"]] == ["debut"]


def test_changement_hors_prediction_fait_echouer(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "AUDUSD", esperance=0.10)
    _resume(rejeu, "AUDUSD", esperance=-0.30)
    _manifeste(brut, "AUDUSD", MOTEUR_A)
    rapport = _valider(module, reference, rejeu, brut, attendus=("GER40",))
    assert rapport["verdict"] == "NON_CONFORME"
    assert rapport["inattendus"] == ["AUDUSD"]


def test_changement_prevu_reste_conforme_et_reste_lisible(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "GER40", n=100)
    _resume(rejeu, "GER40", n=90)
    _manifeste(brut, "GER40", MOTEUR_A)
    rapport = _valider(module, reference, rejeu, brut, attendus=("GER40",))
    assert rapport["verdict"] == "CONFORME"
    detail = rapport["details"][0]
    assert detail["statut"] == "change" and detail["attendu"] is True
    assert any(e["champ"] == "global.n" for e in detail["ecarts"])


def test_artefact_d_une_autre_epoque_est_en_attente_pas_identique(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "AUDUSD")
    _resume(rejeu, "AUDUSD")
    _manifeste(brut, "AUDUSD", MOTEUR_B)
    rapport = _valider(module, reference, rejeu, brut, attendus=())
    assert rapport["verdict"] == "PARTIEL"
    assert rapport["en_attente"] == ["AUDUSD"]


def test_symbole_sorti_de_l_univers_n_est_pas_un_ecart(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "USDCOP")
    (brut / "_HORS_UNIVERS.json").write_text(json.dumps({
        "USDCOP": {"raison": "209 barres H4 exploitables", "type": "ArchiveHorsUniversError"},
    }), encoding="utf-8")
    rapport = _valider(module, reference, rejeu, brut)
    assert rapport["verdict"] == "CONFORME"
    assert rapport["details"][0]["statut"] == "hors_univers"


def test_attendu_qui_ne_bouge_pas_est_signale_sans_faire_echouer(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "GER40")
    _resume(rejeu, "GER40")
    _manifeste(brut, "GER40", MOTEUR_A)
    rapport = _valider(module, reference, rejeu, brut, attendus=("GER40",))
    assert rapport["verdict"] == "CONFORME"
    assert rapport["attendus_inchanges"] == ["GER40"]
    assert rapport["avertissements"]


def test_ecart_infinitesimal_n_est_pas_un_ecart(univers):
    module, reference, rejeu, brut = univers
    _resume(reference, "AUDUSD", esperance=0.100000)
    _resume(rejeu, "AUDUSD", esperance=0.100000 + 1e-12)
    _manifeste(brut, "AUDUSD", MOTEUR_A)
    rapport = _valider(module, reference, rejeu, brut, attendus=())
    assert rapport["verdict"] == "CONFORME"
