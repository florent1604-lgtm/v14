"""La fin d'un backfill n'est pas l'arret des processus, c'est l'univers complet.

Ces tests fixent la seule definition acceptable de "termine" : chaque symbole
de l'archive porte un artefact scelle par le moteur PRESENT SUR DISQUE, ou est
consigne hors univers. Un artefact valide d'une generation precedente est du
travail restant, pas du travail fait.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

MOTEUR_A = [{"name": "moteur.py", "bytes": 1, "sha256": "aa"}]
MOTEUR_B = [{"name": "moteur.py", "bytes": 2, "sha256": "bb"}]


def _module():
    chemin = RACINE / "tools" / "cloture_backfill_rejeu.py"
    spec = importlib.util.spec_from_file_location("cloture_backfill_rejeu", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cloture_backfill_rejeu"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def univers(tmp_path):
    module = _module()
    resumes, brut = tmp_path / "rejeu", tmp_path / "brut"
    resumes.mkdir()
    brut.mkdir()
    return module, resumes, brut


def _artefact(resumes: Path, brut: Path, symbole: str, engine: list) -> None:
    (resumes / f"{symbole}.json").write_text(
        json.dumps({"symbole": symbole, "global": {"n": 100}}), encoding="utf-8")
    (brut / symbole).mkdir(parents=True, exist_ok=True)
    (brut / symbole / "manifest.json").write_text(
        json.dumps({"snapshot": {"engine": engine, "snapshot_id": "x"}}),
        encoding="utf-8")


def _etat(module, resumes, brut, symboles, **kwargs):
    return module.etat_backfill(
        symboles, resumes=resumes, brut=brut,
        empreinte_courante=module.epoque_rejeu.empreinte(MOTEUR_A), **kwargs)


def test_un_artefact_perime_reste_du_travail_a_faire(univers):
    module, resumes, brut = univers
    _artefact(resumes, brut, "AUDUSD", MOTEUR_A)
    _artefact(resumes, brut, "EURUSD", MOTEUR_B)
    etat = _etat(module, resumes, brut, ["AUDUSD", "EURUSD"])
    assert etat["termines"] == 1
    assert etat["restants"] == ["EURUSD"]


def test_un_symbole_hors_univers_ne_bloque_pas_la_fin(univers):
    module, resumes, brut = univers
    _artefact(resumes, brut, "AUDUSD", MOTEUR_A)
    etat = _etat(module, resumes, brut, ["AUDUSD", "USDCOP"],
                 hors_univers={"USDCOP": {"raison": "archive trop courte"}})
    assert etat["restants"] == []
    assert etat["hors_univers"] == ["USDCOP"]
    assert module.raison_arret(etat, ecoule_s=10, silence_s=100,
                               delai_max_s=1000) == "termine"


def test_un_resume_sans_manifeste_n_est_pas_termine(univers):
    module, resumes, brut = univers
    (resumes / "AUDUSD.json").write_text("{}", encoding="utf-8")
    etat = _etat(module, resumes, brut, ["AUDUSD"])
    assert etat["termines"] == 0 and etat["restants"] == ["AUDUSD"]


def test_tant_qu_il_reste_un_symbole_on_attend(univers):
    module, resumes, brut = univers
    _artefact(resumes, brut, "AUDUSD", MOTEUR_A)
    etat = _etat(module, resumes, brut, ["AUDUSD", "EURUSD"],
                 dernier_ecrit=1000.0)
    assert module.raison_arret(etat, ecoule_s=10, silence_s=100,
                               delai_max_s=10_000, maintenant=1050.0) == ""


def test_la_sentinelle_arrete_avant_tout(univers):
    module, resumes, brut = univers
    etat = _etat(module, resumes, brut, ["AUDUSD"], sentinelle=True)
    assert module.raison_arret(etat, ecoule_s=1, silence_s=10_000,
                               delai_max_s=10_000) == "sentinelle"


def test_le_silence_des_lots_arrete_la_veille(univers):
    module, resumes, brut = univers
    etat = _etat(module, resumes, brut, ["AUDUSD"], dernier_ecrit=1000.0)
    assert module.raison_arret(etat, ecoule_s=500, silence_s=3600,
                               delai_max_s=100_000,
                               maintenant=1000.0 + 3600) == "silence"


def test_le_delai_maximal_arrete_la_veille(univers):
    module, resumes, brut = univers
    etat = _etat(module, resumes, brut, ["AUDUSD"])
    assert module.raison_arret(etat, ecoule_s=90_000, silence_s=10_000,
                               delai_max_s=86_400) == "delai"


def test_le_markdown_dit_la_raison_et_l_etat(univers):
    module, resumes, brut = univers
    _artefact(resumes, brut, "AUDUSD", MOTEUR_A)
    etat = _etat(module, resumes, brut, ["AUDUSD"])
    texte = module._markdown({
        "cloture_le": "2026-08-24T02:00:00+00:00", "raison_arret": "termine",
        "etat": etat,
        "etapes": {"audit": {"commande": "audit", "code": 0,
                             "sortie": "artefacts acceptes 147/149",
                             "erreur": "", "secondes": 1.0}},
    })
    assert "termine" in texte and "artefacts acceptes 147/149" in texte
    assert "n'a aucune autorite d'execution" in texte
