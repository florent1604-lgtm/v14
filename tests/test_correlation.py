"""Plafond d'exposition par grappe de corrélation.

Ce garde-fou existe à cause d'une journée précise : huit positions ouvertes,
budget global de 6 % parfaitement respecté, et six d'entre elles portant le
yen avec une corrélation moyenne de 0.69. Un pari pris six fois sous
l'apparence d'un portefeuille diversifié.

Les tests portent donc sur ce que le module doit REFUSER, et sur son
inertie : un garde-fou qui bloque en cas de panne serait pire que son
absence.
"""

from __future__ import annotations

import json

import pytest

from titanium.correlation import (
    MAX_RISQUE_GRAPPE_PCT, NB_GRAPPES, Grappes, charger, place_disponible,
    risque_par_grappe,
)


class _Pos:
    # distance 0.001 → 0.001/0.00001 × 1.0 = 100 unités par lot ;
    # sur 10 000 d'équité, cela fait exactement 1 % par position.
    def __init__(self, symbol, sl=1.099, ouvert=1.100, vol=1.0):
        self.symbol = symbol
        self.sl = sl
        self.price_open = ouvert
        self.volume = vol


class _Spec:
    trade_tick_size = 0.00001
    trade_tick_value = 1.0


def _mt5(positions):
    return type("M", (), {
        "positions_get": staticmethod(lambda *a, **k: positions),
        "symbol_info": staticmethod(lambda s: _Spec()),
    })()


GRAPPES = Grappes(
    par_actif={"EURJPY": "g4", "NZDJPY": "g4", "AUDJPY": "g4",
               "XAUUSD": "g10", "US500": "g6"},
    membres={"g4": ["EURJPY", "NZDJPY", "AUDJPY"],
             "g10": ["XAUUSD"], "g6": ["US500"]})


class TestGrappes:
    def test_actif_connu_rend_sa_grappe(self):
        assert GRAPPES.grappe_de("EURJPY") == "g4"

    def test_casse_ignoree(self):
        assert GRAPPES.grappe_de("eurjpy") == "g4"

    def test_inconnu_forme_sa_propre_grappe(self):
        """Le rattacher d'office inventerait une corrélation non mesurée ;
        l'isoler ne bride rien à tort."""
        assert GRAPPES.grappe_de("XYZABC").startswith("seul:")

    def test_deux_inconnus_ne_se_melangent_pas(self):
        assert GRAPPES.grappe_de("AAA") != GRAPPES.grappe_de("BBB")


