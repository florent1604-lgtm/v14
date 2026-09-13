"""Convention unique du spread entre classement, backtest et live."""

from __future__ import annotations

import pytest

from titanium.backtest import cout_spread_r
from titanium.echelle import (CODE_COUT_SPREAD, cout_relatif_stop,
                              verdict_cout)
from titanium.selection import _cout_relatif
from titanium.sizing import MAX_COUT_SPREAD_PCT


class _Spec:
    spread = 20
    point = 0.00001


class _SpecExact:
    """Spread exactement représentable : le seuil peut tomber pile."""

    spread = 1
    point = 0.125


def test_un_spread_complet_n_est_jamais_double() -> None:
    spread_prix = _Spec.spread * _Spec.point
    distance = 0.002

    assert cout_spread_r(spread_prix, distance) == pytest.approx(0.10)
    assert cout_relatif_stop(_Spec(), distance) == pytest.approx(0.10)
    assert _cout_relatif(spread_prix, distance) == pytest.approx(0.10)


def test_un_cout_egal_au_plafond_passe() -> None:
    """Les trois portes testaient ``> plafond`` : l'égalité est acceptée.

    spread_prix = 0.125 · stop = 1.0 -> coût = 0.125, PILE au plafond.
    """
    verdict = verdict_cout(_SpecExact(), 1.0, plafond=MAX_COUT_SPREAD_PCT)
    assert verdict.cout == MAX_COUT_SPREAD_PCT
    assert verdict.depasse is False
    assert verdict.code == ""


def test_un_spread_compte_deux_fois_refuserait_ce_qui_passe() -> None:
    """Le défaut documenté par `sizing.py` : une version doublait un spread
    ask-bid déjà complet, donc appliquait 12,5 % là où elle affichait 25 %.

    Doubler le spread revient à diviser le stop par deux : l'actif qui passait
    pile au plafond doit alors être refusé, et le refus doit le dire.
    """
    spec = _SpecExact()
    assert verdict_cout(spec, 1.0,
                        plafond=MAX_COUT_SPREAD_PCT).depasse is False
    double = verdict_cout(spec, 0.5, plafond=MAX_COUT_SPREAD_PCT)
    assert double.cout == 2 * MAX_COUT_SPREAD_PCT
    assert double.depasse is True
    assert double.code == CODE_COUT_SPREAD


def test_le_code_de_refus_est_produit_avec_la_decision() -> None:
    """Le code ne se reconstitue plus depuis le texte du motif.

    Deux refus chiffrés différemment portent le MÊME code : le nom du code ne
    dépend pas de la phrase, et la phrase porte la valeur.
    """
    spec = _SpecExact()
    refus = verdict_cout(spec, 0.5, plafond=MAX_COUT_SPREAD_PCT)
    pire = verdict_cout(spec, 0.25, plafond=MAX_COUT_SPREAD_PCT)
    assert refus.code == pire.code == CODE_COUT_SPREAD
    assert refus.motif != pire.motif
    assert f"{refus.cout:.0%}" in refus.motif
    assert f"{pire.cout:.0%}" in pire.motif
