"""La sauvegarde des donnees de mesure doit etre verifiable et tournante."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from titanium.sauvegarde import (
    SauvegardeError,
    empreinte,
    instantanes,
    purger,
    sauvegarder,
    verifier,
    verrou,
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
    assert restants[-1] == "20260813T060400000000Z"


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


def test_deux_sauvegardes_dans_la_meme_seconde_coexistent(tmp_path: Path) -> None:
    """Une tache planifiee et un appel manuel tombent volontiers ensemble.

    Avec un nom a la seconde, la seconde sauvegarde levait `FileExistsError`
    et ne sauvegardait rien : la collision arrivait precisement quand deux
    ecrivains s'inquietaient en meme temps des donnees.
    """
    source = _source(tmp_path)
    destination = tmp_path / "sauvegardes"

    meme_instant = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    premiers = sauvegarder(source, destination, fichiers=FICHIERS,
                           maintenant=meme_instant)
    seconds = sauvegarder(source, destination, fichiers=FICHIERS,
                          maintenant=meme_instant)

    assert premiers["dossier"] != seconds["dossier"]
    assert len(instantanes(destination)) == 2


def test_verrou_refuse_deux_sauvegardes_simultanees(tmp_path: Path) -> None:
    """Le verrou est inter-processus : `O_CREAT|O_EXCL` est atomique partout."""
    destination = tmp_path / "sauvegardes"
    source = _source(tmp_path)

    with verrou(destination, attente_s=0.2):
        assert (destination / ".verrou").is_file()
        with pytest.raises(SauvegardeError):
            sauvegarder(source, destination, fichiers=FICHIERS,
                        attente_verrou_s=0.2)

    # Verrou rendu : la sauvegarde repasse.
    assert sauvegarder(source, destination, fichiers=FICHIERS)["fichiers"]
    assert not (destination / ".verrou").exists()


def test_verrou_perime_est_repris(tmp_path: Path) -> None:
    """Un processus tue ne doit pas bloquer les sauvegardes pour toujours."""
    destination = tmp_path / "sauvegardes"
    destination.mkdir(parents=True)
    abandonne = destination / ".verrou"
    abandonne.write_text("999999", encoding="utf-8")
    vieux = time.time() - 3600
    os.utime(abandonne, (vieux, vieux))

    rapport = sauvegarder(_source(tmp_path), destination, fichiers=FICHIERS,
                          attente_verrou_s=0.2, verrou_perime_s=60.0)

    assert rapport["fichiers"]
    assert not abandonne.exists()


def test_manifeste_qualifie_la_coherence(tmp_path: Path) -> None:
    """Un instantane est crash-consistant : il doit le DIRE, pas le laisser croire."""
    source = _source(tmp_path)
    rapport = sauvegarder(source, tmp_path / "sauvegardes", fichiers=FICHIERS)

    assert rapport["garantie"] == "crash-consistant"
    assert rapport["coherent"] is True
    assert rapport["sources_modifiees_pendant"] == []


def test_source_qui_bouge_pendant_la_copie_est_nommee(tmp_path: Path) -> None:
    """La boucle continue d'ecrire : un instantane transversal doit se signaler."""
    source = _source(tmp_path)
    reel = shutil.copy2

    def copie_puis_ecrit(src, dst, *args, **kwargs):
        resultat = reel(src, dst, *args, **kwargs)
        if Path(src).name == "trades.ndjson":
            # Une cloture arrive APRES la copie du journal, AVANT celle des
            # positions : c'est exactement le cas que le manifeste doit avouer.
            with Path(src).open("a", encoding="utf-8") as flux:
                flux.write(json.dumps({"ticket": "live:99"}) + "\n")
        return resultat

    with mock.patch("titanium.sauvegarde.shutil.copy2", side_effect=copie_puis_ecrit):
        rapport = sauvegarder(source, tmp_path / "sauvegardes", fichiers=FICHIERS)

    assert rapport["coherent"] is False
    assert rapport["sources_modifiees_pendant"] == ["trades.ndjson"]


def test_manifeste_conserve_les_metadonnees_source_avant_et_apres(
        tmp_path: Path) -> None:
    """La qualification doit etre verifiable apres coup, pas seulement en RAM."""
    source = _source(tmp_path)
    rapport = sauvegarder(source, tmp_path / "sauvegardes", fichiers=FICHIERS)
    manifeste = json.loads(
        (Path(rapport["dossier"]) / "manifeste.json").read_text(encoding="utf-8"))

    assert manifeste["sources_avant"]["trades.ndjson"]["octets"] > 0
    assert manifeste["sources_apres"] == manifeste["sources_avant"]


def test_coherence_metier_signale_un_cycle_limite_incompatible(
        tmp_path: Path) -> None:
    """Un snapshot stable mais causalement impossible ne doit pas etre qualifie."""
    source = _source(tmp_path)
    (source / "positions.json").write_text(
        json.dumps({"42": {"limit_order_ticket": 7}}), encoding="utf-8")
    (source / "pending_limits.json").write_text(
        json.dumps({"7": {"symbol": "EURUSD"}}), encoding="utf-8")
    (source / "limit_lifecycle.ndjson").write_text(
        json.dumps({
            "event_id": "7:filled", "event": "filled", "order_ticket": 7,
            "position_ticket": 999,
        }) + "\n", encoding="utf-8")

    rapport = sauvegarder(
        source, tmp_path / "sauvegardes",
        fichiers=("trades.ndjson", "positions.json", "pending_limits.json",
                  "limit_lifecycle.ndjson"),
    )

    assert rapport["coherent"] is False
    assert any("position 42" in item for item in rapport["coherence_metier"]["incoherences"])


def test_copies_ecrites_dans_un_staging_avant_la_publication(
        tmp_path: Path) -> None:
    """Un crash ne laisse pas de faux instantane directement dans la destination."""
    source = _source(tmp_path)
    destination = tmp_path / "sauvegardes"
    reel = shutil.copy2
    parents_observes: list[Path] = []

    def copie_observant(src, dst, *args, **kwargs):
        parents_observes.append(Path(dst).parents[1])
        return reel(src, dst, *args, **kwargs)

    with mock.patch("titanium.sauvegarde.shutil.copy2", side_effect=copie_observant):
        sauvegarder(source, destination, fichiers=FICHIERS)

    assert parents_observes
    assert {parent.name for parent in parents_observes} == {".staging"}
