"""L'audit d'edge doit distinguer le timing de l'exposition a la tendance."""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

import audit_edge_directionnel as A  # noqa: E402
from edge_directionnel import BarrierOutcome  # noqa: E402


def issue(outcome: int, side: int) -> BarrierOutcome:
    return BarrierOutcome(outcome=outcome, mfe_r=0.0, mae_r=0.0, bars=1, side=side)


def test_le_delta_meme_cote_annule_une_pure_derive():
    """Marche haussier : tout long gagne, tout short perd, aucun timing.

    Le signal est long et gagne ; les controles alternent les cotes et
    ressortent a 50 %. Le delta 'tous' affiche donc +50 points d'illusion,
    alors que le delta meme cote voit la verite : zero.
    """
    rows = [{
        "signal": issue(1, 1),
        "controls": [issue(1, 1), issue(-1, -1), issue(1, 1), issue(-1, -1)],
    }]
    assert A._delta(rows, lambda o, s: True)["delta_pts"] == 50.0
    assert A._delta(rows, lambda o, s: o.side == s.side)["delta_pts"] == 0.0


def test_un_vrai_avantage_de_timing_survit_au_meme_cote():
    rows = [{
        "signal": issue(1, 1),
        "controls": [issue(-1, 1), issue(-1, 1), issue(-1, -1), issue(1, -1)],
    }]
    assert A._delta(rows, lambda o, s: o.side == s.side)["delta_pts"] == 100.0


def test_la_derive_est_rapportee_par_sens():
    rows = [{
        "signal": issue(1, 1),
        "controls": [issue(1, 1), issue(1, 1), issue(-1, -1), issue(-1, -1)],
    }]
    d = A._derive(rows)
    assert d["p_long_pct"] == 100.0
    assert d["p_short_pct"] == 0.0


def test_un_signal_censure_n_est_jamais_compte():
    """Sans resolution, il n'y a pas d'issue a comparer."""
    rows = [{"signal": issue(0, 1), "controls": [issue(1, 1)]}]
    assert A._delta(rows, lambda o, s: True)["paires"] == 0


def test_un_signal_sans_controle_du_meme_cote_est_ecarte():
    rows = [{"signal": issue(1, 1), "controls": [issue(1, -1)]}]
    assert A._delta(rows, lambda o, s: o.side == s.side)["paires"] == 0
