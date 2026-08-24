"""Un seuil choisi sur le segment qui le juge ne mesure plus rien.

Ces tests fixent la discipline de la porte de cout : le choix se fait sur la
calibration seule, la verification ne sert qu'a juger, et le critere de choix
(esperance par trade ou R total) est un arbitrage explicite, pas un defaut
cache.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent


def _module():
    chemin = RACINE / "tools" / "porte_cout.py"
    spec = importlib.util.spec_from_file_location("porte_cout", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["porte_cout"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    return _module()


def _trades(lignes: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": s, "asset_class": c, "split": sp, "cost_r": co,
          "gross_r": n + co, "net_r": n, "r_unit": 1.0}
         for s, c, sp, co, n in lignes])


def test_la_verification_n_entre_pas_dans_le_choix(module):
    # Seuil 0,10 : excellent en calibration. Seuil 0,20 : excellent seulement
    # en verification. Le choix doit ignorer le second.
    lignes = [("A", "crypto", "calibration", 0.05, 1.0)] * 100
    lignes += [("A", "crypto", "calibration", 0.15, -1.0)] * 100
    lignes += [("A", "crypto", "verification", 0.15, 5.0)] * 100
    choix = module.choisir(module.courbe(_trades(lignes), (0.10, 0.20)))
    assert choix["seuil"] == 0.10


def test_le_critere_somme_ne_choisit_pas_la_solution_de_coin(module):
    lignes = [("A", "crypto", "calibration", 0.05, 1.0)] * 100
    lignes += [("A", "crypto", "calibration", 0.15, 0.5)] * 1000
    grille = (0.10, 0.20)
    trades = _trades(lignes)
    assert module.choisir(module.courbe(trades, grille),
                          critere="esperance")["seuil"] == 0.10
    assert module.choisir(module.courbe(trades, grille),
                          critere="somme")["seuil"] == 0.20


def test_une_cellule_trop_maigre_n_est_pas_eligible(module):
    lignes = [("A", "crypto", "calibration", 0.05, 9.0)] * 5
    lignes += [("A", "crypto", "calibration", 0.15, 0.1)] * 500
    choix = module.choisir(module.courbe(_trades(lignes), (0.10, 0.20)),
                           effectif_min=60)
    assert choix["seuil"] == 0.20


def test_la_courbe_publie_la_part_conservee_et_les_deux_segments(module):
    lignes = [("A", "fx", "calibration", 0.05, 1.0)] * 50
    lignes += [("A", "fx", "verification", 0.50, -1.0)] * 50
    courbe = module.courbe(_trades(lignes), (0.10,))
    sans, avec = courbe[0], courbe[1]
    assert sans["seuil"] is None and sans["part_conservee"] == 1.0
    assert avec["part_conservee"] == 0.5
    assert avec["calibration"]["n"] == 50 and avec["verification"]["n"] == 0


def test_critere_inconnu_refuse(module):
    with pytest.raises(ValueError):
        module.choisir([], critere="au_pif")


def test_la_mesure_separe_les_classes_d_actif(module):
    lignes = [("A", "crypto", "calibration", 0.05, 1.0)] * 100
    lignes += [("B", "fx", "calibration", 0.05, -1.0)] * 100
    rapport = module.mesurer(_trades(lignes), grille=(0.10,))
    assert set(rapport["par_classe"]) == {"crypto", "fx"}
    assert rapport["par_classe"]["crypto"]["choix"]["seuil"] == 0.10
    assert rapport["par_classe"]["fx"]["courbe"][1]["calibration"][
        "esperance_r"] == -1.0
