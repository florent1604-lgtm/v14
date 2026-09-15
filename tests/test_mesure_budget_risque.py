"""Le budget de risque est mesure, pas raconte.

Ces tests n'ont besoin NI de MT5 NI d'un compte : ils figent les entrees et
verifient l'arithmetique que l'outil publie. Ce qui est verifie ici est ce qui
rend la mesure opposable — l'invariant, les conversions, et le fait que le
risque d'UN trade n'a pas bouge quand l'enveloppe triplait.
"""

from __future__ import annotations

import pytest

from tools import mesure_budget_risque as mbr

EQUITE = 1_865.0


@pytest.mark.unit
def test_les_plafonds_sont_lus_dans_le_code_pas_recopies():
    """Les valeurs viennent des modules qui les declarent, pas d'une constante."""
    from titanium.correlation import MAX_RISQUE_GRAPPE_PCT
    from tools.live_demo import MAX_RISQUE_CUMULE_PCT, MAX_RISQUE_PANIER_PCT

    p = mbr.plafonds()
    assert p.grappe_pct == MAX_RISQUE_GRAPPE_PCT == 5.7
    assert p.global_pct == MAX_RISQUE_CUMULE_PCT == 17.1
    assert p.panier_pct == MAX_RISQUE_PANIER_PCT == 3.0
    assert p.par_trade_pct == 2.0


@pytest.mark.unit
def test_l_invariant_trois_grappes_saturent_l_enveloppe():
    """`3 x 5,7 = 17,1` : la hausse n'est pas un chiffre rond choisi au juge."""
    p = mbr.plafonds()
    assert p.invariant_tenu
    assert p.grappes_saturantes == pytest.approx(3.0)


@pytest.mark.unit
def test_l_ancien_couple_tenait_le_meme_invariant():
    """Avant, 3 x 2 = 6 : la structure n'a pas change, l'echelle si."""
    assert pytest.approx(3 * mbr.ANCIEN_GRAPPE_PCT) == mbr.ANCIEN_GLOBAL_PCT


@pytest.mark.unit
def test_les_conversions_en_euros():
    assert mbr.euros(EQUITE, 5.7) == pytest.approx(106.305)
    assert mbr.euros(EQUITE, 17.1) == pytest.approx(318.915)
    assert mbr.euros(EQUITE, 2.0) == pytest.approx(37.3)
    assert mbr.euros(EQUITE, 6.0) == pytest.approx(111.9)


@pytest.mark.unit
def test_le_facteur_est_le_meme_sur_les_deux_plafonds():
    """Un triplement coherent : 5,7/2 et 17,1/6 valent le meme rapport."""
    p = mbr.plafonds()
    assert p.grappe_pct / mbr.ANCIEN_GRAPPE_PCT == pytest.approx(2.85)
    assert p.global_pct / mbr.ANCIEN_GLOBAL_PCT == pytest.approx(2.85)


@pytest.mark.unit
def test_le_risque_d_un_trade_n_a_pas_triple():
    """Le point qui compte : c'est l'ENVELOPPE qui a triple, pas le trade."""
    p = mbr.plafonds()
    assert p.par_trade_pct == 2.0, "plafond par trade : inchange"
    assert p.panier_pct == 3.0, "panier par symbole : inchange"
    assert p.par_trade_pct < p.panier_pct < p.grappe_pct < p.global_pct


@pytest.mark.unit
def test_le_rapport_chiffre_le_scenario_sature(capsys):
    """Sans livre lisible, les plafonds restent chiffrables et l'invariant dit."""
    livre = mbr.Livre(False, motif="test")
    resultat = mbr.rapport(livre, EQUITE)
    sortie = capsys.readouterr().out

    assert resultat["equite_eur"] == EQUITE
    assert resultat["source_equite"] == "--equite"
    assert resultat["invariant"]["tenu"] is True
    assert resultat["euros"]["nouveau_grappe"] == pytest.approx(106.31, abs=0.01)
    assert resultat["euros"]["nouveau_global"] == pytest.approx(318.92, abs=0.01)
    assert resultat["euros"]["ancien_global"] == pytest.approx(111.9, abs=0.01)
    assert resultat["euros"]["facteur"] == pytest.approx(2.85)
    # Le scenario sature ne peut pas etre invente sans livre : il le dit.
    assert "non calculables" in sortie
    # Et la limite est ecrite noir sur blanc.
    assert "Aucune arene" in sortie


@pytest.mark.unit
def test_le_scenario_sature_chiffre_exposition_et_marge():
    """Avec un livre, le notionnel et la marge sont extrapoles du ratio MESURE."""
    ligne = mbr.Ligne(
        symbole="DAX40.fs", ticket="1", volume=0.01, prix=25_540.0,
        stop_distance=96.07, risque_eur=24.02,
        notionnel_eur=6_391.38, marge_eur=63.91,
    )
    livre = mbr.Livre(True, equite=EQUITE, solde=1_905.0, levier=100.0,
                      devise="EUR", serveur="Axi-US50-Demo", lignes=(ligne,))
    resultat = mbr.rapport(livre, None)

    assert resultat["equite_eur"] == EQUITE
    assert resultat["ratio_notionnel_sur_risque"] == pytest.approx(266.08, abs=0.01)
    sature = resultat["scenario_sature"]
    assert sature["risque_eur"] == pytest.approx(318.92, abs=0.01)
    assert sature["perte_si_tous_stops"] == sature["risque_eur"]
    assert sature["marge_eur"] == pytest.approx(
        sature["notionnel_eur"] / 100.0, abs=0.01
    )
    assert livre.risque_eur == pytest.approx(24.02)


@pytest.mark.unit
def test_un_risque_nul_n_invente_pas_de_notionnel(capsys):
    """Sans risque, le ratio n'existe pas : l'outil doit le dire, pas diviser."""
    ligne = mbr.Ligne(symbole="X", ticket="1", volume=0.0, prix=0.0,
                      stop_distance=0.0, risque_eur=0.0,
                      notionnel_eur=0.0, marge_eur=0.0)
    livre = mbr.Livre(True, equite=EQUITE, levier=100.0, lignes=(ligne,))
    resultat = mbr.rapport(livre, None)
    assert "notionnel_eur" not in resultat["scenario_sature"]
    assert "non calculables ici" in capsys.readouterr().out
