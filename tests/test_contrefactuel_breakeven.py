"""Le contrefactuel de breakeven doit rester une BORNE, jamais une promesse."""

from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

import contrefactuel_breakeven as C  # noqa: E402


def t(**kw):
    base = dict(symbole="EURUSD", mae_r=-0.3, mfe_r=0.0, pnl_r=-1.0,
                sortie="init", contexte="")
    base.update(kw)
    return C.Trade(**base)


def test_un_stop_plein_dont_le_pic_depasse_le_seuil_est_sauve():
    r = C.contrefactuel([t(mfe_r=0.7, pnl_r=-1.0)], 0.5)
    assert len(r["sauves"]) == 1
    assert r["esperance"] == C.GAIN_BREAKEVEN_R


def test_un_stop_plein_sous_le_seuil_reste_perdu():
    r = C.contrefactuel([t(mfe_r=0.4, pnl_r=-1.0)], 0.5)
    assert r["sauves"] == []
    assert r["esperance"] == -1.0


def test_un_trade_deja_gere_n_est_jamais_recredite():
    """Seules les sorties `init` sont concernees : un trade sorti au trailing
    a deja beneficie de la gestion, le recrediter compterait deux fois."""
    r = C.contrefactuel([t(sortie="trailing", mfe_r=1.5, pnl_r=1.2)], 0.5)
    assert r["sauves"] == []
    assert r["esperance"] == 1.2


def test_les_gagnants_exposes_sont_signales_et_non_chiffres():
    r = C.contrefactuel([t(sortie="trailing", mfe_r=1.5, mae_r=-0.6, pnl_r=1.2)], 0.5)
    assert r["exposes"] == 1
    assert r["esperance"] == 1.2      # le risque est signale, jamais deduit


def test_ligne_illisible_est_sautee(tmp_path):
    chemin = tmp_path / "excursions.ndjson"
    chemin.write_text(
        json.dumps({"mfe_r": 0.7, "mae_r": -0.2, "pnl_r": -1.0,
                    "exit_reason": "init"}) + "\n{ pas du json\n",
        encoding="utf-8")
    assert len(C.charger(chemin)) == 1
