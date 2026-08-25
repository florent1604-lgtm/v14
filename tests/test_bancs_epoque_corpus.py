"""Contrat d'epoque des bancs hors ligne, bout en bout.

Le 25/08/2026, un commit qui ajoutait un champ de journal live a
``titanium/edge.py`` — fichier moteur — a fait refuser 147/147 artefacts
scelles par les deux bancs, avec un code de sortie nul et un rapport vide.
Ces tests fixent le contrat qui l'interdit : la generation mesuree est celle
du CORPUS, l'ecart avec l'arbre de travail est permis mais publie, et zero
symbole mesure n'est jamais un resultat.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from titanium.backtest import Trade
from tools import epoque_rejeu, evalue_l1_passif as l1, rejeu_univers as ru

MOTEUR_SCELLE = [{"name": "titanium/edge.py", "bytes": 7, "sha256": "c" * 64}]
MOTEUR_AUTRE = [{"name": "titanium/edge.py", "bytes": 9, "sha256": "d" * 64}]
DEBUT = "2026-03-02T00:00:00+00:00"


def _snapshot(engine: list[dict], identifiant: str = "1") -> dict:
    return {
        "snapshot_id": identifiant * 64,
        "schema_version": 2,
        "asset_class": "metaux",
        "protocol": {"ltf": "M15"},
        "engine": engine,
    }


def _trade() -> Trade:
    return Trade(
        symbol="TEST", side=1,
        bar_entree="2026-03-02T00:00:00+00:00",
        bar_sortie="2026-03-02T02:00:00+00:00",
        prix_entree=100.0, prix_sortie=101.0, sl=99.0, tp=102.0,
        r_unit=1.0, pnl_r=0.5, cost_r=0.1, mae_r=-0.2, mfe_r=0.9,
        barres=8, motif="tp", contexte="metaux|continuation|3p",
        pillars=3, family="continuation", indicators={},
    )


def _corpus(racine: Path, symbole: str, engine: list[dict]) -> None:
    """Ecrit un artefact REELLEMENT scelle : sceau, compteurs, arithmetique."""
    bruts, resumes = racine / "bruts", racine / "resumes"
    resumes.mkdir(parents=True, exist_ok=True)
    brut, manifeste = ru.construire_artefact_brut(
        symbole, [_trade()], coupure="2026-04-01T00:00:00+00:00",
        snapshot=_snapshot(engine),
    )
    resume = json.dumps({"symbole": symbole}, ensure_ascii=False).encode("utf-8")
    manifeste = ru.lier_resume_au_manifeste(manifeste, symbole, resume)
    ru.persister_artefact_brut(bruts, symbole, brut, manifeste,
                               resume_path=resumes / f"{symbole}.json",
                               resume=resume)


def _quotes(racine: Path, symbole: str) -> None:
    """Un flux L1 continu qui couvre largement le plus long TTL."""
    dossier = racine / "quotes" / symbole
    dossier.mkdir(parents=True, exist_ok=True)
    depart = l1.instant_utc_ms("2026-03-02T00:15:00+00:00")
    lignes = []
    for pas in range(0, 1400):
        lignes.append(json.dumps({
            "symbole": symbole, "ts_ms": depart + pas * 1_000,
            "bid": 100.0, "ask": 100.2, "horloge": "utc",
        }, ensure_ascii=False))
    (dossier / "2026-03-02.ndjson").write_text(
        "\n".join(lignes) + "\n", encoding="utf-8")


def _specifications(racine: Path, symbole: str) -> Path:
    chemin = racine / "_specifications.json"
    chemin.write_text(json.dumps({
        symbole: {"point": 0.01, "tick_size": 0.01, "digits": 2},
    }), encoding="utf-8")
    return chemin


@pytest.fixture()
def terrain(tmp_path: Path):
    _corpus(tmp_path, "TEST", MOTEUR_SCELLE)
    _quotes(tmp_path, "TEST")
    specifications = _specifications(tmp_path, "TEST")

    def mesurer(**changes):
        parametres = {
            "quotes": tmp_path / "quotes",
            "bruts": tmp_path / "bruts",
            "resumes": tmp_path / "resumes",
            "specifications_path": specifications,
            "cutoff_ms": l1.instant_utc_ms("2026-03-03T00:00:00+00:00"),
            "seuil_gap_ms": 5_000,
        }
        parametres.update(changes)
        return l1.mesurer(**parametres)

    return tmp_path, mesurer


def test_un_corpus_scelle_reste_mesurable_quand_l_arbre_a_bouge(terrain):
    """LE test de regression : le corpus n'a pas bouge, le code oui."""
    _, mesurer = terrain
    rapport, details = mesurer()
    assert rapport["status"] == l1.STATUT_MESURE
    assert rapport["inventory"]["symbols_measured"] == 1
    assert details
    epoque = rapport["epoque"]
    assert epoque["corpus_epoch"] == epoque_rejeu.empreinte(MOTEUR_SCELLE)
    assert epoque["workspace_engine_epoch"] == epoque_rejeu.empreinte_courante()
    assert epoque["workspace_matches_corpus"] is False
    assert [m["symbol"] for m in epoque["manifests"]] == ["TEST"]


def test_deux_generations_dans_le_corpus_sont_un_echec_ferme(terrain):
    racine, mesurer = terrain
    _corpus(racine, "AUTRE", MOTEUR_AUTRE)
    _quotes(racine, "AUTRE")
    with pytest.raises(epoque_rejeu.EpoqueCorpusError) as erreur:
        mesurer()
    assert erreur.value.motif == "GENERATIONS_MIXTES"


