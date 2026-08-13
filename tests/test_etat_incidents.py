"""Échec de lecture de l'état suivi rendu bruyant — tâche Prime 72911e8f.

Le scénario que ces tests interdisent de revivre : `positions.json` est abîmé
après un arrêt brutal, `load_state` rend `{}` sans rien dire, les positions
vivantes sont réadoptées sans `context_key`, leurs clôtures partent en
quarantaine `CONTEXTE_INCONNU` — et la donnée d'edge est perdue pour toujours.
L'historique de perception ne se reconstitue pas après coup.

Quatre exigences, dans l'ordre où elles comptent :

1. `load_state` ne lève JAMAIS — cinq appelants en dépendent, dont la boucle
   armée ; un fichier abîmé ne doit pas empêcher de gérer une position vivante ;
2. fichier **absent** et fichier **illisible** ne se confondent pas ;
3. une copie de sauvegarde existe AVANT que quoi que ce soit puisse réécrire ;
4. ce qui se lit est gardé — perdre quatre positions parce que la cinquième est
   malformée serait une perte auto-infligée.
"""

from __future__ import annotations

import json

import pytest

from titanium.execution import position_manager as pm


@pytest.fixture(autouse=True)
def _incidents_propres():
    """Le registre est un état de module : chaque test part d'un tableau vide."""
    pm._INCIDENTS_ETAT.clear()
    yield
    pm._INCIDENTS_ETAT.clear()


def _etat(**kw) -> dict:
    d = {"r": 0.001, "phase": "init", "symbol": "EURUSD", "side": 1,
         "entry": 1.1, "context_key": "EURUSD|long|continuation|3p"}
    d.update(kw)
    return d


def _ecrire(chemin, contenu):
    chemin.write_text(contenu if isinstance(contenu, str)
                      else json.dumps(contenu), encoding="utf-8")
    return chemin


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Le cas normal ne bruite pas
# ═══════════════════════════════════════════════════════════════════════════════

class TestSilenceQuandToutVaBien:
    def test_fichier_absent_aucun_incident(self, tmp_path):
        """Premier démarrage : il n'y a rien à signaler."""
        assert pm.load_state(tmp_path / "positions.json") == {}
        assert pm.incidents_etat() == []

    def test_fichier_valide_aucun_incident(self, tmp_path):
        p = _ecrire(tmp_path / "positions.json", {"111": _etat(), "222": _etat()})
        etat = pm.load_state(p)
        assert set(etat) == {"111", "222"}
        assert pm.incidents_etat() == []

    def test_fichier_vide_json_valide_aucun_incident(self, tmp_path):
        """`{}` est un état légitime : zéro position suivie."""
        p = _ecrire(tmp_path / "positions.json", {})
        assert pm.load_state(p) == {}
        assert pm.incidents_etat() == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Fichier illisible : bruyant, sauvegardé, mais jamais bloquant
# ═══════════════════════════════════════════════════════════════════════════════

