"""Convention unique du spread entre classement, backtest et live."""

from __future__ import annotations

import pytest

from titanium.backtest import cout_spread_r
from titanium.echelle import cout_relatif_stop
from titanium.selection import _cout_relatif


class _Spec:
    spread = 20
    point = 0.00001


def test_un_spread_complet_n_est_jamais_double() -> None:
    spread_prix = _Spec.spread * _Spec.point
    distance = 0.002

    assert cout_spread_r(spread_prix, distance) == pytest.approx(0.10)
    assert cout_relatif_stop(_Spec(), distance) == pytest.approx(0.10)
    assert _cout_relatif(spread_prix, distance) == pytest.approx(0.10)
