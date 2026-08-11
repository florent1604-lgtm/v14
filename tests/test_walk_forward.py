from __future__ import annotations

import json
from datetime import date, timedelta

from titanium.backtest import Resultat, Trade
from titanium.analysis.walk_forward import analyser_resultat, ecrire_rapport
from tools.walk_forward import build_parser


def _resultat(pnls: list[float]) -> Resultat:
    debut = date(2026, 1, 1)
    trades = []
    for i, pnl in enumerate(pnls):
        trades.append(Trade(
            symbol="EURUSD",
            side=1,
            bar_entree=str(debut + timedelta(days=i)),
            pnl_r=pnl,
            cost_r=0.1,
        ))
    return Resultat(symbol="EURUSD", trades=trades)


def test_fenetres_is_oos_glissantes_et_alerte_sharpe_negatif():
    resultat = _resultat([1.0, 1.2, 0.8, -0.5, -1.5, 0.5, 1.5])

    rapport = analyser_resultat(resultat, is_trades=3, oos_trades=2, step=2)

    assert rapport["symbol"] == "EURUSD"
    assert len(rapport["windows"]) == 2
    assert rapport["windows"][0]["is"]["n"] == 3
    assert rapport["windows"][0]["oos"]["n"] == 2
    assert rapport["windows"][0]["oos"]["sharpe_per_trade"] < 0
    assert rapport["windows"][0]["alert"] == "OOS_SHARPE_NEGATIF"
    assert rapport["windows"][1]["alert"] is None
    assert rapport["summary"]["negative_oos_windows"] == 1


def test_echantillon_insuffisant_est_explicite():
    rapport = analyser_resultat(
        _resultat([0.2, -0.1, 0.3]), is_trades=3, oos_trades=2, step=1)

    assert rapport["windows"] == []
    assert rapport["status"] == "INSUFFICIENT_TRADES"
    assert rapport["required_trades"] == 5


def test_rapport_json_est_ecrit_par_actif(tmp_path):
    rapport = analyser_resultat(
        _resultat([1.0, 1.2, 0.8, -0.5, -1.5]),
        is_trades=3,
        oos_trades=2,
        step=2,
    )

    cible = ecrire_rapport(rapport, tmp_path)

    assert cible == tmp_path / "EURUSD.json"
    relu = json.loads(cible.read_text(encoding="utf-8"))
    assert relu["symbol"] == "EURUSD"
    assert relu["windows"][0]["alert"] == "OOS_SHARPE_NEGATIF"


def test_cli_expose_les_tailles_is_oos_et_la_sortie():
    args = build_parser().parse_args([
        "EURUSD", "--is-trades", "40", "--oos-trades", "10",
        "--step", "5", "--output", "results/wf-test",
    ])

    assert args.symboles == ["EURUSD"]
    assert args.is_trades == 40
    assert args.oos_trades == 10
    assert args.step == 5
    assert args.output == "results/wf-test"