def test_un_manifeste_absent_est_un_echec_ferme(terrain):
    racine, mesurer = terrain
    (racine / "bruts" / "TEST" / "manifest.json").unlink()
    with pytest.raises(epoque_rejeu.EpoqueCorpusError) as erreur:
        mesurer()
    assert erreur.value.motif == "MANIFESTE_ILLISIBLE"


def test_un_pin_juste_mesure_et_un_pin_faux_refuse(terrain):
    _, mesurer = terrain
    rapport, _ = mesurer(pin_epoque=epoque_rejeu.empreinte(MOTEUR_SCELLE))
    assert rapport["status"] == l1.STATUT_MESURE
    assert rapport["epoque"]["pin"] == epoque_rejeu.empreinte(MOTEUR_SCELLE)
    with pytest.raises(epoque_rejeu.EpoqueCorpusError) as erreur:
        mesurer(pin_epoque=epoque_rejeu.empreinte(MOTEUR_AUTRE))
    assert erreur.value.motif == "PIN_DIFFERENT_DU_CORPUS"


def test_zero_symbole_par_sceau_invalide_est_une_erreur_dure(terrain):
    """Un sceau casse ne doit pas rendre un rapport vide au code de sortie 0."""
    racine, mesurer = terrain
    chemin = racine / "bruts" / "TEST" / "trades.ndjson"
    chemin.write_bytes(chemin.read_bytes().replace(b'"net_r":0.5', b'"net_r":0.4'))
    rapport, details = mesurer()
    assert rapport["status"] == l1.STATUT_BLOQUE
    assert rapport["blocking_reason"] == "SCEAU_ARTEFACT_INVALIDE"
    assert rapport["inventory"]["symbols_measured"] == 0
    assert details == []


def test_zero_decision_dans_un_corpus_valide_n_est_pas_un_blocage(terrain):
    """Absence de donnee et panne d'analyse ne se confondent jamais."""
    racine, mesurer = terrain
    rapport, _ = mesurer(
        cutoff_ms=l1.instant_utc_ms("2026-03-02T00:10:00+00:00"))
    assert rapport["status"] == l1.STATUT_SANS_DECISION
    assert rapport["blocking_reason"] is None


def test_le_sceau_ne_depend_pas_de_l_arbre_de_travail(terrain, monkeypatch):
    """Deux mesures identiques du meme corpus rendent le meme snapshot_id."""
    _, mesurer = terrain
    premier, _ = mesurer()
    monkeypatch.setattr(epoque_rejeu, "empreinte_courante", lambda: "f" * 64)
    second, _ = mesurer()
    assert (premier["source_snapshot"]["snapshot_id"]
            == second["source_snapshot"]["snapshot_id"])
    assert second["epoque"]["workspace_engine_epoch"] == "f" * 64


def test_le_banc_bloque_n_ecrit_aucun_rapport(terrain, capsys, monkeypatch):
    """Un rapport vide qui ecrase le dernier rapport valide transforme une
    panne en « aucun signal »."""
    racine, _ = terrain
    sortie = racine / "rapport.json"
    sortie.write_text('{"status": "MEASURED_PRICE_PATH_ONLY"}', encoding="utf-8")
    _corpus(racine, "AUTRE", MOTEUR_AUTRE)
    monkeypatch.setattr("sys.argv", [
        "evalue_l1_passif.py",
        "--cutoff", "2026-03-03T00:00:00+00:00",
        "--quotes", str(racine / "quotes"),
        "--bruts", str(racine / "bruts"),
        "--resumes", str(racine / "resumes"),
        "--specifications", str(racine / "_specifications.json"),
        "--sortie", str(sortie),
        "--details", str(racine / "details.ndjson"),
    ])
    assert l1.main() == 2
    rendu = json.loads(capsys.readouterr().out)
    assert rendu["status"] == l1.STATUT_BLOQUE
    assert rendu["blocking_reason"] == "GENERATIONS_MIXTES"
    assert rendu["written"] is False
    assert json.loads(sortie.read_text(encoding="utf-8"))["status"] == (
        "MEASURED_PRICE_PATH_ONLY")
    # Durcissement Claude/Hermes : ANALYSIS_BLOCKED doit etre DANS UN RAPPORT
    # PUBLIE, pas seulement dans un code de retour qu'aucun tableau de bord ne
    # lit. Le rapport valide precedent, lui, n'est jamais ecrase.
    blocage = json.loads(
        epoque_rejeu.chemin_blocage(sortie).read_text(encoding="utf-8"))
    assert blocage["status"] == l1.STATUT_BLOQUE
    assert blocage["blocking_reason"] == "GENERATIONS_MIXTES"
    assert blocage["report_not_written"] == str(sortie)


def test_une_mesure_reussie_efface_le_blocage_precedent(terrain, monkeypatch):
    """Un blocage resolu qui resterait affiche ferait passer la panne suivante
    pour un simple reste."""
    racine, _ = terrain
    sortie = racine / "rapport.json"
    epoque_rejeu.publier_blocage(sortie, {"blocking_reason": "GENERATIONS_MIXTES"})
    assert epoque_rejeu.chemin_blocage(sortie).exists()
    monkeypatch.setattr("sys.argv", [
        "evalue_l1_passif.py",
        "--cutoff", "2026-03-03T00:00:00+00:00",
        "--quotes", str(racine / "quotes"),
        "--bruts", str(racine / "bruts"),
        "--resumes", str(racine / "resumes"),
        "--specifications", str(racine / "_specifications.json"),
        "--sortie", str(sortie),
        "--details", str(racine / "details.ndjson"),
    ])
    assert l1.main() == 0
    assert not epoque_rejeu.chemin_blocage(sortie).exists()
    assert json.loads(sortie.read_text(encoding="utf-8"))["status"] == (
        l1.STATUT_MESURE)
