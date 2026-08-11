"""Le pont Titanium → analystes.

La propriété qui rend ce couplage sûr est **l'absence de couplage** : la
boucle de trading doit se comporter exactement pareil que le travailleur
soit vivant, mort, lent ou fou. Les tests portent donc surtout sur ce que
le pont ne peut pas faire.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from titanium.avis import (
    NEUTRE, PEREMPTION_S, Avis, Demande, conviction_pour, demandes_en_attente,
    dernier_avis, deposer, enregistrer,
)


def _iso(decalage_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(seconds=decalage_s)).isoformat()


DEM = dict(symbol="XAUUSD", side=1, verdict="ENTER", code="OK",
           piliers=3, famille="continuation", prix=2050.0,
           stop_distance=10.0, rr=3.0, bar_time="2026-08-07T10:00:00")


class TestDepot:
    def test_ecrit_une_ligne(self, tmp_path):
        f = tmp_path / "d.ndjson"
        assert deposer(Demande(**DEM), f)
        assert len(f.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_ajoute_sans_ecraser(self, tmp_path):
        f = tmp_path / "d.ndjson"
        for _ in range(3):
            deposer(Demande(**DEM), f)
        assert len(f.read_text(encoding="utf-8").strip().splitlines()) == 3

    def test_ne_leve_jamais(self, tmp_path):
        """Le pont ne doit pas pouvoir arrêter le trading."""
        assert deposer(Demande(**DEM), tmp_path / "a" / "b" / "c.ndjson")

    def test_horodate_automatiquement(self, tmp_path):
        f = tmp_path / "d.ndjson"
        deposer(Demande(**DEM), f)
        assert json.loads(f.read_text(encoding="utf-8"))["demande_a"]


class TestResume:
    def test_contient_les_faits(self):
        r = Demande(**DEM).resume()
        assert "XAUUSD" in r and "3/4" in r and "haussier" in r

    def test_ne_conclut_pas(self):
        """On demande un avis indépendant, pas une validation. Un brief qui
        vend le setup obtiendrait un accord poli et sans valeur."""
        r = Demande(**DEM).resume().lower()
        for vendeur in ("excellent", "fort", "recommand", "opportunité"):
            assert vendeur not in r

    def test_demande_explicitement_le_desaccord(self):
        assert "désaccord" in Demande(**DEM).resume()

    def test_sens_baissier(self):
        assert "baissier" in Demande(**{**DEM, "side": -1}).resume()


class TestLecture:
    def _ecrire(self, tmp_path, avis_list):
        f = tmp_path / "a.ndjson"
        f.write_text("\n".join(json.dumps(a) for a in avis_list) + "\n",
                     encoding="utf-8")
        return f

    def test_fichier_absent_rend_none(self, tmp_path):
        assert dernier_avis("XAUUSD", tmp_path / "rien.ndjson") is None

    def test_prend_le_dernier(self, tmp_path):
        f = self._ecrire(tmp_path, [
            {"symbol": "XAUUSD", "side": 1, "conviction": 0.3,
             "rendu_a": _iso(-60)},
            {"symbol": "XAUUSD", "side": 1, "conviction": 0.9,
             "rendu_a": _iso(-10)},
        ])
        assert dernier_avis("XAUUSD", f).conviction == pytest.approx(0.9)

    def test_avis_perime_ignore(self, tmp_path):
        """Une analyse d'il y a six heures ne décrit plus ce marché."""
        f = self._ecrire(tmp_path, [
            {"symbol": "XAUUSD", "side": 1, "conviction": 0.9,
             "rendu_a": _iso(-PEREMPTION_S - 60)},
        ])
        assert dernier_avis("XAUUSD", f) is None

    def test_autre_symbole_ignore(self, tmp_path):
        f = self._ecrire(tmp_path, [
            {"symbol": "EURUSD", "side": 1, "conviction": 0.9,
             "rendu_a": _iso()},
        ])
        assert dernier_avis("XAUUSD", f) is None

    def test_ligne_corrompue_ne_casse_pas(self, tmp_path):
        f = tmp_path / "a.ndjson"
        f.write_text('pas du json\n{"symbol":"XAUUSD","side":1,'
                     f'"conviction":0.8,"rendu_a":"{_iso()}"}}\n',
                     encoding="utf-8")
        assert dernier_avis("XAUUSD", f).conviction == pytest.approx(0.8)

    def test_horodatage_illisible_traite_comme_perime(self, tmp_path):
        f = self._ecrire(tmp_path, [
            {"symbol": "XAUUSD", "side": 1, "conviction": 0.9,
             "rendu_a": "jamais"},
        ])
        assert dernier_avis("XAUUSD", f) is None