class TestRisqueParGrappe:
    def test_somme_les_positions_d_une_meme_grappe(self):
        # 0.001 de distance / 0.00001 × 1.0 × 1 lot = 100 unités sur
        # 10 000 d'équité = 1 % par position.
        m = _mt5([_Pos("EURJPY"), _Pos("NZDJPY")])
        r = risque_par_grappe(m, GRAPPES, 10_000.0)
        assert r["g4"] == pytest.approx(2.0, abs=0.01)

    def test_grappes_distinctes_ne_s_additionnent_pas(self):
        m = _mt5([_Pos("EURJPY"), _Pos("XAUUSD")])
        r = risque_par_grappe(m, GRAPPES, 10_000.0)
        assert r["g4"] == pytest.approx(1.0, abs=0.01)
        assert r["g10"] == pytest.approx(1.0, abs=0.01)

    def test_position_sans_stop_compte_le_plafond(self):
        """La plus dangereuse ne doit pas passer pour gratuite."""
        p = _Pos("EURJPY")
        p.sl = 0.0
        assert risque_par_grappe(_mt5([p]), GRAPPES, 10_000.0)["g4"] == 2.0

    def test_equite_nulle_ne_divise_pas_par_zero(self):
        assert risque_par_grappe(_mt5([_Pos("EURJPY")]), GRAPPES, 0.0) == {}

    def test_mt5_casse_ne_leve_pas(self):
        m = type("M", (), {"positions_get": staticmethod(
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boum")))})()
        assert risque_par_grappe(m, GRAPPES, 10_000.0) == {}


class TestPlaceDisponible:
    def test_grappe_vide_accepte(self):
        ok, _ = place_disponible("EURJPY", 0.5, _mt5([]), GRAPPES, 10_000.0)
        assert ok

    def test_le_cas_du_07_08_2026(self):
        """Quatre paires JPY ouvertes : la cinquième doit être refusée.

        C'est exactement la situation qui a coûté −318 EUR, sous un budget
        global parfaitement respecté.
        """
        ouvertes = [_Pos("EURJPY", vol=0.6), _Pos("NZDJPY", vol=0.6),
                    _Pos("AUDJPY", vol=0.6)]
        ok, motif = place_disponible("EURJPY", 0.5, _mt5(ouvertes),
                                     GRAPPES, 10_000.0)
        assert not ok
        assert "g4" in motif

    def test_une_autre_grappe_reste_ouverte(self):
        """Le blocage doit être LOCAL : saturer le yen n'interdit pas l'or."""
        ouvertes = [_Pos("EURJPY", vol=0.6), _Pos("NZDJPY", vol=0.6),
                    _Pos("AUDJPY", vol=0.6)]
        ok, _ = place_disponible("XAUUSD", 0.5, _mt5(ouvertes),
                                 GRAPPES, 10_000.0)
        assert ok

    def test_le_motif_est_chiffre(self):
        """« trop corrélé » ne se vérifie pas ; « g4 déjà à 1.80 % » si."""
        ouvertes = [_Pos("EURJPY", vol=0.9), _Pos("NZDJPY", vol=0.9)]
        _, motif = place_disponible("AUDJPY", 0.5, _mt5(ouvertes),
                                    GRAPPES, 10_000.0)
        assert "%" in motif

    def test_le_motif_nomme_les_voisins(self):
        ouvertes = [_Pos("EURJPY", vol=0.9), _Pos("NZDJPY", vol=0.9)]
        _, motif = place_disponible("AUDJPY", 0.5, _mt5(ouvertes),
                                    GRAPPES, 10_000.0)
        assert "EURJPY" in motif or "NZDJPY" in motif

    def test_plafond_sous_le_budget_global(self):
        """Un plafond de grappe égal au budget global ne borderait rien."""
        from tools.live_demo import MAX_RISQUE_CUMULE_PCT
        assert MAX_RISQUE_GRAPPE_PCT < MAX_RISQUE_CUMULE_PCT


class TestCache:
    def test_cache_frais_evite_le_recalcul(self, tmp_path, monkeypatch):
        import time

        from titanium import correlation
        f = tmp_path / "g.json"
        f.write_text(json.dumps({
            "par_actif": {"X": "g1"}, "membres": {"g1": ["X"]},
            "calcule_le": time.time(), "methode": "test"}), encoding="utf-8")
        monkeypatch.setattr(correlation, "CACHE", f)
        monkeypatch.setattr(correlation, "calculer",
                            lambda *a, **k: pytest.fail("recalcul inutile"))
        assert charger(["X"]).grappe_de("X") == "g1"

    def test_cache_perime_recalcule(self, tmp_path, monkeypatch):
        from titanium import correlation
        f = tmp_path / "g.json"
        f.write_text(json.dumps({
            "par_actif": {"X": "vieux"}, "membres": {},
            "calcule_le": 0.0, "methode": "test"}), encoding="utf-8")
        monkeypatch.setattr(correlation, "CACHE", f)
        monkeypatch.setattr(correlation, "calculer",
                            lambda *a, **k: Grappes(par_actif={"X": "neuf"}))
        assert charger(["X"]).grappe_de("X") == "neuf"

    def test_cache_corrompu_ne_leve_pas(self, tmp_path, monkeypatch):
        from titanium import correlation
        f = tmp_path / "g.json"
        f.write_text("{ pas du json", encoding="utf-8")
        monkeypatch.setattr(correlation, "CACHE", f)
        monkeypatch.setattr(correlation, "calculer",
                            lambda *a, **k: Grappes(par_actif={"X": "neuf"}))
        assert charger(["X"]).grappe_de("X") == "neuf"


class TestGranularite:
    def test_nombre_de_grappes_exploitable(self):
        """k=3 sur 55 actifs mêlait BRENT, EURJPY et COCOA : bloquer un
        trade pétrole parce que le yen est chargé serait faux."""
        assert 8 <= NB_GRAPPES <= 20
