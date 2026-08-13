"""La sauvegarde des donnees de mesure doit etre verifiable et tournante."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from titanium.sauvegarde import (
    SauvegardeError,
    empreinte,
    instantanes,
    purger,
    sauvegarder,
    verifier,
)

FICHIERS = ("trades.ndjson", "positions.json")


def _source(tmp_path: Path, lignes: int = 3) -> Path:
    source = tmp_path / "results"
    source.mkdir()
    (source / "trades.ndjson").write_text(
        "".join(json.dumps({"ticket": f"live:{i}"}) + "\n" for i in range(lignes)),
        encoding="utf-8")
    (source / "positions.json").write_text('{"1": {"symbol": "EURUSD"}}',
                                           encoding="utf-8")
    return source


def test_instantane_copie_et_compte_les_lignes(tmp_path: Path) -> None:
    source = _source(tmp_path, lignes=5)
    rapport = sauvegarder(source, tmp_path / "sauvegardes", fichiers=FICHIERS)

    dossier = Path(rapport["dossier"])
    assert (dossier / "trades.ndjson").is_file()
    assert (dossier / "manifeste.json").is_file()

    par_nom = {entree["nom"]: entree for entree in rapport["fichiers"]}
    assert par_nom["trades.ndjson"]["lignes"] == 5
    # positions.json n'est pas du NDJSON : compter ses lignes n'aurait pas de sens.
    assert par_nom["positions.json"]["lignes"] is None
    assert par_nom["trades.ndjson"]["sha256"] == empreinte(source / "trades.ndjson")


def test_la_source_n_est_jamais_modifiee(tmp_path: Path) -> None:
    source = _source(tmp_path)
    avant = {chemin.name: empreinte(chemin) for chemin in source.iterdir()}

    sauvegarder(source, tmp_path / "sauvegardes", fichiers=FICHIERS)

    apres = {chemin.name: empreinte(chemin) for chemin in source.iterdir()}
    assert avant == apres


def test_fichier_absent_est_declare_pas_silencieux(tmp_path: Path) -> None:
    source = _source(tmp_path)
    rapport = sauvegarder(source, tmp_path / "sauvegardes",
                          fichiers=(*FICHIERS, "journal_rejets.ndjson"))
    # Distinguer "pas encore ecrit" de "efface" est tout l'interet du manifeste.
    assert rapport["absents"] == ["journal_rejets.ndjson"]
    assert [entree["nom"] for entree in rapport["fichiers"]] == list(FICHIERS)


def test_verifier_detecte_une_copie_alteree(tmp_path: Path) -> None:
    source = _source(tmp_path)
    rapport = sauvegarder(source, tmp_path / "sauvegardes", fichiers=FICHIERS)
    dossier = Path(rapport["dossier"])
    assert verifier(dossier)["ok"] is True

    (dossier / "trades.ndjson").write_text("ligne falsifiee\n", encoding="utf-8")

    controle = verifier(dossier)
    assert controle["ok"] is False
    assert controle["corrompus"] == ["trades.ndjson"]


def test_verifier_detecte_une_copie_disparue(tmp_path: Path) -> None:
    source = _source(tmp_path)
    dossier = Path(sauvegarder(source, tmp_path / "sauvegardes",
                               fichiers=FICHIERS)["dossier"])
    (dossier / "positions.json").unlink()

    controle = verifier(dossier)
    assert controle["ok"] is False
    assert controle["manquants"] == ["positions.json"]


def test_verifier_refuse_un_dossier_sans_manifeste(tmp_path: Path) -> None:
    (tmp_path / "vide").mkdir()
    with pytest.raises(SauvegardeError):
        verifier(tmp_path / "vide")


def test_rotation_garde_les_plus_recents(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "sauvegardes"
    base = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)

    for index in range(5):
        sauvegarder(source, destination, fichiers=FICHIERS, retention=3,
                    maintenant=base + timedelta(minutes=index))

    restants = [dossier.name for dossier in instantanes(destination)]
    assert len(restants) == 3
    assert restants == sorted(restants)
    assert restants[-1] == "20260813T060400Z"


def test_un_instantane_interrompu_n_est_pas_compte_ni_supprime(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "sauvegardes"
    base = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)
    sauvegarder(source, destination, fichiers=FICHIERS, retention=1, maintenant=base)

    # Un dossier sans manifeste = une copie interrompue. La rotation ne doit ni
    # le compter comme une sauvegarde valide, ni supprimer une bonne a sa place.
    interrompu = destination / "20260813T055900Z"
    interrompu.mkdir()
    (interrompu / "trades.ndjson").write_text("partiel", encoding="utf-8")

    supprimes = purger(destination, retention=1)

    assert supprimes == []
    assert interrompu.is_dir()
    assert len(instantanes(destination)) == 1


def test_retention_nulle_ne_supprime_rien(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "sauvegardes"
    base = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)
    for index in range(2):
        sauvegarder(source, destination, fichiers=FICHIERS, retention=0,
                    maintenant=base + timedelta(minutes=index))
    assert len(instantanes(destination)) == 2
