"""Le risque modulé par la confiance.

Ce module répartit du risque ; il ne doit jamais pouvoir en créer. Les tests
portent donc d'abord sur les bornes — un module de sizing qui déborde son
plafond n'est pas un module de sizing, c'est une panne.
"""

from __future__ import annotations

import pytest

from titanium.confiance import (
    POIDS_CONVICTION, RISQUE_MAX_PCT, RISQUE_MIN_PCT, RISQUE_PIVOT_PCT,
    Confiance, evaluer, piliers_de, total_piliers,
)
from titanium.sizing import MAX_RISK_PCT


class TestEchelle:
    def test_quorum_minimum_donne_le_plancher(self):
        assert evaluer(2, quorum=2, conviction=0.5).pct == \
            pytest.approx(RISQUE_MIN_PCT)

    def test_sans_faute_donne_le_plafond(self):
        assert evaluer(4, total_piliers=4, conviction=0.5).pct == \
            pytest.approx(RISQUE_MAX_PCT)

    def test_monotone(self):
        """Plus de piliers ne doit jamais donner moins de risque."""
        vals = [evaluer(p, quorum=2, conviction=0.5).pct for p in (2, 3, 4)]
        assert vals == sorted(vals)

    def test_quorum_prod_depasse_la_cible_historique(self):
        """3/4 doit valoir au moins l'ancien 1 % uniforme — sinon la
        modulation serait une réduction déguisée."""
        assert evaluer(3, quorum=2, conviction=0.5).pct >= RISQUE_PIVOT_PCT

    def test_sous_le_quorum_rend_le_plancher_pas_zero(self):
        """Zéro serait lu comme « pas de position » par un appelant qui n'a
        rien demandé de tel."""
        c = evaluer(1, quorum=2)
        assert c.pct == pytest.approx(RISQUE_MIN_PCT)
        assert "quorum" in c.motif


class TestBornes:
    """Le plafond est un mur. Aucune entrée ne doit le franchir."""

    @pytest.mark.parametrize("piliers", [0, 1, 2, 3, 4, 8, 99, -5])
    @pytest.mark.parametrize("conviction", [-3.0, 0.0, 0.5, 1.0, 7.0])
    def test_jamais_au_dessus_du_plafond_absolu(self, piliers, conviction):
        assert evaluer(piliers, conviction=conviction).pct <= MAX_RISK_PCT

    @pytest.mark.parametrize("conviction", [0.0, 0.5, 1.0])
    def test_jamais_au_dessus_du_plafond_de_modulation(self, conviction):
        assert evaluer(4, conviction=conviction).pct <= RISQUE_MAX_PCT

    @pytest.mark.parametrize("conviction", [0.0, 0.5, 1.0])
    def test_jamais_sous_le_plancher(self, conviction):
        assert evaluer(2, quorum=2, conviction=conviction).pct >= RISQUE_MIN_PCT

    def test_conviction_hors_bornes_ne_casse_pas(self):
        assert evaluer(3, conviction=float("inf")).pct <= MAX_RISK_PCT
        assert evaluer(3, conviction=-99.0).pct >= RISQUE_MIN_PCT

    def test_le_plafond_de_modulation_reste_sous_le_plafond_dur(self):
        """Le plafond absolu doit rester inatteignable par la modulation
        seule, pour continuer à jouer son rôle de dernier recours."""
        assert RISQUE_MAX_PCT < MAX_RISK_PCT


class TestConviction:
    def test_conviction_haute_augmente(self):
        bas = evaluer(3, conviction=0.0).pct
        haut = evaluer(3, conviction=1.0).pct
        assert haut > bas

    def test_conviction_neutre_est_neutre(self):
        """0.5 ne doit rien changer : c'est le point mort de l'échelle."""
        neutre = evaluer(3, conviction=0.5).pct
        sans = evaluer(3, conviction=0.5, poids_conviction=0.0).pct
        assert neutre == pytest.approx(sans)

    def test_influence_bornee(self):
        """Un LLM nuance la taille, il ne la décide pas."""
        base = evaluer(3, conviction=0.5, poids_conviction=0.0).pct
        haut = evaluer(3, conviction=1.0).pct
        assert haut <= base * (1.0 + POIDS_CONVICTION) + 1e-9

    def test_conviction_ne_franchit_pas_le_plancher(self):
        assert evaluer(2, quorum=2, conviction=0.0).pct >= RISQUE_MIN_PCT


