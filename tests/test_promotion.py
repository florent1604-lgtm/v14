"""Le protocole de promotion et son registre.

Ces deux modules décident quand le mode PROD peut s'ouvrir. Un défaut ici ne
se voit pas : il autorise du trading réel sur une mesure fausse. Les tests
portent donc en priorité sur ce qui doit **refuser**.
"""

from __future__ import annotations

import json

import pytest

from titanium.analysis.promotion import (
    BORNE_BASSE_PLANCHER, MIN_TRADES_CELLULE, PF_MIN, PromotionVerdict,
    block_bootstrap_lower, check_demotion, evaluate_cells,
    sample_size_required,
)
from titanium.analysis.promotion_registry import (
    ECHELON_DEMO, ECHELON_REEL, actives, is_promoted, load, promote, revoke,
)


class _T:
    """Trade clos minimal, avec les seuls champs que le protocole lit."""

    def __init__(self, pnl_r, *, classe="fx", support=3, source="live",
                 cost_r=0.01, exact_net=True, exact_cost=False):
        self.pnl_r = pnl_r
        self.asset_class = classe
        self.support_pillars = support
        self.source = source
        self.cost_r = cost_r
        self.exact_net = exact_net
        self.exact_cost = exact_cost


def _lot(n, valeur=0.4, **kw):
    return [_T(valeur, **kw) for _ in range(n)]


# ── Bootstrap par blocs ──────────────────────────────────────────────────

class TestBootstrap:
    def test_serie_constante_donne_sa_valeur(self):
        assert block_bootstrap_lower([0.5] * 60) == pytest.approx(0.5, abs=1e-9)

    def test_borne_basse_sous_la_moyenne(self):
        xs = [1.0, -1.0] * 40
        assert block_bootstrap_lower(xs) < sum(xs) / len(xs) + 1e-9

    def test_reproductible(self):
        xs = [0.3, -1.0, 1.2, 0.1] * 20
        assert block_bootstrap_lower(xs) == block_bootstrap_lower(xs)

    def test_echantillon_vide(self):
        assert block_bootstrap_lower([]) == float("-inf")

    def test_bloc_plus_grand_que_l_echantillon(self):
        """Ne doit pas lever : un petit échantillon reste analysable."""
        assert block_bootstrap_lower([0.5, 0.5], bloc=10, n_boot=100) \
            == pytest.approx(0.5, abs=1e-9)

    def test_ne_recolle_pas_la_fin_au_debut(self):
        """Un bloc ne doit jamais enjamber la fin de série.

        Reboucler avec `xs[i % n]` fabriquerait une continuité entre le
        dernier et le premier trade — deux instants sans rapport — et
        lisserait artificiellement la dépendance qu'on cherche à mesurer.
        """
        xs = [10.0] + [0.0] * 59
        # Si les blocs rebouclaient, le 10.0 initial serait sur-échantillonné
        # en fin de tirage et remonterait la borne basse au-dessus de 0.
        assert block_bootstrap_lower(xs, n_boot=2000) <= 0.34


class TestTailleRequise:
    def test_espérance_sous_le_plancher_est_inatteignable(self):
        assert sample_size_required(1.0, 0.01) == 999_999

    def test_plus_l_edge_est_faible_plus_il_en_faut(self):
        assert sample_size_required(1.0, 0.10) > sample_size_required(1.0, 0.50)

    def test_sigma_invalide(self):
        assert sample_size_required(0.0, 0.5) == 999_999


# ── Les huit critères ────────────────────────────────────────────────────

