"""Le classement du rejeu ne doit jamais melanger deux generations de moteur.

``results/rejeu_univers`` est un dossier vivant : pendant un backfill il porte
en meme temps les resumes de l'ancien moteur et ceux du nouveau. Les lire tous
produit un classement d'actifs mesures par des codes differents.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def _module():
    chemin = RACINE / "tools" / "analyse_rejeu_univers.py"
    spec = importlib.util.spec_from_file_location("analyse_rejeu_univers", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["analyse_rejeu_univers"] = module
    spec.loader.exec_module(module)
    return module


def _resume(dossier: Path, symbole: str, esp: float) -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{symbole}.json").write_text(json.dumps({
        "symbole": symbole,
        "global": {"n": 100, "esperance_r": esp, "winrate": 0.5,
                   "profit_factor": 1.1, "ecart_type_r": 1.0},
        "calibration": {"n": 60, "esperance_r": esp},
        "verification": {"n": 40, "esperance_r": esp},
    }), encoding="utf-8")


def _manifeste(dossier: Path, symbole: str, engine: list) -> None:
    (dossier / symbole).mkdir(parents=True, exist_ok=True)
    (dossier / symbole / "manifest.json").write_text(
        json.dumps({"snapshot": {"engine": engine, "snapshot_id": "x"}}),
        encoding="utf-8")


@pytest.fixture()
def univers(tmp_path, monkeypatch):
    module = _module()
    rejeu, brut = tmp_path / "rejeu", tmp_path / "brut"
    rejeu.mkdir()
    brut.mkdir()
    monkeypatch.setattr(module, "REJEU", rejeu)
    monkeypatch.setattr(module, "REJEU_BRUT", brut)
    moteur_a = [{"name": "moteur.py", "bytes": 1, "sha256": "aa"}]
    moteur_b = [{"name": "moteur.py", "bytes": 2, "sha256": "bb"}]
    _resume(rejeu, "COURANT", 0.10)
    _resume(rejeu, "ANCIEN", 0.90)
    _resume(rejeu, "ANCIEN2", 0.70)
    _resume(rejeu, "SANS_MANIFESTE", 0.80)
    _manifeste(brut, "COURANT", moteur_a)
    _manifeste(brut, "ANCIEN", moteur_b)
    _manifeste(brut, "ANCIEN2", moteur_b)
    monkeypatch.setattr(module, "empreinte_moteur_courante",
                        lambda: module.epoque_rejeu.empreinte(moteur_a))
    module._moteurs = {"a": moteur_a, "b": moteur_b}
    return module


def test_le_defaut_reste_le_moteur_present_sur_disque(univers):
    """AMEND 3 de Codex : une generation choisie a la MAJORITE d'un dossier
    vivant ne peut pas etre un defaut. Pendant un backfill, le defaut
    basculerait a 50 pourcent et publierait un changement de cohorte comme un
    changement de performance."""
    resultats, tri = univers.charger_rejeu()
    assert [r["symbole"] for r in resultats] == ["COURANT"]
    assert tri["arbre_correspond_au_corpus"] is True
    assert tri["statut"] == "MESURE"
    assert "sans_manifeste" in tri["epoques_ecartees"]


def test_la_generation_dominante_est_un_mode_de_diagnostic(univers):
    resultats, tri = univers.charger_rejeu(epoque="dominante")
    assert sorted(r["symbole"] for r in resultats) == ["ANCIEN", "ANCIEN2"]
    assert tri["retenus"] == 2
    assert tri["ecartes"] == 2
    assert tri["arbre_correspond_au_corpus"] is False
    assert tri["statut"] == "ANALYSIS_PARTIAL"
    assert tri["epoque_retenue"] == univers.epoque_rejeu.empreinte(
        univers._moteurs["b"])[:16]


def test_une_empreinte_explicite_epingle_la_generation(univers):
    attendue = univers.epoque_rejeu.empreinte(univers._moteurs["a"])
    resultats, tri = univers.charger_rejeu(epoque=attendue)
    assert [r["symbole"] for r in resultats] == ["COURANT"]
    assert tri["epoque_retenue"] == attendue[:16]


def test_epoque_toutes_est_un_mode_de_diagnostic(univers):
    resultats, tri = univers.charger_rejeu(epoque="toutes")
    assert sorted(r["symbole"] for r in resultats) == [
        "ANCIEN", "ANCIEN2", "COURANT", "SANS_MANIFESTE"]
    assert tri["ecartes"] == 0


def test_epoque_inconnue_refusee(univers):
    with pytest.raises(ValueError):
        univers.charger_rejeu(epoque="n_importe_quoi")


def test_empreinte_partagee_avec_l_audit_des_artefacts():
    """Une seule definition de l'epoque dans tout le projet."""
    from tools import audit_rejeu_artefacts as audit, epoque_rejeu

    manifeste = {"snapshot": {"engine": [{"name": "m.py", "sha256": "aa"}]}}
    assert audit._fingerprint_moteur(manifeste) == epoque_rejeu.empreinte_manifeste(
        manifeste)


def test_empreinte_moteur_courante_est_reelle_et_stable():
    module = _module()
    empreinte = module.empreinte_moteur_courante()
    assert isinstance(empreinte, str) and len(empreinte) == 64
    assert empreinte == module.empreinte_moteur_courante()


def test_artefact_sans_manifeste_na_pas_d_epoque(tmp_path, monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "REJEU_BRUT", tmp_path)
    assert module.empreinte_moteur_artefact("INCONNU") == ""
    (tmp_path / "CASSE").mkdir()
    (tmp_path / "CASSE" / "manifest.json").write_text("{pas du json",
                                                      encoding="utf-8")
    assert module.empreinte_moteur_artefact("CASSE") == ""
