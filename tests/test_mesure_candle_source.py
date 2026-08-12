"""Comparaison d'espérance formes / displacement — revue Hermes 5982eb27.

Ce que ces tests verrouillent, dans l'ordre d'importance :

1. l'outil REFUSE de conclure sous le seuil d'échantillon — c'est sa raison
   d'être, un verdict sur cinq trades vaut moins que pas de verdict ;
2. « champ absent » et « champ vide » ne se mélangent pas — le premier veut
   dire « on ne sait pas », le second « on sait que G5 n'a pas été fourni » ;
3. le PF ne vaut jamais l'infini quand il n'y a aucune perte ;
4. le sens du verdict suit le signe de l'écart, pas l'inverse.
"""

from __future__ import annotations

import json

import pytest

from tools import mesure_candle_source as mcs


def _t(r: float, source=..., piliers: int = 2) -> dict:
    """Une clôture. `source=...` signifie champ ABSENT, à distinguer de ''."""
    t = {"pnl_r": r, "support_pillars": piliers}
    if source is not ...:
        t["candle_source"] = source
    return t


def _lot(n: int, r: float, source: str) -> list[dict]:
    return [_t(r, source) for _ in range(n)]


# ═══════════════════════════════════════════════════════════════════════════════
# Chargement
# ═══════════════════════════════════════════════════════════════════════════════

class TestChargement:
    def test_fichier_absent_rend_liste_vide(self, tmp_path):
        assert mcs.charger(tmp_path / "rien.ndjson") == []

    def test_ligne_cassee_ne_tue_pas_la_lecture(self, tmp_path):
        p = tmp_path / "j.ndjson"
        p.write_text('{"pnl_r": 1.0}\n{ pas du json\n{"pnl_r": -1.0}\n',
                     encoding="utf-8")
        assert len(mcs.charger(p)) == 2

    def test_pnl_manquant_ou_non_numerique_ecarte(self, tmp_path):
        p = tmp_path / "j.ndjson"
        p.write_text('{"pnl_r": 1.0}\n{"pnl_r": null}\n{"pnl_r": "x"}\n{}\n',
                     encoding="utf-8")
        assert len(mcs.charger(p)) == 1

    def test_nan_ecarte(self, tmp_path):
        """NaN passe isinstance(float) mais empoisonne toute moyenne."""
        p = tmp_path / "j.ndjson"
        p.write_text('{"pnl_r": NaN}\n{"pnl_r": 0.5}\n', encoding="utf-8")
        assert len(mcs.charger(p)) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Statistiques
# ═══════════════════════════════════════════════════════════════════════════════