class TestCriteres:
    def test_cellule_trop_petite_refusee(self):
        v = evaluate_cells(_lot(20))[0]
        assert not v.eligible
        assert any("C1_TAILLE" in b for b in v.bloquants)

    def test_backtest_exclu(self):
        """Le backtest n'a ni slippage ni exécution réelle : le verser
        reviendrait à promouvoir sur des conditions qui n'existent pas."""
        assert evaluate_cells(_lot(80, source="backtest")) == []

    def test_strate_faible_exclue(self):
        """PROD est un sous-ensemble d'EXPLORE : on mesure la strate S≥3,
        pas l'agrégat, sinon la strate S=2 domine la moyenne."""
        assert evaluate_cells(_lot(80, support=2)) == []

    def test_classe_inconnue_exclue(self):
        """Un symbole non classé ne doit pas ouvrir le mode réel."""
        assert evaluate_cells(_lot(80, classe="")) == []

    def test_pnl_net_non_comptable_refuse(self):
        lot = _lot(70, exact_net=False)
        v = evaluate_cells(lot)[0]
        assert any("C3_PNL_NET_COMPTABLE" in b for b in v.bloquants)

    def test_spread_estime_ne_bloque_pas_un_pnl_net_comptable(self):
        """La ventilation du spread reste diagnostique : le net MT5 inclut
        deja spread, commission, swap et fee dans le resultat promu."""
        lot = _lot(120, exact_net=True, exact_cost=False)
        v = evaluate_cells(lot)[0]
        assert not any("C3_" in b for b in v.bloquants)

    def test_esperance_negative_refusee(self):
        v = evaluate_cells(_lot(80, valeur=-0.2))[0]
        assert not v.eligible

    def test_pf_insuffisant_refuse(self):
        # Alternance qui donne un PF juste au-dessus de 1.
        lot = [_T(1.0) if i % 2 else _T(-0.95) for i in range(80)]
        v = evaluate_cells(lot)[0]
        assert v.profit_factor < PF_MIN
        assert any("C5_PF" in b for b in v.bloquants)

    def test_instabilite_temporelle_refusee(self):
        """Deux segments perdants sur trois : le gain vient d'une période."""
        lot = _lot(30, valeur=-0.3) + _lot(30, valeur=-0.3) + _lot(30, valeur=2.0)
        v = evaluate_cells(lot)[0]
        assert any("C6_STABILITE" in b for b in v.bloquants)

    def test_cout_qui_mange_l_esperance_refuse(self):
        lot = _lot(80, valeur=0.10, cost_r=0.08)
        v = evaluate_cells(lot)[0]
        assert any("C8_COUT_RELATIF" in b for b in v.bloquants)

    def test_cellule_saine_est_eligible(self):
        import random
        rng = random.Random(7)
        lot = [_T(rng.gauss(0.55, 0.55)) for _ in range(120)]
        v = evaluate_cells(lot)[0]
        assert v.eligible, f"bloquants inattendus : {v.bloquants}"
        assert v.n == 120


class TestCorrectionMultiplicite:
    def test_le_masque_bh_n_est_pas_une_q_valeur(self):
        """`_benjamini_hochberg` rend un MASQUE de rejets.

        Le traiter comme un nombre et le comparer à 0.10 inverserait le
        critère : `True > 0.10` étant vrai, toute cellule éligible se
        verrait bloquée. Ce test verrouille le sens correct.
        """
        import random
        rng = random.Random(11)
        lot = [_T(rng.gauss(0.6, 0.5)) for _ in range(120)]
        v = evaluate_cells(lot)[0]
        assert v.survit_fdr is True
        assert v.eligible

    def test_cellule_sans_signal_ne_survit_pas(self):
        import random
        rng = random.Random(3)
        lot = [_T(rng.gauss(0.0, 1.0)) for _ in range(120)]
        v = evaluate_cells(lot)[0]
        assert not v.eligible


def test_rejets_du_journal_bloquent_la_promotion():
    verdict = evaluate_cells(_lot(120, valeur=0.8), rejected_lines=2)[0]
    assert verdict.eligible is False
    assert "C0_JOURNAL_REJETS(2)" in verdict.bloquants


class TestDemotion:
    def test_retrograde_si_les_derniers_ne_paient_plus(self):
        lot = _lot(40, valeur=0.8) + _lot(30, valeur=-0.5)
        assert check_demotion(lot, {"fx|S>=3": {}}) == ["fx|S>=3"]

    def test_ne_retrograde_pas_une_cellule_saine(self):
        assert check_demotion(_lot(60, valeur=0.6), {"fx|S>=3": {}}) == []

    def test_echantillon_recent_trop_court(self):
        """Rétrograder sur trois trades serait du bruit."""
        assert check_demotion(_lot(5, valeur=-1.0), {"fx|S>=3": {}}) == []


