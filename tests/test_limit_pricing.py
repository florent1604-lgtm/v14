"""Le prix d'entree passif de la boucle, teste une seule fois pour tous.

Ce module est la SOURCE UNIQUE du prix : la boucle live
(``titanium.execution.limit_orders``) et la politique ``v14_live`` du
simulateur l'appellent tous les deux. Le 24/08/2026, la revue Hermes H1 (P0-3)
a montre qu'une copie de cette formule avait deja diverge sur trois points en
quelques heures. Ces tests fixent le contrat pour que la prochaine divergence
soit rouge et non silencieuse.
"""
from __future__ import annotations

import math

import pytest

from titanium.execution.limit_pricing import (
    arrondi_passif,
    plan_limite_entree,
    ttl_du_spread,
)


def test_un_achat_au_spread_etroit_se_pose_au_bid():
    plan = plan_limite_entree(bid=1.10000, ask=1.10020, side=1,
                              stop_distance=0.0100, tick=1e-5, digits=5)
    assert plan.price == pytest.approx(1.10000)
    assert plan.saving_vs_market == pytest.approx(0.00020)
    assert plan.ttl_seconds == 600


def test_une_vente_est_le_miroir_exact_d_un_achat():
    achat = plan_limite_entree(bid=1.10000, ask=1.10500, side=1,
                               stop_distance=0.0100, tick=1e-5, digits=5)
    vente = plan_limite_entree(bid=1.10000, ask=1.10500, side=-1,
                               stop_distance=0.0100, tick=1e-5, digits=5)
    assert achat.passive_extra == pytest.approx(vente.passive_extra)
    assert achat.price < 1.10000 and vente.price > 1.10500
    assert (1.10000 - achat.price) == pytest.approx(vente.price - 1.10500)


def test_un_spread_couteux_exige_un_meilleur_prix_et_expire_plus_vite():
    etroit = plan_limite_entree(bid=100.0, ask=100.10, side=1,
                                stop_distance=10.0, tick=0.01, digits=2)
    large = plan_limite_entree(bid=100.0, ask=102.0, side=1,
                               stop_distance=10.0, tick=0.01, digits=2)
    assert large.price < etroit.price
    assert large.ttl_seconds < etroit.ttl_seconds


@pytest.mark.parametrize("spread_r,ttl", [
    (0.0, 600), (0.08, 600), (0.0801, 300), (0.15, 300), (0.1501, 120), (5.0, 120),
])
def test_la_duree_de_validite_suit_le_poids_du_spread(spread_r, ttl):
    assert ttl_du_spread(spread_r) == ttl


@pytest.mark.parametrize("tick,digits", [(0.25, 2), (0.5, 1), (1e-5, 5), (0.1, 1)])
def test_le_prix_est_toujours_un_multiple_du_tick(tick, digits):
    """Un prix qui n'est pas un multiple du tick est refuse par le courtier."""
    for side in (1, -1):
        plan = plan_limite_entree(bid=99.98, ask=100.23, side=side,
                                  stop_distance=3.0, tick=tick, digits=digits)
        assert plan.price / tick == pytest.approx(round(plan.price / tick))


def test_l_arrondi_ne_traverse_jamais_le_carnet():
    assert arrondi_passif(99.99, tick=0.25, digits=2, side=1) == pytest.approx(99.75)
    assert arrondi_passif(99.99, tick=0.25, digits=2, side=-1) == pytest.approx(100.0)


@pytest.mark.parametrize("kwargs", [
    {"bid": 1.1, "ask": 1.1002, "side": 0, "stop_distance": 0.004},
    {"bid": 1.1, "ask": 1.1002, "side": 2, "stop_distance": 0.004},
    {"bid": 1.1, "ask": 1.1002, "side": 1, "stop_distance": 0.0},
    {"bid": 1.1, "ask": 1.1002, "side": 1, "stop_distance": -0.004},
    {"bid": 1.1, "ask": 1.1002, "side": 1, "stop_distance": float("nan")},
    {"bid": 1.1, "ask": 1.1002, "side": 1, "stop_distance": float("inf")},
    {"bid": float("nan"), "ask": 1.1002, "side": 1, "stop_distance": 0.004},
    {"bid": 1.1, "ask": float("inf"), "side": 1, "stop_distance": 0.004},
    {"bid": 0.0, "ask": 1.1002, "side": 1, "stop_distance": 0.004},
    {"bid": 1.1005, "ask": 1.1002, "side": 1, "stop_distance": 0.004},
])
def test_toute_entree_douteuse_echoue_FERME(kwargs):
    """Aucune valeur par defaut n'est inventee : sans prix valide, pas d'ordre.

    Un stop nul etait accepte par la copie du simulateur alors que la boucle
    live le refuse : la politique « celle du bot » posait donc des ordres que
    le bot n'aurait jamais envoyes.
    """
    with pytest.raises(ValueError):
        plan_limite_entree(tick=1e-5, digits=5, **kwargs)


@pytest.mark.parametrize("tick", [0.0, -1.0, float("nan"), float("inf")])
def test_un_tick_inconnu_echoue_FERME(tick):
    with pytest.raises(ValueError):
        plan_limite_entree(bid=1.1, ask=1.1002, side=1, stop_distance=0.004,
                           tick=tick, digits=5)


def test_le_prix_reste_fini_et_positif_sur_toute_la_grille():
    """Balayage : aucune combinaison plausible ne doit produire un prix absurde."""
    for bid in (0.5, 1.1, 100.0, 18_000.0):
        for spread_r in (0.0, 0.01, 0.09, 0.2, 1.0):
            for side in (1, -1):
                stop = bid * 0.01
                ask = bid + spread_r * stop
                plan = plan_limite_entree(bid=bid, ask=ask, side=side,
                                          stop_distance=stop, tick=1e-5, digits=5)
                assert math.isfinite(plan.price) and plan.price > 0
                assert plan.saving_vs_market >= 0.0
                # La comparaison se fait AU TICK PRES : dans ce balayage
                # synthetique l'ask lui-meme n'est pas sur la grille de
                # cotation, et l'arrondi final au nombre de decimales du
                # symbole peut alors le franchir d'un dix-millioniemme. Un
                # courtier ne cote jamais hors grille.
                if side > 0:
                    assert plan.price <= bid + 1e-5
                else:
                    assert plan.price >= ask - 1e-5


def test_la_boucle_live_et_le_simulateur_appellent_la_meme_fonction():
    """Preuve d'identite, pas de ressemblance."""
    from titanium.execution import limit_orders
    from titanium.execution_sim import policies

    assert limit_orders.plan_limite_entree is plan_limite_entree
    assert policies.plan_limite_entree is plan_limite_entree