class TestTracabilite:
    def test_multiplicateur_rapporte_a_la_cible(self):
        c = evaluer(3, conviction=0.5)
        assert c.multiplicateur == pytest.approx(c.pct / RISQUE_PIVOT_PCT)

    def test_serialisable(self):
        """Le multiplicateur doit finir au journal : c'est lui qui permettra
        de vérifier a posteriori si le pari sur les piliers tient."""
        d = evaluer(4, conviction=0.8).to_dict()
        assert set(d) == {"pct", "mult", "piliers", "conviction", "motif"}

    def test_motif_nomme_le_cas(self):
        assert evaluer(4).motif == "tous les piliers"
        assert "3/4" in evaluer(3).motif
        assert "quorum" in evaluer(2, quorum=2).motif


class TestPiliersDe:
    def test_compte_les_piliers_nommes(self):
        class G:
            def __init__(self, nom, p):
                self.name, self.passed = nom, p
        d = type("D", (), {"gates": [
            G("fair_value", True), G("liquidity", False),
            G("ote_ob", True), G("candle_confirmed", True)]})()
        assert piliers_de(d) == 3

    def test_portes_anonymes_ne_comptent_pas(self):
        """Une porte sans nom reconnu n'est pas un pilier. Mieux vaut
        sous-estimer la confiance que sur-dimensionner sur du vide."""
        class G:
            def __init__(self, p):
                self.passed = p
        d = type("D", (), {"gates": [G(True)] * 4})()
        assert piliers_de(d) == 0

    def test_decision_sans_portes(self):
        assert piliers_de(type("D", (), {})()) == 0
        assert piliers_de(type("D", (), {"gates": None})()) == 0


class TestIntegrationSizing:
    def test_le_pct_est_utilisable_par_budget_for(self):
        """Le contrat avec `budget_for` : un pourcentage, pas une fraction."""
        from titanium.sizing import SymbolSpec, budget_for

        spec = SymbolSpec(name="EURUSD", volume_min=0.01, volume_max=100.0,
                          volume_step=0.01, tick_value=1.0, tick_size=0.00001,
                          point=0.00001, digits=5, spread=10,
                          trade_contract_size=100_000.0)
        c = evaluer(4, conviction=0.8)
        b = budget_for(spec, 0.0020, 5000.0, target_pct=c.pct)
        assert b.tradable
        # ~1.75 % de 5000 = ~87.5 EUR, contre ~50 à 1 %.
        assert 60.0 < b.risk_money < 100.0


class TestComptagePiliersReel:
    """Contre le défaut du 07/08/2026 : compter TOUTES les portes.

    Une décision en porte six — `data_valid` et `trend_sr` s'ajoutent aux
    quatre piliers de micro-structure. Les compter toutes donnait 4 sur un
    setup à 2 piliers, donc le risque MAXIMUM au setup le plus faible admis.
    """

    def _decision(self, passes: dict):
        from titanium.gates.confluence_gate import GateResult
        return type("D", (), {"gates": [
            GateResult(n, p, "") for n, p in passes.items()]})()

    def test_ignore_data_valid_et_trend_sr(self):
        d = self._decision({
            "data_valid": True, "trend_sr": True,      # ← hors quorum
            "fair_value": True, "liquidity": False,
            "ote_ob": False, "candle_confirmed": True,
        })
        assert piliers_de(d) == 2, "seuls les piliers de confluence comptent"

    def test_setup_au_quorum_recoit_le_plancher(self):
        """Le cas exact observé : EURUSD, 2 piliers, recevait 1.75 %."""
        d = self._decision({
            "data_valid": True, "trend_sr": True,
            "fair_value": True, "liquidity": False,
            "ote_ob": False, "candle_confirmed": True,
        })
        c = evaluer(piliers_de(d), total_piliers=total_piliers(), quorum=2)
        assert c.pct == pytest.approx(RISQUE_MIN_PCT)

    def test_setup_complet_recoit_le_plafond(self):
        d = self._decision({
            "data_valid": True, "trend_sr": True,
            "fair_value": True, "liquidity": True,
            "ote_ob": True, "candle_confirmed": True,
        })
        c = evaluer(piliers_de(d), total_piliers=total_piliers(), quorum=2)
        assert c.pct == pytest.approx(RISQUE_MAX_PCT)

    def test_total_lu_a_la_source(self):
        from titanium.gates.confluence_gate import _SUPPORT_PILLARS
        assert total_piliers() == len(_SUPPORT_PILLARS) == 4