class TestFichierIllisible:
    def test_json_tronque_consigne_un_incident(self, tmp_path):
        p = _ecrire(tmp_path / "positions.json", '{"111": {"r": 0.001')
        assert pm.load_state(p) == {}
        inc = pm.incidents_etat()
        assert len(inc) == 1
        assert inc[0]["genre"] == "illisible"
        assert str(p) == inc[0]["chemin"]

    def test_racine_qui_n_est_pas_un_dict(self, tmp_path):
        """Une liste parse sans erreur mais n'est pas un état."""
        p = _ecrire(tmp_path / "positions.json", [1, 2, 3])
        assert pm.load_state(p) == {}
        assert pm.incidents_etat()[0]["genre"] == "illisible"

    def test_une_copie_de_sauvegarde_est_faite(self, tmp_path):
        brut = '{"111": {"r": 0.001'
        p = _ecrire(tmp_path / "positions.json", brut)
        pm.load_state(p)
        sauv = pm.incidents_etat()[0]["sauvegarde"]
        assert sauv, "aucune sauvegarde : la preuve serait écrasée au tour suivant"
        from pathlib import Path
        assert Path(sauv).read_text(encoding="utf-8") == brut

    def test_deux_incidents_ne_s_ecrasent_pas(self, tmp_path):
        """Un second incident ne doit pas détruire la preuve du premier."""
        p = tmp_path / "positions.json"
        _ecrire(p, '{"a": bad')
        pm.load_state(p)
        _ecrire(p, '{"b": worse')
        pm.load_state(p)
        sauvs = [i["sauvegarde"] for i in pm.incidents_etat()]
        assert len(set(sauvs)) == 2, "les deux sauvegardes portent le même nom"

    def test_ne_leve_jamais(self, tmp_path):
        """Cinq appelants en dépendent, dont la boucle armée."""
        for contenu in ('{"111": {"r": 0.001', "[]", "null", "", "   ", "42"):
            p = _ecrire(tmp_path / "positions.json", contenu)
            assert isinstance(pm.load_state(p), dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Perte partielle : on garde ce qui se lit
# ═══════════════════════════════════════════════════════════════════════════════

class TestPertePartielle:
    def test_une_entree_malformee_n_emporte_plus_les_autres(self, tmp_path):
        """`from_dict` exige `d["r"]` : une KeyError emportait TOUT le fichier.

        Quatre positions vivantes perdues à cause d'une cinquième malformée,
        c'est une perte auto-infligée, pas une conséquence de la corruption.
        """
        p = _ecrire(tmp_path / "positions.json", {
            "111": _etat(), "222": _etat(),
            "333": {"phase": "init"},          # pas de "r" -> KeyError
            "444": _etat(),
        })
        etat = pm.load_state(p)
        assert set(etat) == {"111", "222", "444"}

    def test_la_perte_partielle_est_consignee(self, tmp_path):
        p = _ecrire(tmp_path / "positions.json", {
            "111": _etat(), "333": {"phase": "init"}})
        pm.load_state(p)
        inc = pm.incidents_etat()[0]
        assert inc["genre"] == "entrees_perdues"
        assert inc["entrees_lues"] == 1
        assert inc["entrees_perdues"] == 1
        assert "333" in inc["detail"]

    def test_la_perte_partielle_declenche_aussi_une_sauvegarde(self, tmp_path):
        p = _ecrire(tmp_path / "positions.json", {"333": {"phase": "init"}})
        pm.load_state(p)
        assert pm.incidents_etat()[0]["sauvegarde"]

    def test_valeur_non_dict_compte_comme_entree_perdue(self, tmp_path):
        p = _ecrire(tmp_path / "positions.json", {"111": _etat(), "222": "texte"})
        etat = pm.load_state(p)
        assert set(etat) == {"111"}
        assert pm.incidents_etat()[0]["entrees_perdues"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Le canal de signalement
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalement:
    def test_le_journal_disque_recoit_l_incident(self, tmp_path):
        p = _ecrire(tmp_path / "positions.json", '{"111": {"r": 0.001')
        pm.load_state(p)
        jrn = tmp_path / "etat_incidents.ndjson"
        assert jrn.exists()
        lignes = [json.loads(l) for l in jrn.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert lignes[-1]["genre"] == "illisible"

    def test_le_journal_est_append_only(self, tmp_path):
        p = tmp_path / "positions.json"
        for _ in range(3):
            _ecrire(p, "{cassé")
            pm.load_state(p)
        jrn = tmp_path / "etat_incidents.ndjson"
        assert len([l for l in jrn.read_text(encoding="utf-8").splitlines() if l.strip()]) == 3

    def test_incidents_etat_rend_une_copie(self, tmp_path):
        """Un appelant ne doit pas pouvoir vider le registre par inadvertance."""
        _ecrire(tmp_path / "positions.json", "{cassé")
        pm.load_state(tmp_path / "positions.json")
        copie = pm.incidents_etat()
        copie.clear()
        copie_bis = pm.incidents_etat()
        assert len(copie_bis) == 1

    def test_le_registre_est_borne(self, tmp_path):
        """Un fichier durablement illisible ne doit pas faire enfler le processus."""
        p = tmp_path / "positions.json"
        _ecrire(p, "{cassé")
        for _ in range(pm._MAX_INCIDENTS + 20):
            pm.load_state(p)
        assert len(pm.incidents_etat()) == pm._MAX_INCIDENTS

    def test_journal_indisponible_ne_casse_pas_la_lecture(self, tmp_path):
        """Journaliser est de l'observabilité : ça ne bloque jamais une lecture.

        On rend l'écriture du journal impossible en occupant son nom par un
        RÉPERTOIRE — plus fidèle qu'un `Path.open` mocké, qui casserait aussi
        la lecture du fichier d'état et testerait autre chose.
        """
        (tmp_path / "etat_incidents.ndjson").mkdir()
        p = _ecrire(tmp_path / "positions.json",
                    {"111": _etat(), "333": {"phase": "init"}})
        etat = pm.load_state(p)
        assert set(etat) == {"111"}, "la lecture doit survivre au journal muet"
        assert pm.incidents_etat()[0]["genre"] == "entrees_perdues"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Le battement publie l'incident
# ═══════════════════════════════════════════════════════════════════════════════

def test_le_battement_porte_les_incidents(tmp_path, monkeypatch):
    """Sans cette remontée, l'incident resterait dans un fichier que personne
    ne regarde. Le battement est le seul canal que le tableau de bord relit."""
    from tools import live_demo

    _ecrire(tmp_path / "positions.json", "{cassé")
    pm.load_state(tmp_path / "positions.json")

    battement = tmp_path / "loop_heartbeat.json"
    monkeypatch.setattr(live_demo, "BATTEMENT", battement)
    live_demo.battre({"tours": 1}, armer=True, equity=4000.0)

    d = json.loads(battement.read_text(encoding="utf-8"))
    assert d["etat_incidents_total"] == 1
    assert d["etat_incidents"][0]["genre"] == "illisible"


def test_le_battement_reste_muet_sans_incident(tmp_path, monkeypatch):
    from tools import live_demo

    battement = tmp_path / "loop_heartbeat.json"
    monkeypatch.setattr(live_demo, "BATTEMENT", battement)
    live_demo.battre({"tours": 1}, armer=True, equity=4000.0)

    d = json.loads(battement.read_text(encoding="utf-8"))
    assert d["etat_incidents"] == []
    assert d["etat_incidents_total"] == 0


def test_le_tableau_de_bord_relaie_l_incident(tmp_path, monkeypatch):
    """Dernier saut : le battement porte l'incident, encore faut-il que l'état
    du tableau de bord le recopie. Il ne relaie que des clés nommées — sans ce
    relais, « rendre bruyant » s'arrête à un fichier que personne ne lit."""
    from datetime import datetime, timezone

    from titanium.web import state as ws

    (tmp_path / "results").mkdir(exist_ok=True)
    battement = tmp_path / "results" / "loop_heartbeat.json"
    battement.write_text(json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "intervalle": 60, "armed": True, "equity": 4000.0, "stats": {},
        "etat_incidents": [{"genre": "illisible", "chemin": "x",
                            "detail": "d", "at": "now"}],
        "etat_incidents_total": 1,
    }), encoding="utf-8")
    monkeypatch.setattr(ws, "RACINE", tmp_path)

    out = ws.loop()
    assert out["etat_incidents_total"] == 1
    assert out["etat_incidents"][0]["genre"] == "illisible"
