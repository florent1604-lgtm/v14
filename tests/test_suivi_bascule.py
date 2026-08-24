"""Une bascule se juge sur des trades clos, jamais sur une intention.

Ces tests fixent les trois precautions du suivi : plancher d'effectif, temoin
hors FX, et ecart compare a son erreur type.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def _module():
    chemin = RACINE / "tools" / "suivi_bascule.py"
    spec = importlib.util.spec_from_file_location("suivi_bascule", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["suivi_bascule"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    return _module()


BASCULE = datetime(2026, 8, 24, 6, 20, tzinfo=timezone.utc)


def _trade(module, *, jour: int, heure: int, r: float, classe: str = "indices",
           famille: str = "continuation") -> dict:
    return {
        "closed_at": datetime(2026, 8, jour, heure, tzinfo=timezone.utc),
        "pnl_r": r, "asset_class": classe, "famille": famille,
        "context": f"SYM|long|{famille}|3p", "cost_r": 0.05,
    }


def test_sous_le_plancher_aucun_verdict(module):
    trades = [_trade(module, jour=23, heure=1, r=-1.0) for _ in range(50)]
    trades += [_trade(module, jour=24, heure=8, r=+1.0) for _ in range(5)]
    rapport = module.comparer(trades, BASCULE)
    assert rapport["global"]["apres"]["n"] == 5
    assert rapport["global"]["ecart"]["verdict"] == "INDECIS"


def test_une_hausse_franche_est_nommee(module):
    trades = [_trade(module, jour=23, heure=1, r=-1.0) for _ in range(60)]
    trades += [_trade(module, jour=24, heure=8, r=+1.0) for _ in range(60)]
    rapport = module.comparer(trades, BASCULE)
    assert rapport["global"]["ecart"]["verdict"] == "HAUSSE"
    assert rapport["global"]["ecart"]["delta_r"] == pytest.approx(2.0)


def test_un_ecart_dans_le_bruit_n_est_pas_une_hausse(module):
    r_avant = [-1.0, 1.0] * 30
    r_apres = [-1.0, 1.0] * 30
    r_apres[0] = 1.02
    trades = [_trade(module, jour=23, heure=1, r=r) for r in r_avant]
    trades += [_trade(module, jour=24, heure=8, r=r) for r in r_apres]
    rapport = module.comparer(trades, BASCULE)
    assert rapport["global"]["ecart"]["verdict"] == "INDISTINGUABLE"


def test_le_temoin_hors_fx_ignore_le_fx(module):
    trades = [_trade(module, jour=23, heure=1, r=-2.0, classe="fx")
              for _ in range(40)]
    trades += [_trade(module, jour=23, heure=2, r=+0.10) for _ in range(40)]
    trades += [_trade(module, jour=24, heure=8, r=+0.10) for _ in range(40)]
    rapport = module.comparer(trades, BASCULE)
    # Le global monte parce que le FX disparait...
    assert rapport["global"]["ecart"]["verdict"] == "HAUSSE"
    # ...mais le temoin, lui, ne bouge pas : c'est ce qui prouve que la hausse
    # vient de la suspension et non du marche.
    assert rapport["hors_fx"]["ecart"]["verdict"] == "INDISTINGUABLE"


def test_la_famille_est_lue_dans_la_cle_de_contexte(module):
    trades = [_trade(module, jour=24, heure=8, r=+1.0, famille="reversal")
              for _ in range(3)]
    rapport = module.comparer(trades, BASCULE)
    assert set(rapport["par_famille"]) == {"reversal"}


def test_une_ligne_illisible_est_ignoree_pas_fatale(module, tmp_path):
    journal = tmp_path / "trades.ndjson"
    journal.write_text("\n".join([
        json.dumps({"closed_at": "2026-08-24T08:00:00+00:00", "pnl_r": 1.0,
                    "asset_class": "indices", "context": "A|long|continuation|3p"}),
        "{ ceci n est pas du json",
        json.dumps({"closed_at": "pas une date", "pnl_r": 1.0}),
        json.dumps({"closed_at": "2026-08-24T09:00:00+00:00", "pnl_r": "NaN"}),
    ]), encoding="utf-8")
    trades = module.charger(journal)
    assert len(trades) == 1 and trades[0]["famille"] == "continuation"