# ── Registre ─────────────────────────────────────────────────────────────

def _eligible():
    return PromotionVerdict(cell="fx|S>=3", n=80, expectancy_r=0.4,
                            lower_95_r=0.2, profit_factor=1.8, eligible=True)


class TestRegistre:
    def test_absent_rend_false(self, tmp_path):
        assert is_promoted(tmp_path / "rien.json", "fx|S>=3") is False

    def test_corrompu_rend_false(self, tmp_path):
        f = tmp_path / "p.json"
        f.write_text("{ ceci n'est pas du json", encoding="utf-8")
        assert is_promoted(f, "fx|S>=3") is False
        assert load(f) == {}

    def test_promotion_puis_autorisation(self, tmp_path):
        f = tmp_path / "p.json"
        promote(f, "fx|S>=3", _eligible(), approved_by="florent")
        assert is_promoted(f, "fx|S>=3") is True

    def test_verdict_non_eligible_refuse(self, tmp_path):
        """L'humain peut refuser l'éligible ; il ne peut pas accepter
        l'inéligible. La clé humaine est à sens unique."""
        v = PromotionVerdict(cell="fx|S>=3", eligible=False,
                             bloquants=["C1_TAILLE(20/60)"])
        with pytest.raises(ValueError, match="pas éligible"):
            promote(tmp_path / "p.json", "fx|S>=3", v, approved_by="florent")

    def test_approbation_obligatoire(self, tmp_path):
        with pytest.raises(ValueError, match="approved_by"):
            promote(tmp_path / "p.json", "fx|S>=3", _eligible(), approved_by="")

    def test_echelon_reel_interdit_ici(self, tmp_path):
        with pytest.raises(ValueError, match="échelon réel"):
            promote(tmp_path / "p.json", "fx|S>=3", _eligible(),
                    approved_by="florent", rung=ECHELON_REEL)

    def test_echelon_insuffisant_rend_false(self, tmp_path):
        f = tmp_path / "p.json"
        promote(f, "fx|S>=3", _eligible(), approved_by="florent", rung=1)
        assert is_promoted(f, "fx|S>=3") is False

    def test_revocation_ferme_la_porte(self, tmp_path):
        f = tmp_path / "p.json"
        promote(f, "fx|S>=3", _eligible(), approved_by="florent")
        assert revoke(f, "fx|S>=3", reason="espérance retombée") is True
        assert is_promoted(f, "fx|S>=3") is False

    def test_revocation_conserve_l_historique(self, tmp_path):
        """Comprendre pourquoi une promotion avait été accordée reste
        possible après sa révocation."""
        f = tmp_path / "p.json"
        promote(f, "fx|S>=3", _eligible(), approved_by="florent")
        revoke(f, "fx|S>=3", reason="régime changé")
        e = json.loads(f.read_text(encoding="utf-8"))["fx|S>=3"]
        assert e["approved_by"] == "florent"
        assert e["revoke_reason"] == "régime changé"
        assert e["verdict"]["n"] == 80

    def test_actives_ignore_les_revoquees(self, tmp_path):
        f = tmp_path / "p.json"
        promote(f, "fx|S>=3", _eligible(), approved_by="florent")
        promote(f, "crypto|S>=3", _eligible(), approved_by="florent")
        revoke(f, "crypto|S>=3", reason="x")
        assert set(actives(f)) == {"fx|S>=3"}

    def test_ecriture_atomique(self, tmp_path):
        """Un plantage en cours d'écriture ne doit pas laisser un registre
        tronqué — qui ouvrirait ou fermerait des cellules au hasard."""
        f = tmp_path / "p.json"
        promote(f, "fx|S>=3", _eligible(), approved_by="florent")
        assert not f.with_suffix(".tmp").exists()
        assert json.loads(f.read_text(encoding="utf-8"))
