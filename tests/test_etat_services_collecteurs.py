"""Le suivi des collecteurs de marché ne doit pas changer le verdict de V14.

Deux exigences opposées se rencontrent ici :

  1. une collecte arrêtée n'est PAS une panne — la mêler aux services ferait
     échouer `DEMARRER_V14.bat` chaque fois qu'une archive est volontairement
     à l'arrêt ;
  2. une collecte EN DOUBLE est grave — deux enregistreurs de carnet écrivent
     les mêmes différentiels dans le même fichier append-only, et une archive
     dédoublée ne se répare pas.
"""

from __future__ import annotations

from tools import etat_services as es


def _procs(monkeypatch, lignes):
    monkeypatch.setattr(es, "_processus", lambda: [
        {"ProcessId": pid, "ParentProcessId": parent, "CommandLine": ligne}
        for pid, parent, ligne in lignes])


def test_racines_ignore_les_collecteurs_par_defaut(monkeypatch):
    """Le tableau de bord et les .bat lisent ce retour et jugent la sante de
    V14 dessus : leur vue ne doit pas bouger."""
    _procs(monkeypatch, [
        (10, 1, r"python tools\live_demo.py"),
        (11, 1, r"python tools\enregistreur_carnet_binance.py"),
    ])
    assert set(es.racines()) == set(es.SERVICES)
    assert es.racines()["live_demo"] == [10]


def test_les_collecteurs_se_lisent_a_part(monkeypatch):
    _procs(monkeypatch, [
        (11, 1, r"python tools\enregistreur_quotes.py --symboles BTCUSD"),
        (12, 1, r"python tools\enregistreur_carnet_binance.py"),
    ])
    trouve = es.racines(es.COLLECTEURS)
    assert trouve == {"enregistreur_quotes": [11], "enregistreur_carnet_binance": [12]}


def test_un_relais_du_venv_ne_compte_pas_pour_une_instance(monkeypatch):
    """Le python.exe du venv lance un enfant : deux processus, une instance.
    Le compte naif avait deja fait voir un doublon la ou il n y en avait pas."""
    _procs(monkeypatch, [
        (11, 1, r"python tools\enregistreur_carnet_binance.py"),
        (12, 11, r"python tools\enregistreur_carnet_binance.py"),
    ])
    assert es.racines(es.COLLECTEURS)["enregistreur_carnet_binance"] == [11]


def test_un_collecteur_arrete_ne_fait_pas_echouer_le_diagnostic(monkeypatch, capsys):
    _procs(monkeypatch, [
        (10, 1, r"python tools\live_demo.py"),
        (20, 1, r"python tools\dashboard.py"),
        (30, 1, r"python tools\analystes.py"),
    ])
    code = es.main()
    sortie = capsys.readouterr().out
    assert code == 0, "une collecte a l arret n est pas une panne de V14"
    assert "enregistreur_quotes" in sortie and "arrete" in sortie


def test_un_collecteur_en_double_est_signale_sans_toucher_au_code_de_sortie(
        monkeypatch, capsys):
    """Le piege evite de justesse le 16/08/2026.

    `DEMARRER_V14.bat` s'arrete quand ce script rend 0 ("les services tournent
    deja"). Faire echouer le diagnostic pour un doublon de COLLECTEUR ferait
    croire au .bat que V14 est a l'arret : il lancerait une SECONDE boucle
    armee sur le meme compte, l'incident du 08/08/2026. Le code de sortie
    appartient aux trois services et a eux seuls.
    """
    _procs(monkeypatch, [
        (10, 1, r"python tools\live_demo.py"),
        (20, 1, r"python tools\dashboard.py"),
        (30, 1, r"python tools\analystes.py"),
        (41, 1, r"python tools\enregistreur_carnet_binance.py"),
        (42, 1, r"python tools\enregistreur_carnet_binance.py"),
    ])
    code = es.main()
    assert "DOUBLON" in capsys.readouterr().out
    assert code == 0, "un doublon de collecteur ne doit pas relancer les services"


def test_un_service_arrete_fait_toujours_echouer_le_diagnostic(monkeypatch):
    """Le contrat d origine ne bouge pas : c est lui qui autorise le demarrage."""
    _procs(monkeypatch, [(20, 1, r"python tools\dashboard.py")])
    assert es.main() == 1
