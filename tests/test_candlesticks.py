"""Tests du moteur de chandeliers.

Trois règles font toute la valeur de ce module par rapport à une simple liste de
patterns. Elles viennent d'une red-team Codex sur V12, et ce sont elles que ces
tests verrouillent en priorité :

1. **le contexte tranche** — un marteau en pleine hausse n'est pas un achat ;
2. **un pattern `confirm=True` n'est qu'un candidat** — aucune lecture anticipée ;
3. **pas de somme de patterns corrélés** — une seule contribution.
"""

from __future__ import annotations

import pandas as pd
import pytest

from titanium.features.candlesticks import (
    NB_DETECTEURS,
    Candle,
    Pattern,
    analyze,
    net_bias,
    net_bias_on_df,
)


def df(*bougies) -> pd.DataFrame:
    """Chaque bougie est un tuple (open, high, low, close)."""
    return pd.DataFrame(list(bougies), columns=["open", "high", "low", "close"])


PLATE = (100.0, 100.5, 99.5, 100.1)


# ═══════════════════════════ Règle 1 — le contexte ═══════════════════════════

def test_marteau_en_baisse_est_un_achat():
    marteau = Pattern("marteau", +1, "reversal", 0.65, False, "", "needs_downtrend")
    assert net_bias([marteau], uptrend=False)["direction"] == 1


def test_marteau_en_hausse_est_ignore():
    """LE point : un marteau n'est un signal qu'APRÈS une baisse."""
    marteau = Pattern("marteau", +1, "reversal", 0.65, False, "", "needs_downtrend")
    assert net_bias([marteau], uptrend=True)["direction"] == 0


def test_etoile_filante_en_hausse_est_une_vente():
    p = Pattern("etoile_filante", -1, "reversal", 0.65, False, "", "needs_uptrend")
    assert net_bias([p], uptrend=True)["direction"] == -1


def test_etoile_filante_en_baisse_est_ignoree():
    p = Pattern("etoile_filante", -1, "reversal", 0.65, False, "", "needs_uptrend")
    assert net_bias([p], uptrend=False)["direction"] == 0


def test_contexte_inconnu_nignore_rien():
    """`uptrend=None` : on ne filtre pas plutôt que d'inventer une tendance."""
    p = Pattern("marteau", +1, "reversal", 0.65, False, "", "needs_downtrend")
    assert net_bias([p], uptrend=None)["direction"] == 1


def test_pattern_sans_contexte_passe_toujours():
    p = Pattern("marubozu", +1, "continuation", 0.6, False, "")
    for tendance in (True, False, None):
        assert net_bias([p], uptrend=tendance)["direction"] == 1


# ══════════════════ Règle 2 — confirmation, aucune anticipation ══════════════

def test_pattern_a_confirmer_ne_donne_aucun_biais():
    """Un candidat ne vaut rien tant que la bougie suivante ne l'a pas validé."""
    p = Pattern("marteau", +1, "reversal", 0.9, True, "", "")
    r = net_bias([p])
    assert r["direction"] == 0
    assert r["patterns"] == []
    assert "marteau" in r["pending_confirmation"]


def test_pattern_immediat_donne_un_biais():
    p = Pattern("marubozu", +1, "continuation", 0.6, False, "")
    assert net_bias([p])["direction"] == 1


def test_confirmation_validee_par_la_bougie_suivante():
    """Marteau en N−1, puis une verte qui clôture au-dessus : actionnable."""
    d = df(
        (105.0, 105.5, 104.0, 104.2),   # contexte baissier
        (104.0, 104.2, 103.8, 104.0),
        (104.0, 104.1, 100.0, 103.9),   # marteau en N−1
        (103.9, 105.0, 103.8, 104.8),   # confirme : verte, clôture > 103.9
    )
    r = net_bias_on_df(d, uptrend=False)
    assert r["direction"] == 1
    assert "marteau" in r["confirmed_from_prev"]


def test_confirmation_invalidee_ne_donne_rien():
    """Même marteau, mais la bougie suivante clôture en dessous : rien."""
    d = df(
        (105.0, 105.5, 104.0, 104.2),
        (104.0, 104.2, 103.8, 104.0),
        (104.0, 104.1, 100.0, 103.9),   # marteau
        (103.9, 104.0, 102.0, 102.5),   # rouge : n'a pas confirmé
    )
    r = net_bias_on_df(d, uptrend=False)
    assert "marteau" not in r.get("confirmed_from_prev", [])


# ══════════════════ Règle 3 — une seule contribution ═════════════════════════

