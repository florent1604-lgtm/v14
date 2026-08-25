"""Contrat de l'epoque d'analyse declaree.

Le 25/08/2026, un commit qui ajoutait un champ de journal live a
``titanium/edge.py`` — fichier moteur — a fait basculer l'empreinte du code
present sur disque. Les bancs hors ligne, qui exigeaient cette empreinte,
ont refuse les 147 artefacts scelles d'un coup, sans qu'aucun test ne le
signale. Le corpus n'avait pourtant pas bouge.

Une mesure ne se rattache pas a l'arbre de travail : elle se rattache a la
GENERATION qui a produit les artefacts lus. Ce module fige ce contrat.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import epoque_rejeu as ep


def _manifeste(racine: Path, symbole: str, moteur: list[dict], *,
               donnees: bytes | None = b'{"pnl_r":0.5}\n') -> Path:
    """Manifeste ET artefact : le sceau porte sur le COUPLE, jamais sur l'un.

    ``donnees=None`` fabrique un manifeste ORPHELIN, cas de refus n3 de la
    revue Claude (offset 640) : un manifeste seul certifierait un vide.
    """
    dossier = racine / symbole
    dossier.mkdir(parents=True, exist_ok=True)
    if donnees is not None:
        (dossier / "trades.ndjson").write_bytes(donnees)
    chemin = dossier / "manifest.json"
    chemin.write_text(json.dumps({
        "artifact_type": "v14.offline_replay.trades",
        "schema_version": 2,
        "symbol": symbole,
        "manifest_sha256": "0" * 64,
        "trades": {"name": "trades.ndjson",
                   "bytes": len(donnees) if donnees is not None else 0},
        "snapshot": {"engine": moteur},
    }, ensure_ascii=False), encoding="utf-8")
    return chemin


MOTEUR_A = [{"name": "titanium/edge.py", "bytes": 10, "sha256": "a" * 64}]
MOTEUR_B = [{"name": "titanium/edge.py", "bytes": 11, "sha256": "b" * 64}]


def test_corpus_homogene_rend_son_empreinte(tmp_path: Path):
    for symbole in ("AAA", "BBB"):
        _manifeste(tmp_path, symbole, MOTEUR_A)
    assert ep.epoque_corpus(tmp_path) == ep.empreinte(MOTEUR_A)


def test_generations_mixtes_sont_refusees(tmp_path: Path):
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    _manifeste(tmp_path, "BBB", MOTEUR_B)
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path)
    assert erreur.value.motif == "GENERATIONS_MIXTES"
    assert set(erreur.value.detail["epoques"]) == {
        ep.empreinte(MOTEUR_A)[:16], ep.empreinte(MOTEUR_B)[:16]}
    # Cas de refus n4 de Claude : le refus doit NOMMER le symbole fautif,
    # sinon il n'indique pas quoi rejouer.
    assert erreur.value.detail["par_epoque"][ep.empreinte(MOTEUR_A)[:16]] == ["AAA"]
    assert erreur.value.detail["par_epoque"][ep.empreinte(MOTEUR_B)[:16]] == ["BBB"]


def test_manifeste_absent_est_refuse(tmp_path: Path):
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    (tmp_path / "BBB").mkdir()
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path)
    assert erreur.value.motif == "MANIFESTE_ILLISIBLE"
    assert erreur.value.detail["symboles"] == ["BBB"]


def test_manifeste_sans_bloc_moteur_est_refuse(tmp_path: Path):
    _manifeste(tmp_path, "AAA", [])
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path)
    assert erreur.value.motif == "EPOQUE_ABSENTE"


def test_corpus_vide_est_refuse(tmp_path: Path):
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path)
    assert erreur.value.motif == "CORPUS_VIDE"


def test_un_manifeste_sans_artefact_est_refuse(tmp_path: Path):
    """Cas de refus n3 (Claude, offset 640) : le sceau porte sur le COUPLE."""
    _manifeste(tmp_path, "AAA", MOTEUR_A, donnees=None)
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path)
    assert erreur.value.motif == "ARTEFACT_ABSENT_OU_INCOHERENT"
    assert erreur.value.detail["symboles"] == ["AAA"]


def test_un_artefact_de_taille_incoherente_est_refuse(tmp_path: Path):
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    (tmp_path / "AAA" / "trades.ndjson").write_bytes(b"tronque")
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path)
    assert erreur.value.motif == "ARTEFACT_ABSENT_OU_INCOHERENT"


def test_le_corpus_est_celui_qui_est_demande(tmp_path: Path):
    """Un symbole hors demande ne peut ni sauver ni casser la mesure."""
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    _manifeste(tmp_path, "BBB", MOTEUR_B)
    assert ep.epoque_corpus(tmp_path, ["AAA"]) == ep.empreinte(MOTEUR_A)
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path, ["AAA", "ZZZ"])
    assert erreur.value.motif == "MANIFESTE_ILLISIBLE"


def test_un_pin_juste_passe_et_un_pin_faux_refuse(tmp_path: Path):
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    attendue = ep.empreinte(MOTEUR_A)
    assert ep.epoque_corpus(tmp_path, pin=attendue) == attendue
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path, pin=ep.empreinte(MOTEUR_B))
    assert erreur.value.motif == "PIN_DIFFERENT_DU_CORPUS"


def test_un_pin_ne_contourne_jamais_un_corpus_invalide(tmp_path: Path):
    """Le pin est une assertion, jamais une autorisation."""
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    _manifeste(tmp_path, "BBB", MOTEUR_B)
    with pytest.raises(ep.EpoqueCorpusError) as erreur:
        ep.epoque_corpus(tmp_path, pin=ep.empreinte(MOTEUR_A))
    assert erreur.value.motif == "GENERATIONS_MIXTES"


def test_etat_epoque_publie_l_ecart_sans_l_interdire(tmp_path: Path):
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    etat = ep.etat_epoque(tmp_path, ["AAA"])
    assert etat["corpus_epoch"] == ep.empreinte(MOTEUR_A)
    assert etat["workspace_engine_epoch"] == ep.empreinte_courante()
    assert etat["workspace_matches_corpus"] is False
    assert etat["manifests"] == [{
        "symbol": "AAA",
        "manifest_sha256": "0" * 64,
        "manifest_bytes_sha256": hashlib.sha256(
            (tmp_path / "AAA" / "manifest.json").read_bytes()).hexdigest(),
        "engine_epoch": ep.empreinte(MOTEUR_A),
    }]
    assert etat["pin"] is None
    # Cas de refus n2 de Claude : un corpus partiel est parfaitement homogene.
    # Sans la liste DEMANDEE au sceau, 135 symboles se lisent comme 147.
    assert etat["requested_symbols"] == ["AAA"]
    assert etat["retained_symbols"] == ["AAA"]
    assert etat["requested_count"] == etat["retained_count"] == 1


def test_etat_epoque_est_vrai_quand_l_arbre_correspond(tmp_path: Path):
    _manifeste(tmp_path, "AAA", ep.bloc_moteur_courant())
    etat = ep.etat_epoque(tmp_path, ["AAA"])
    assert etat["workspace_matches_corpus"] is True


def test_epoque_reference_prend_la_generation_dominante(tmp_path: Path):
    for symbole in ("AAA", "BBB", "CCC"):
        _manifeste(tmp_path, symbole, MOTEUR_A)
    _manifeste(tmp_path, "DDD", MOTEUR_B)
    reference, tri = ep.epoque_reference(tmp_path)
    assert reference == ep.empreinte(MOTEUR_A)
    assert tri[ep.empreinte(MOTEUR_A)] == 3
    assert tri[ep.empreinte(MOTEUR_B)] == 1


def test_epoque_reference_departage_de_facon_deterministe(tmp_path: Path):
    _manifeste(tmp_path, "AAA", MOTEUR_A)
    _manifeste(tmp_path, "BBB", MOTEUR_B)
    attendue = min(ep.empreinte(MOTEUR_A), ep.empreinte(MOTEUR_B))
    assert ep.epoque_reference(tmp_path)[0] == attendue
    assert ep.epoque_reference(tmp_path)[0] == attendue


def test_epoque_reference_refuse_un_corpus_sans_generation(tmp_path: Path):
    with pytest.raises(ep.EpoqueCorpusError):
        ep.epoque_reference(tmp_path)
