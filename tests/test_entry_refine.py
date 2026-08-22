"""Affinage d'entrée sur M5 — timing, zone, et le plancher de resserrement.

L'invariant qui justifie le portage séparé de V12 : **le plancher par défaut
laisse le SL intact**. V12 le fixe à 0.6, ce qui autorise −40 % sur la distance
de stop ; comme `cost_r = spread / r_unit`, cela multiplie le coût de
transaction par 1,67. V14 prend 1.0, ce qui neutralise l'ancrage tout en
gardant le timing et la zone.

Les tests couvrent donc trois choses distinctes : que le défaut ne resserre
rien, que le plancher V12 resserre bien quand on le demande explicitement, et
que le module ne lève jamais — un affinage raté ne doit pas empêcher le trade.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from titanium.features.entry_refine import (
    PLANCHER_SL_DEFAUT,
    Affinage,
    affiner,
)


def _df(highs, lows, closes=None, opens=None):
    highs = [float(x) for x in highs]
    lows = [float(x) for x in lows]
    closes = [float(x) for x in (closes if closes is not None else lows)]
    opens = [float(x) for x in (opens if opens is not None else closes)]
    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": np.ones(len(highs)),
    })


def _m5_avec_creux(creux: float, prix: float, n: int = 40) -> pd.DataFrame:
    """Série M5 en V, dont le seul swing bas est ``creux``.

    Surtout pas de palier plat : `_swings` retient un extremum par ÉGALITÉ, donc
    toute série de bas identiques produit un swing à chaque barre. Une première
    version de ces fixtures posait des bas constants et l'ancrage tombait sur le
    palier, pas sur le creux — le code avait raison, la fixture était fausse.
    """
    lows = [creux + abs(i - n // 2) * 0.05 for i in range(n)]
    highs = [lo + 1.0 for lo in lows]
    closes = [min(prix, hi) for hi in highs]
    return _df(highs, lows, closes=closes)


def _m5_avec_sommet(sommet: float, prix: float, n: int = 40) -> pd.DataFrame:
    """Miroir du précédent : série en Λ dont le seul swing haut est ``sommet``."""
    highs = [sommet - abs(i - n // 2) * 0.05 for i in range(n)]
    lows = [hi - 1.0 for hi in highs]
    closes = [max(prix, lo) for lo in lows]
    return _df(highs, lows, closes=closes)


# ── Le plancher par défaut ne resserre rien ────────────────────────────────

def test_defaut_ne_resserre_jamais_le_sl():
    """C'est l'invariant central du portage. Le SL sort tel qu'il est entré."""
    assert PLANCHER_SL_DEFAUT == 1.0
    df = _m5_avec_creux(creux=99.0, prix=100.0)
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert r.sl_mult == pytest.approx(1.5)


def test_defaut_conserve_l_ancrage_pour_information():
    """Le SL ne bouge pas, mais l'ancrage reste lisible pour un futur A/B."""
    df = _m5_avec_creux(creux=99.0, prix=100.0)
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert r.applique is True
    assert r.ancrage_sl == pytest.approx(99.0)
    # Aucune note de resserrement, puisqu'il n'y en a pas eu.
    assert not any("->" in n for n in r.notes)


# ── Le comportement V12 reste accessible, explicitement ────────────────────

def test_plancher_v12_resserre_bien():
    """Avec 0.6, un creux proche resserre le SL — jusqu'au plancher, pas en deçà."""
    df = _m5_avec_creux(creux=99.9, prix=100.0)   # 0,1 + 0,1 ATR = 0,2 ATR visé
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0,
                sl_mult_base=1.5, plancher_sl=0.6)
    assert r.sl_mult == pytest.approx(0.9)        # plancher 1.5 x 0.6, pas 0.2
    assert any("->" in n for n in r.notes)


def test_plancher_v12_n_elargit_jamais():
    """Un creux lointain ne doit pas élargir le SL au-delà de la base."""
    df = _m5_avec_creux(creux=80.0, prix=100.0)   # 20 ATR de distance
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0,
                sl_mult_base=1.5, plancher_sl=0.6)
    assert r.sl_mult == pytest.approx(1.5)


def test_resserrement_borne_le_cout_a_1_67x():
    """Traduction en coût : le plancher V12 borne l'inflation à 1/0,6."""
    df = _m5_avec_creux(creux=99.95, prix=100.0)
    base = 1.5
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0,
                sl_mult_base=base, plancher_sl=0.6)
    inflation_cout = base / r.sl_mult          # cost_r = spread / r_unit
    assert inflation_cout == pytest.approx(1 / 0.6, rel=1e-6)


