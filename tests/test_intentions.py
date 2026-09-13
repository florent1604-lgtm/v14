"""File d'intentions de clôture manuelle : dépôt, usage unique, péremption.

Ces tests fixent le contrat que la boucle armée pourra consommer sans risque :
une intention ne s'exécute jamais deux fois, et une intention vieille ne
s'exécute pas du tout.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from titanium.web import intentions as it

pytestmark = pytest.mark.unit


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "intentions.ndjson", tmp_path / "intentions_journal.ndjson"


def test_depot_puis_en_attente(files):
    file, journal = files
    ok, nonce = it.deposer(it.Intention("close_position", "123", "US500"), file)
    assert ok is True
    assert len(nonce) == 32

    attente = it.en_attente(file, journal)
    assert len(attente) == 1
    assert attente[0].ticket == "123"
    assert attente[0].action == "close_position"


def test_une_action_inconnue_est_refusee_au_depot(files):
    """Le refus arrive tant que l'opérateur est devant l'écran."""
    file, _ = files
    ok, motif = it.deposer(it.Intention("open_position", "123"), file)
    assert ok is False
    assert "action inconnue" in motif
    assert not file.exists(), "une intention refusée ne doit pas toucher le disque"


def test_un_ticket_vide_est_refuse(files):
    file, _ = files
    ok, motif = it.deposer(it.Intention("close_position", "   "), file)
    assert ok is False
    assert "ticket" in motif


def test_usage_unique_le_nonce_empeche_le_rejeu(files):
    """Le cœur de la sûreté : honorée une fois, plus jamais rejouée."""
    file, journal = files
    intention = it.Intention("close_position", "777", "US500")
    it.deposer(intention, file)

    attente = it.en_attente(file, journal)
    assert len(attente) == 1

    it.marquer_consommee(attente[0], "HONOREE", "ticket 777 ferme", journal)
    assert it.en_attente(file, journal) == [], (
        "une intention consommée réapparaît : risque de double clôture"
    )


def test_une_intention_refusee_ne_revient_pas_non_plus(files):
    """Même refusée, elle est consommée : sinon la boucle la retenterait en boucle."""
    file, journal = files
    it.deposer(it.Intention("close_position", "888"), file)
    attente = it.en_attente(file, journal)
    it.marquer_consommee(attente[0], "REFUSEE", "EXEC_DISARMED", journal)
    assert it.en_attente(file, journal) == []


def test_une_intention_perimee_est_ignoree(files):
    """Vieille de vingt minutes, elle ne veut plus rien dire."""
    file, journal = files
    vieux = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    intention = it.Intention("close_position", "999", demande_a=vieux)
    it.deposer(intention, file)

    assert intention.fraiche() is False
    assert it.en_attente(file, journal) == [], (
        "une intention périmée serait exécutée en retard, contre l'intention réelle"
    )


def test_une_ligne_corrompue_ne_casse_pas_la_lecture(files):
    """Le pont ne doit jamais casser le trading."""
    file, journal = files
    it.deposer(it.Intention("close_position", "111"), file)
    with file.open("a", encoding="utf-8") as f:
        f.write("{ceci n'est pas du json\n")
    it.deposer(it.Intention("close_position", "222"), file)

    tickets = {i.ticket for i in it.en_attente(file, journal)}
    assert tickets == {"111", "222"}


def test_fichiers_absents_rendent_une_liste_vide(files):
    file, journal = files
    assert it.en_attente(file, journal) == []
    assert it.nonces_consommes(journal) == set()


def test_ajout_seul_une_intention_deposee_reste_au_journal(files):
    """Aucune intention ne disparaît silencieusement du fichier de dépôt."""
    file, journal = files
    it.deposer(it.Intention("close_position", "1"), file)
    it.deposer(it.Intention("cancel_order", "2"), file)
    attente = it.en_attente(file, journal)
    for i in attente:
        it.marquer_consommee(i, "HONOREE", "", journal)

    lignes = [json.loads(x) for x in file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lignes) == 2, "le fichier de dépôt doit rester en ajout seul"


def test_etat_expose_le_reste_a_faire(files):
    file, journal = files
    it.deposer(it.Intention("close_position", "42", "US500"), file)
    etat = it.etat(file, journal)
    assert etat["deposees"] == 1
    assert etat["consommees"] == 0
    assert len(etat["en_attente"]) == 1
    assert etat["peremption_s"] == it.PEREMPTION_S


def test_le_module_ne_peut_pas_executer():
    """Garde-fou structurel : la file dépose, elle n'exécute pas."""
    interdits = {
        "order_send", "order_check", "MetaTrader5", "mt5_lock", "mt5_session",
        "ExecutionPolicy", "assert_can_trade", "execute_recorded",
        "TRADE_ACTION_DEAL", "TRADE_ACTION_SLTP", "TRADE_ACTION_REMOVE",
    }
    arbre = ast.parse(Path(it.__file__).read_text(encoding="utf-8"))
    utilises = (
        {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
        | {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
    )
    fautifs = sorted(utilises & interdits)
    assert not fautifs, f"la file d'intentions ne doit pas exécuter : {fautifs}"
