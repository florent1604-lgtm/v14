"""Un arbre de correlation deja calcule doit etre LU, meme le week-end.

Panne mesuree le 22-23/08/2026. La boucle armee redemarre un samedi a 08h46 :
seule la crypto est ouverte, 17 actifs portables. Le garde-fou qui interdit de
RECALCULER un arbre sous 20 actifs interdisait aussi d'en LIRE un deja calcule.
L'arbre n'a donc jamais ete charge, et la porte de risque correle a refuse
435 entrees d'affilee -- la totalite -- pendant 28 heures.
"""
from __future__ import annotations

import json
import time

import pytest

from titanium import correlation
from tools import live_demo as live


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    chemin = tmp_path / "grappes.json"
    monkeypatch.setattr(correlation, "CACHE", chemin)
    return chemin


def _ecrire(chemin, *, age_h: float) -> None:
    chemin.write_text(json.dumps({
        "par_actif": {"BTCUSD": "g8", "ETHUSD": "g8", "XLMUSD": "g9"},
        "membres": {"g8": ["BTCUSD", "ETHUSD"], "g9": ["XLMUSD"]},
        "calcule_le": time.time() - age_h * 3600,
        "methode": "riskfolio/ward",
    }), encoding="utf-8")


def test_charger_cache_rend_un_arbre_perime(cache):
    """Un arbre de 29 h reste une photographie ; l'absence d'arbre ferme tout."""
    _ecrire(cache, age_h=29)
    arbre = correlation.charger_cache()
    assert arbre is not None
    assert arbre.par_actif["BTCUSD"] == "g8"
    assert 28 * 3600 < correlation.age_grappes(arbre) < 30 * 3600


def test_charger_cache_respecte_un_ttl_explicite(cache):
    _ecrire(cache, age_h=29)
    assert correlation.charger_cache(ttl=3600) is None


def test_charger_cache_sans_fichier_ni_contenu(cache):
    assert correlation.charger_cache() is None
    cache.write_text(json.dumps({"par_actif": {}}), encoding="utf-8")
    assert correlation.charger_cache() is None
    cache.write_text("{pas du json", encoding="utf-8")
    assert correlation.charger_cache() is None


def test_sous_20_actifs_l_arbre_du_cache_est_adopte(cache, monkeypatch, capsys):
    """Le cas du week-end : 17 actifs ouverts, un arbre sur disque, on l'adopte."""
    _ecrire(cache, age_h=29)
    monkeypatch.setattr(live, "_GRAPPES", None)
    monkeypatch.setattr(live, "_GRAPPES_A", 0.0)

    def interdit(*_a, **_k):
        raise AssertionError("charger() recalcule : interdit sous 20 actifs")

    monkeypatch.setattr(correlation, "charger", interdit)

    live.rafraichir_grappes([f"CRYPTO{i}" for i in range(17)])

    assert live._GRAPPES is not None
    assert live._GRAPPES.par_actif["BTCUSD"] == "g8"
    sortie = capsys.readouterr().out
    assert "CACHE" in sortie and "17 ouverts" in sortie


def test_sous_20_actifs_sans_cache_la_porte_reste_fermee(cache, monkeypatch, capsys):
    monkeypatch.setattr(live, "_GRAPPES", None)
    monkeypatch.setattr(live, "_GRAPPES_A", 0.0)
    monkeypatch.setattr(correlation, "charger",
                        lambda *_a, **_k: pytest.fail("recalcul interdit"))

    live.rafraichir_grappes(["BTCUSD", "ETHUSD"])

    assert live._GRAPPES is None
    assert "aucun arbre sur disque" in capsys.readouterr().out


def test_au_dessus_de_20_actifs_le_recalcul_reprend(cache, monkeypatch):
    """Le seuil garde ce qu'il devait garder : le CALCUL, pas la lecture."""
    monkeypatch.setattr(live, "_GRAPPES", None)
    monkeypatch.setattr(live, "_GRAPPES_A", 0.0)
    appels = []

    def faux_charger(catalogue, **_k):
        appels.append(list(catalogue))
        return correlation.Grappes(par_actif={"BTCUSD": "g1"},
                                   membres={"g1": ["BTCUSD"]},
                                   calcule_le=time.time(), methode="test")

    monkeypatch.setattr(correlation, "charger", faux_charger)

    live.rafraichir_grappes([f"S{i}" for i in range(25)])

    assert len(appels) == 1
    assert live._GRAPPES.par_actif == {"BTCUSD": "g1"}


def test_un_arbre_deja_en_memoire_n_est_pas_remplace_par_le_cache(cache, monkeypatch):
    """Sous 20 actifs, on ne degrade jamais un arbre frais en arbre de cache."""
    _ecrire(cache, age_h=29)
    frais = correlation.Grappes(par_actif={"BTCUSD": "gFRAIS"},
                                membres={"gFRAIS": ["BTCUSD"]},
                                calcule_le=time.time(), methode="frais")
    monkeypatch.setattr(live, "_GRAPPES", frais)
    monkeypatch.setattr(live, "_GRAPPES_A", 0.0)  # perime, donc rafraichissable

    live.rafraichir_grappes(["BTCUSD", "ETHUSD"])

    assert live._GRAPPES.par_actif == {"BTCUSD": "gFRAIS"}


def test_les_refus_sont_ecrits_sur_disque(tmp_path, monkeypatch):
    """La boucle tourne dans une console sans fichier : les motifs se perdaient."""
    journal = tmp_path / "refus_live.ndjson"
    monkeypatch.setattr(live, "REFUS_LIVE", journal)
    stats: dict = {}

    live._refus(stats, "GRAPPE", "BTCUSD", "GRAPPES_INDISPONIBLES",
                risque_pct=0.5)
    live._refus(stats, "RISKGATE_DENY", "ETHUSD", "conviction insuffisante",
                piliers=2, side=1)

    lignes = [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines()]
    assert [x["code"] for x in lignes] == ["GRAPPE", "RISKGATE_DENY"]
    assert lignes[0]["symbole"] == "BTCUSD"
    assert lignes[0]["risque_pct"] == 0.5
    assert lignes[1]["piliers"] == 2
    assert stats["tunnel"]["post_enter_refusal"] == {
        "GRAPPE": 1, "RISKGATE_DENY": 1}


def test_journaliser_un_refus_ne_bloque_jamais(monkeypatch, tmp_path):
    """Un journal impossible a ecrire ne doit pas empecher le refus de compter."""
    monkeypatch.setattr(live, "REFUS_LIVE", tmp_path / "interdit" / "x.ndjson")
    monkeypatch.setattr(live.Path, "mkdir",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("nope")))
    stats: dict = {}
    live._refus(stats, "GRAPPE", "BTCUSD", "peu importe")
    assert stats["tunnel"]["post_enter_refusal"]["GRAPPE"] == 1
