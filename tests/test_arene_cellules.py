"""Le proprietaire unique de la comparaison de deux tables d'arene.

Une seule regle doit dire si deux cellules different : sa cle d'appariement,
ses colonnes, son comptage. Ces tests fixent les deux POLITIQUES que les
harnais emploient — toutes les colonnes contre les cinq de decision, cellules
absentes comptees ou non — parce que c'est precisement ce qui etait ecrit deux
fois, dans `tools/comparer_budget_arene.py` et `tools/execution_adaptative.py`.

Sans MT5, sans arene : on compare des tables en memoire.
"""

from __future__ import annotations

import pytest

from tools import arene_cellules as arene

pytestmark = pytest.mark.unit


def test_la_cle_d_appariement_porte_la_politique_le_split_et_le_scenario():
    index = arene.indexer(
        [{"policy": "adapt_a", "split": "oos", "scenario_id": "s7", "net_pnl": 1.0}]
    )
    assert list(index) == ["adapt_a|oos|s7"]
    assert arene.projeter(index, ("net_pnl",)) == {"adapt_a|oos|s7": [1.0]}


def test_l_empreinte_ne_depend_pas_de_l_ordre_des_cles():
    assert arene.empreinte({"b": [2], "a": [1]}) == arene.empreinte({"a": [1], "b": [2]})


def test_un_ecart_de_cout_est_vu_a_resultat_constant():
    """Comparer le seul net laisserait passer une cellule dont le cout a bouge."""
    gauche = {"p|d|s0": [1.0, 0.5, 3.0]}
    droite = {"p|d|s0": [1.0, 0.5, 9.0]}
    resultat = arene.comparer(gauche, droite, colonnes=arene.COLONNES_DECISION)
    assert resultat["cellules_bougees"] == 1
    assert resultat["colonnes_bougees"] == {"total_cost_bps": 1}
    assert resultat["ecart_net_max"] == 0.0
    assert resultat["cellules_comparees"] == 1


def test_une_cellule_presente_d_un_seul_cote_ne_compte_que_si_on_le_demande():
    gauche = {"p|d|s0": [1.0], "p|d|s1": [2.0]}
    droite = {"p|d|s0": [1.0]}
    sans = arene.comparer(gauche, droite, colonnes=("net_pnl",))
    assert sans["cellules_comparees"] == 1
    assert sans["cellules_bougees"] == 0
    avec = arene.comparer(
        gauche, droite, colonnes=("net_pnl",), cellules_absentes_comptent=True
    )
    assert avec["cellules_comparees"] == 2
    assert avec["cellules_bougees"] == 1
    assert avec["colonnes_bougees"] == {arene.COLONNE_ABSENTE: 1}
    assert avec["bougees_par_politique"] == {"p": 1}