def test_pas_de_somme_de_patterns_correles():
    """Trois patterns sur la même bougie ne valent pas trois fois l'information."""
    ps = [Pattern("a", +1, "reversal", 0.6, False, ""),
          Pattern("b", +1, "reversal", 0.5, False, ""),
          Pattern("c", +1, "continuation", 0.4, False, "")]
    r = net_bias(ps)
    assert r["score"] == pytest.approx(0.6), "seul le plus fort compte"
    assert len(r["patterns"]) == 1


def test_le_plus_fort_lemporte():
    ps = [Pattern("faible", +1, "reversal", 0.3, False, ""),
          Pattern("fort", -1, "reversal", 0.8, False, "")]
    r = net_bias(ps)
    assert r["direction"] == -1
    assert r["patterns"] == ["fort"]


# ═══════════════════════ détection sur vraies formes ═════════════════════════

def test_avalement_haussier():
    d = df(PLATE,
           (101.0, 101.2, 98.8, 99.0),      # rouge, corps [99.0 ; 101.0]
           (98.8, 102.0, 98.5, 101.5))      # verte englobante, corps plus grand
    noms = [p.name for p in analyze(d)]
    assert "avalement_haussier" in noms


def test_avalement_baissier():
    d = df(PLATE,
           (99.0, 101.2, 98.8, 101.0),      # verte
           (101.5, 101.8, 98.0, 98.5))      # rouge englobante
    assert "avalement_baissier" in [p.name for p in analyze(d)]


def test_marteau():
    d = df(PLATE, PLATE, (104.0, 104.2, 100.0, 103.9))
    assert "marteau" in [p.name for p in analyze(d)]


def test_etoile_filante():
    d = df(PLATE, PLATE, (100.0, 104.0, 99.9, 100.2))
    assert "etoile_filante" in [p.name for p in analyze(d)]


def test_marubozu():
    d = df(PLATE, PLATE, (100.0, 105.05, 99.95, 105.0))
    assert "marubozu" in [p.name for p in analyze(d)]


def test_trois_soldats():
    d = df((100.0, 102.1, 99.9, 102.0),
           (102.0, 104.1, 101.9, 104.0),
           (104.0, 106.1, 103.9, 106.0))
    assert "trois_soldats" in [p.name for p in analyze(d)]


def test_etoile_du_matin():
    d = df((105.0, 105.2, 100.0, 100.2),    # grande rouge
           (100.0, 100.4, 99.6, 100.1),     # indécision
           (100.2, 103.5, 100.1, 103.0))    # forte hausse au-dessus du milieu
    assert "etoile_matin" in [p.name for p in analyze(d)]


def test_inside_bar():
    d = df(PLATE, (100.0, 106.0, 94.0, 105.0), (101.0, 103.0, 99.0, 102.0))
    assert "inside_bar" in [p.name for p in analyze(d)]


def test_les_douze_detecteurs_sont_la():
    """Garde-fou de non-régression : V12 en a douze, on en a douze."""
    assert NB_DETECTEURS == 12


# ═══════════════════════════ robustesse ══════════════════════════════════════

def test_bougie_plate_nest_pas_un_doji():
    """H = L : aucune amplitude. La traiter en doji inventerait un signal."""
    d = df(PLATE, PLATE, (100.0, 100.0, 100.0, 100.0))
    assert analyze(d) == []


def test_ohlc_incoherent_rejete():
    """Un high sous le low ne doit produire aucun pattern."""
    d = df(PLATE, PLATE, (100.0, 98.0, 102.0, 100.5))
    assert analyze(d) == []


@pytest.mark.parametrize("valeur", [float("nan"), float("inf")])
def test_valeurs_non_finies_rejetees(valeur):
    d = df(PLATE, PLATE, (100.0, valeur, 99.0, 100.5))
    assert analyze(d) == []


def test_historique_trop_court():
    assert analyze(df(PLATE)) == []
    assert analyze(df(PLATE, PLATE)) == []
    assert analyze(None) == []


def test_net_bias_on_df_ne_leve_jamais():
    for entree in (None, df(PLATE), df()):
        r = net_bias_on_df(entree)
        assert r["direction"] == 0


def test_candle_valide():
    assert Candle(100, 101, 99, 100.5).valid
    assert not Candle(100, 99, 101, 100.5).valid          # high < low
    assert not Candle(100, float("nan"), 99, 100.5).valid
    assert Candle(100, 100, 100, 100).flat


def test_doji_nest_ni_haussier_ni_baissier():
    """Clôture = ouverture : ni bull ni bear, strictement."""
    c = Candle(100, 101, 99, 100)
    assert not c.bull and not c.bear
