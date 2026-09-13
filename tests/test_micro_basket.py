from __future__ import annotations

import json

import pytest

from titanium.execution.micro_basket import (
    BasketMember,
    BasketParams,
    decide_basket_exit,
    load_basket_peaks,
    required_improvement_r,
    save_basket_peaks,
)


def test_espacement_retient_le_plus_strict_atr_spread_ou_base():
    assert required_improvement_r(
        stop_distance=10.0, atr=8.0, spread=2.0,
    ) == pytest.approx(0.4)
    assert required_improvement_r(
        stop_distance=10.0, atr=2.0, spread=0.1,
    ) == pytest.approx(0.1)


def test_espacement_invalide_refuse_fail_closed():
    assert required_improvement_r(stop_distance=0.0) is None
    assert required_improvement_r(stop_distance=1.0, spread=-0.1) is None


def test_panier_s_arme_puis_sort_sur_restitution_agregee():
    params = BasketParams(arm_r=0.6, min_lock_r=0.1,
                          max_giveback_r=0.45, min_retention=0.45)
    sommet = decide_basket_exit(
        [BasketMember("1", 0.8, 100), BasketMember("2", 0.6, 50)],
        params=params,
    )
    assert sommet.should_exit is False
    assert sommet.current_r == pytest.approx((0.8 * 100 + 0.6 * 50) / 150)
    assert sommet.peak_r == pytest.approx(sommet.current_r)

    retour = decide_basket_exit(
        [BasketMember("1", 0.2, 100), BasketMember("2", 0.1, 50)],
        previous_peak_r=sommet.peak_r,
        params=params,
    )
    assert retour.should_exit is True
    assert retour.reason == "AVANTAGE_PANIER_RESTITUE"
    assert retour.floor_r > retour.current_r


def test_panier_utilise_des_poids_egaux_si_un_risque_manque():
    decision = decide_basket_exit([
        BasketMember("1", 1.0, 100),
        BasketMember("2", -1.0, 0),
    ])
    assert decision.current_r == pytest.approx(0.0)


def test_une_position_ne_declenche_jamais_un_panier():
    decision = decide_basket_exit([BasketMember("1", -3.0, 100)])
    assert decision.should_exit is False
    assert decision.reason == "PANIER_INCOMPLET"


def test_pics_panier_roundtrip_atomique(tmp_path):
    path = tmp_path / "micro_baskets.json"
    save_basket_peaks(path, {"XAUUSD": 0.81})
    assert load_basket_peaks(path) == {"XAUUSD": pytest.approx(0.81)}
    assert json.loads(path.read_text(encoding="utf-8")) == {"XAUUSD": 0.81}
    assert not list(tmp_path.glob("*.tmp"))