class TestConvictionPour:
    def _f(self, tmp_path, **kw):
        d = {"symbol": "XAUUSD", "side": 1, "conviction": 0.8,
             "rating": "Buy", "rendu_a": _iso()}
        d.update(kw)
        f = tmp_path / "a.ndjson"
        f.write_text(json.dumps(d) + "\n", encoding="utf-8")
        return f

    def test_sans_avis_rend_neutre(self, tmp_path):
        c, motif = conviction_pour("XAUUSD", 1, tmp_path / "rien.ndjson")
        assert c == pytest.approx(NEUTRE)
        assert "aucun" in motif

    def test_accord_transmet_la_conviction(self, tmp_path):
        c, _ = conviction_pour("XAUUSD", 1, self._f(tmp_path))
        assert c == pytest.approx(0.8)

    def test_desaccord_abaisse_sans_annuler(self, tmp_path):
        """Un LLM rend une position plus petite. Il ne l'annule ni ne
        l'inverse — c'est la règle non négociable de V14."""
        f = self._f(tmp_path, side=-1, rating="Sell", conviction=0.9)
        c, motif = conviction_pour("XAUUSD", 1, f)
        assert 0.0 < c <= 0.2
        assert "désaccord" in motif

    def test_avis_perime_rend_neutre(self, tmp_path):
        f = self._f(tmp_path, rendu_a=_iso(-PEREMPTION_S - 1))
        assert conviction_pour("XAUUSD", 1, f)[0] == pytest.approx(NEUTRE)


class TestFileDAttente:
    def test_deduplique_par_barre(self, tmp_path):
        """Une même barre relue dix fois ne coûte qu'une délibération."""
        d = tmp_path / "d.ndjson"
        for _ in range(10):
            deposer(Demande(**DEM), d)
        assert len(demandes_en_attente(d, tmp_path / "a.ndjson")) == 1

    def test_ignore_les_deja_traitees(self, tmp_path):
        d, a = tmp_path / "d.ndjson", tmp_path / "a.ndjson"
        deposer(Demande(**DEM), d)
        enregistrer(Avis("XAUUSD", 1, bar_time=DEM["bar_time"]), a)
        assert demandes_en_attente(d, a) == []

    def test_barres_distinctes_comptent_separement(self, tmp_path):
        d = tmp_path / "d.ndjson"
        deposer(Demande(**DEM), d)
        deposer(Demande(**{**DEM, "bar_time": "2026-08-07T10:15:00"}), d)
        assert len(demandes_en_attente(d, tmp_path / "a.ndjson")) == 2

    def test_plafond(self, tmp_path):
        d = tmp_path / "d.ndjson"
        for i in range(40):
            deposer(Demande(**{**DEM, "bar_time": f"2026-08-07T{i:02d}:00:00"}), d)
        assert len(demandes_en_attente(d, tmp_path / "a.ndjson", maxi=5)) == 5


class TestAutoriteLimitee:
    """Le pont ne doit contenir aucun chemin d'exécution."""

    def _source(self):
        from pathlib import Path

        import titanium.avis as m
        return Path(m.__file__).read_text(encoding="utf-8")

    def test_aucune_execution(self):
        src = self._source()
        for interdit in ("order_send", "OrderSend", "PositionClose",
                         "executer", "envoyer_ordre", "positions_get"):
            assert interdit not in src, f"{interdit} dans le pont d'avis"

    def test_aucun_appel_reseau(self):
        """La lecture côté boucle doit être un accès fichier, rien d'autre :
        un appel réseau ici bloquerait le trading."""
        src = self._source()
        for interdit in ("requests", "urllib", "httpx", "openai", "invoke("):
            assert interdit not in src