# ── Le côté est respecté ───────────────────────────────────────────────────

def test_ancrage_achat_ignore_un_creux_au_dessus_du_prix():
    """Un creux au-dessus du prix ne protège pas un achat : aucun ancrage."""
    df = _m5_avec_creux(creux=100.5, prix=100.0)   # tout le V est au-dessus
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert r.ancrage_sl is None
    assert r.sl_mult == pytest.approx(1.5)


def test_ancrage_vente_cherche_un_sommet_au_dessus_du_prix():
    df = _m5_avec_sommet(sommet=101.0, prix=100.0)
    r = affiner("TEST", -1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert r.ancrage_sl == pytest.approx(101.0)


def test_ancrage_vente_ignore_un_sommet_sous_le_prix():
    df = _m5_avec_sommet(sommet=99.5, prix=100.0)
    r = affiner("TEST", -1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert r.ancrage_sl is None


def test_palier_plat_ancre_sur_le_palier_et_non_sur_le_creux():
    """Documente une propriété de `_swings`, pas un défaut d'ici.

    `_swings` retient un extremum par ÉGALITÉ. Sur un palier plat, chaque barre
    est donc un swing, et l'ancrage tombe sur le palier — le creux profond
    est ignoré parce qu'il est plus LOIN du prix. Bénin tant que le plancher
    vaut 1.0 ; piégeux dès qu'on le descend, car les paliers plats abondent en
    M5 sur les périodes creuses, celles où le spread est déjà mauvais.
    """
    n = 40
    lows = [99.5] * n
    lows[n // 2] = 99.0                       # vrai creux, plus loin du prix
    df = _df([lo + 1.0 for lo in lows], lows, closes=[100.0] * n)
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert r.ancrage_sl == pytest.approx(99.5)   # le palier, pas le creux


# ── Fail-safe : ne jamais lever, ne jamais bloquer le trade ────────────────

@pytest.mark.parametrize("m5", [None, pd.DataFrame()])
def test_sans_donnees_rend_un_affinage_neutre(m5):
    r = affiner("TEST", 1, m5, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert isinstance(r, Affinage)
    assert r.sl_mult == pytest.approx(1.5)
    assert r.applique is False
    assert r.score == 0.0


@pytest.mark.parametrize(
    "side,atr,prix,base",
    [(0, 1.0, 100.0, 1.5),       # sens nul
     (1, 0.0, 100.0, 1.5),       # ATR nul
     (1, 1.0, 0.0, 1.5),         # prix nul
     (1, 1.0, 100.0, 0.0),       # multiplicateur nul
     (1, -1.0, 100.0, 1.5)],     # ATR négatif
)
def test_entrees_degenerees_rendent_le_sl_de_base(side, atr, prix, base):
    df = _m5_avec_creux(creux=99.0, prix=100.0)
    r = affiner("TEST", side, df, atr_ref=atr, prix_ref=prix, sl_mult_base=base)
    assert r.applique is False
    assert r.sl_mult == pytest.approx(base)


@pytest.mark.parametrize("plancher", [0.0, -0.5, 1.5, float("nan")])
def test_plancher_hors_bornes_est_refuse(plancher):
    """Un plancher > 1 élargirait le SL, un plancher <= 0 l'annulerait."""
    df = _m5_avec_creux(creux=99.0, prix=100.0)
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0,
                sl_mult_base=1.5, plancher_sl=plancher)
    assert r.applique is False
    assert r.sl_mult == pytest.approx(1.5)


def test_valeurs_non_numeriques_ne_levent_pas():
    df = _m5_avec_creux(creux=99.0, prix=100.0)
    r = affiner("TEST", 1, df, atr_ref="abc", prix_ref=100.0, sl_mult_base=1.5)
    assert r.applique is False


# ── Le score reste borné ───────────────────────────────────────────────────

def test_score_borne_a_un():
    df = _m5_avec_creux(creux=99.0, prix=100.0)
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    assert 0.0 <= r.score <= 1.0


def test_as_dict_est_serialisable():
    import json
    df = _m5_avec_creux(creux=99.0, prix=100.0)
    r = affiner("TEST", 1, df, atr_ref=1.0, prix_ref=100.0, sl_mult_base=1.5)
    json.dumps(r.as_dict())