class TestStats:
    def test_lot_vide(self):
        assert mcs.stats([])["n"] == 0

    def test_esperance_et_cumul(self):
        s = mcs.stats([_t(1.0), _t(-0.5), _t(0.25)])
        assert s["esperance"] == pytest.approx(0.25)
        assert s["somme_r"] == pytest.approx(0.75)
        assert s["winrate"] == pytest.approx(2 / 3)

    def test_pf_sans_perte_vaut_none_pas_l_infini(self):
        """`inf` traverse le JSON en `Infinity`, illisible par tout consommateur."""
        assert mcs.stats([_t(1.0), _t(2.0)])["pf"] is None

    def test_pf_ordinaire(self):
        assert mcs.stats([_t(2.0), _t(-1.0)])["pf"] == pytest.approx(2.0)

    def test_erreur_type_decroit_avec_l_echantillon(self):
        petit = mcs.stats([_t(1.0), _t(-1.0)])
        grand = mcs.stats([_t(1.0), _t(-1.0)] * 32)
        assert grand["erreur_type"] < petit["erreur_type"]

    def test_un_seul_trade_erreur_type_nulle(self):
        s = mcs.stats([_t(1.0)])
        assert s["erreur_type"] == 0.0
        assert s["ecart_type"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Le refus de conclure — la raison d'être de l'outil
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerdict:
    def test_echantillon_insuffisant_aucun_verdict(self):
        txt = mcs.rapport(_lot(3, 1.0, "displacement") + _lot(3, -1.0, "formes"))
        assert "AUCUN VERDICT" in txt
        assert "FAVORABLE" not in txt
        assert "DÉFAVORABLE" not in txt

    def test_le_manque_est_chiffre(self):
        txt = mcs.rapport(_lot(5, 1.0, "displacement") + _lot(2, 1.0, "formes"))
        assert f"Il manque {mcs.N_MINIMAL - 2} clôture(s)" in txt

    def test_une_seule_branche_fournie_ne_suffit_pas(self):
        txt = mcs.rapport(_lot(mcs.N_MINIMAL + 10, 0.5, "displacement"))
        assert "AUCUN VERDICT" in txt

    def test_ecart_net_defavorable(self):
        txt = mcs.rapport(_lot(mcs.N_MINIMAL, +1.0, "formes")
                          + _lot(mcs.N_MINIMAL, -1.0, "displacement"))
        assert "DÉFAVORABLE" in txt
        assert "DISPLACEMENT_FALLBACK = False" in txt

    def test_ecart_net_favorable(self):
        txt = mcs.rapport(_lot(mcs.N_MINIMAL, -1.0, "formes")
                          + _lot(mcs.N_MINIMAL, +1.0, "displacement"))
        assert "FAVORABLE" in txt
        assert "DÉFAVORABLE" not in txt

    def test_ecart_dans_le_bruit_reste_indecis(self):
        """Deux branches identiques : z = 0, aucune conclusion permise."""
        trades = ([_t(r, "formes") for r in (1.0, -1.0)] * mcs.N_MINIMAL
                  + [_t(r, "displacement") for r in (1.0, -1.0)] * mcs.N_MINIMAL)
        txt = mcs.rapport(trades)
        assert "INDÉCIS" in txt


# ═══════════════════════════════════════════════════════════════════════════════
# « Absent » et « vide » ne sont pas la même chose
# ═══════════════════════════════════════════════════════════════════════════════

class TestEpoques:
    def test_champ_absent_range_hors_epoque(self):
        txt = mcs.rapport([_t(1.0), _t(-1.0)])
        assert "(hors époque)" in txt

    def test_champ_vide_range_a_part(self):
        txt = mcs.rapport([_t(1.0, ""), _t(-1.0, "")])
        assert "(aucune)" in txt

    def test_les_deux_ne_se_melangent_pas(self):
        """Sinon « on ne sait pas » serait compté comme « on sait que non »."""
        txt = mcs.rapport([_t(1.0), _t(-1.0, "")])
        assert "(hors époque)" in txt and "(aucune)" in txt

    def test_hors_epoque_n_entre_pas_dans_le_verdict(self):
        """29 trades d'avant le correctif ne doivent pas trancher pour lui."""
        trades = (_lot(100, -1.0, None) if False else [_t(-1.0) for _ in range(100)])
        trades += _lot(mcs.N_MINIMAL, +1.0, "formes")
        trades += _lot(mcs.N_MINIMAL, +1.0, "displacement")
        txt = mcs.rapport(trades)
        assert "INDÉCIS" in txt  # formes == displacement, le lot ancien est ignoré


# ═══════════════════════════════════════════════════════════════════════════════
# Sortie
# ═══════════════════════════════════════════════════════════════════════════════

def test_le_rapport_est_serialisable():
    """Un `inf` dans les stats casserait `--json` chez le consommateur."""
    txt = mcs.rapport(_lot(3, 1.0, "displacement"))
    json.dumps({"rapport": txt})  # ne doit pas lever


def test_repartition_par_piliers_affichee():
    txt = mcs.rapport([_t(1.0, "displacement", piliers=3),
                       _t(-1.0, "formes", piliers=2)])
    assert "RÉPARTITION PAR NOMBRE DE PILIERS" in txt


# ═══════════════════════════════════════════════════════════════════════════════
# Variance nulle — le cas dégénéré qui inversait la lecture
# ═══════════════════════════════════════════════════════════════════════════════

class TestVarianceNulle:
    """Une branche dont tous les trades meurent au stop clôture à −1R pile.

    L'erreur-type y vaut 0, donc `écart / erreur_type` divise par zéro. La
    première version rendait `z = 0` dans ce cas et concluait « indécis » —
    exactement l'inverse de la vérité, puisque deux branches sans dispersion
    et de moyennes différentes sont la configuration la PLUS décisive.
    """

    def test_deux_branches_sans_dispersion_ecart_net_tranche(self):
        txt = mcs.rapport(_lot(mcs.N_MINIMAL, +1.0, "formes")
                          + _lot(mcs.N_MINIMAL, -1.0, "displacement"))
        assert "DÉFAVORABLE" in txt
        assert "INDÉCIS" not in txt
        assert "z infini" in txt

    def test_sens_inverse(self):
        txt = mcs.rapport(_lot(mcs.N_MINIMAL, -1.0, "formes")
                          + _lot(mcs.N_MINIMAL, +1.0, "displacement"))
        assert "FAVORABLE" in txt and "DÉFAVORABLE" not in txt

    def test_branches_identiques_sans_dispersion_restent_indecises(self):
        txt = mcs.rapport(_lot(mcs.N_MINIMAL, 0.5, "formes")
                          + _lot(mcs.N_MINIMAL, 0.5, "displacement"))
        assert "INDÉCIS" in txt
        assert "branches identiques" in txt

    def test_le_rapport_reste_serialisable_avec_z_infini(self):
        """`math.inf` ne doit jamais atteindre la sortie JSON."""
        txt = mcs.rapport(_lot(mcs.N_MINIMAL, +1.0, "formes")
                          + _lot(mcs.N_MINIMAL, -1.0, "displacement"))
        json.dumps({"rapport": txt})
        assert "Infinity" not in txt