class TestContexteAtteintLeGraphe:
    """La lecture de Titanium doit RÉELLEMENT parvenir aux analystes.

    Sans ce transport, le graphe analyserait le symbole en aveugle et
    « corréler avec leur propre analyse » n'aurait aucun sens : il n'y
    aurait rien à corréler.
    """

    def test_le_deliberateur_transmet_le_contexte(self):
        from titanium.deliberation import GraphDeliberator

        recu = {}

        class FauxGraphe:
            def propagate(self, sym, date, asset_type="stock", extra_context=""):
                recu["contexte"] = extra_context
                return None, "Buy"

        d = GraphDeliberator(trade_date="2026-08-07")
        d._graph = FauxGraphe()
        d.rating_for("XAUUSD", "3/4 piliers, famille continuation")
        assert "piliers" in recu["contexte"]

    def test_sans_contexte_la_signature_historique_suffit(self):
        """Un graphe qui ignore `extra_context` doit continuer à marcher."""
        from titanium.deliberation import GraphDeliberator

        class GrapheAncien:
            def propagate(self, sym, date, asset_type="stock"):
                return None, "Hold"

        d = GraphDeliberator(trade_date="2026-08-07")
        d._graph = GrapheAncien()
        assert d.rating_for("XAUUSD") == "Hold"

    def test_deux_lectures_ne_partagent_pas_le_cache(self):
        """Deux lectures différentes du même symbole sont deux questions
        différentes ; les confondre rendrait un avis périmé pour la seconde."""
        from titanium.deliberation import GraphDeliberator

        appels = []

        class FauxGraphe:
            def propagate(self, sym, date, asset_type="stock", extra_context=""):
                appels.append(extra_context)
                return None, "Buy"

        d = GraphDeliberator(trade_date="2026-08-07")
        d._graph = FauxGraphe()
        d.rating_for("XAUUSD", "2/4 piliers")
        d.rating_for("XAUUSD", "4/4 piliers")
        assert len(appels) == 2

    def test_contexte_identique_reutilise_le_cache(self):
        from titanium.deliberation import GraphDeliberator

        appels = []

        class FauxGraphe:
            def propagate(self, sym, date, asset_type="stock", extra_context=""):
                appels.append(extra_context)
                return None, "Buy"

        d = GraphDeliberator(trade_date="2026-08-07")
        d._graph = FauxGraphe()
        for _ in range(3):
            d.rating_for("XAUUSD", "3/4 piliers")
        assert len(appels) == 1

    def test_le_graphe_ajoute_sans_ecraser_l_identite(self, monkeypatch):
        """Le contexte d'instrument résolu reste souverain : un brief
        malformé ne doit jamais pouvoir l'effacer.

        Vérifié sur le COMPORTEMENT de `_run_graph`, pas sur le texte du
        source — un test qui cherche une chaîne casse au premier reformatage
        sans que rien ne soit réellement en panne.
        """
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        vu = {}

        class FauxPropagateur:
            def create_initial_state(self, nom, date, asset_type="stock",
                                     past_context="", instrument_context=""):
                vu["ctx"] = instrument_context
                return {}

            def get_graph_args(self):
                return {}

        g = object.__new__(TradingAgentsGraph)
        g.config = {}
        g.propagator = FauxPropagateur()
        g.debug = False
        g.memory_log = type("M", (), {
            "get_past_context": lambda s, n: ""})()
        g.resolve_instrument_context = lambda n, a: "IDENTITE-XAUUSD"
        g.graph = type("G", (), {"invoke": lambda s, *a, **k: {}})()
        try:
            g._run_graph("XAUUSD", "2026-08-07", extra_context="BRIEF-TITANIUM")
        except Exception:  # noqa: BLE001 — seul l'état initial nous intéresse
            pass

        assert "IDENTITE-XAUUSD" in vu.get("ctx", ""), "identité effacée"
        assert "BRIEF-TITANIUM" in vu.get("ctx", ""), "brief non transmis"


class TestPeremptionDesDemandes:
    """Un arriéré de demandes périmées ne se résorbe jamais.

    Constaté le 07/08/2026 : 106 demandes périmées sur 120, chacune coûtant
    deux à sept minutes de délibération pour un avis que `dernier_avis`
    jetterait aussitôt — pendant que les demandes fraîches s'empilaient.
    """

    def _vieille(self, minutes):
        d = Demande(**DEM)
        d.demande_a = _iso(-minutes * 60)
        return d

    def test_demande_perimee_ignoree(self, tmp_path):
        d, a = tmp_path / "d.ndjson", tmp_path / "a.ndjson"
        deposer(self._vieille(120), d)
        assert demandes_en_attente(d, a) == []

    def test_demande_fraiche_traitee(self, tmp_path):
        d, a = tmp_path / "d.ndjson", tmp_path / "a.ndjson"
        deposer(self._vieille(5), d)
        assert len(demandes_en_attente(d, a)) == 1

    def test_purge_retire_les_perimees(self, tmp_path):
        from titanium.avis import purger
        d = tmp_path / "d.ndjson"
        for m in (5, 120, 200, 10):
            deposer(self._vieille(m), d)
        assert purger(d) == 2
        assert len(d.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_purge_conserve_les_lignes_illisibles(self, tmp_path):
        """Une ligne qu'on ne sait pas dater n'est pas une ligne à jeter."""
        from titanium.avis import purger
        d = tmp_path / "d.ndjson"
        d.write_text('{"symbol":"X","demande_a":"jamais"}\nnimporte quoi\n',
                     encoding="utf-8")
        assert purger(d) == 0
        assert len(d.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_purge_fichier_absent(self, tmp_path):
        from titanium.avis import purger
        assert purger(tmp_path / "rien.ndjson") == 0


class TestSanteDansLeBrief:
    """L'analyste doit savoir sur quel état de système il juge.

    Un verdict rendu alors qu'un organe est hors service ne vaut pas un
    verdict rendu sur une chaîne saine. Le taire reviendrait à présenter
    toute lecture comme également fiable.
    """

    def test_sante_transmise(self):
        d = Demande(**DEM)
        d.sante = "ralenti — Analystes LLM en retard"
        assert "ralenti" in d.resume()

    def test_absente_ne_pollue_pas(self):
        assert "État du système" not in Demande(**DEM).resume()

    def test_survit_au_depot(self, tmp_path):
        f = tmp_path / "d.ndjson"
        d = Demande(**DEM)
        d.sante = "bouche — Journal des clôtures"
        deposer(d, f)
        rendu = demandes_en_attente(f, tmp_path / "a.ndjson")[0]
        assert "bouche" in rendu.sante
