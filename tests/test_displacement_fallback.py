"""Secours G5 par displacement — mission Prime ba74e58a.

Ce que ces tests verrouillent :

1. le secours ne s'active QUE si le moteur de formes est muet ;
2. il ne peut pas retourner un avis existant ;
3. il respecte l'amplitude minimale et la fraîcheur ;
4. la trace nomme la source, sinon on ne pourra jamais comparer les deux.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from titanium.features import builder


# ═══════════════════════════════════════════════════════════════════════════════
# Fabriques de barres — un displacement se fabrique, il ne se cherche pas.
# ═══════════════════════════════════════════════════════════════════════════════

def _plat(n: int, prix: float = 100.0, amplitude: float = 0.10) -> pd.DataFrame:
    """n barres calmes autour de `prix`. Fixe l'ATR à ~`amplitude`."""
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    o = np.full(n, prix)
    c = np.full(n, prix)
    h = o + amplitude / 2
    l = o - amplitude / 2
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c,
                         "volume": np.full(n, 100.0)}, index=idx)


def _avec_displacement(n: int, *, sens: int, position: int,
                       ampleur_atr: float = 4.0) -> pd.DataFrame:
    """Barres calmes, puis UNE bougie de `ampleur_atr` × ATR à `position`.

    `position` est un index négatif : -1 = dernière barre.
    """
    amplitude = 0.10
    df = _plat(n, amplitude=amplitude).copy()
    i = len(df) + position
    saut = ampleur_atr * amplitude
    base = float(df["open"].iloc[i])
    if sens > 0:
        df.iloc[i, df.columns.get_loc("close")] = base + saut
        df.iloc[i, df.columns.get_loc("high")] = base + saut
        df.iloc[i, df.columns.get_loc("low")] = base
    else:
        df.iloc[i, df.columns.get_loc("close")] = base - saut
        df.iloc[i, df.columns.get_loc("low")] = base - saut
        df.iloc[i, df.columns.get_loc("high")] = base
    # Les barres suivantes repartent du nouveau niveau, sinon la bougie
    # suivante annulerait le mouvement et ce ne serait plus un displacement.
    nouveau = float(df["close"].iloc[i])
    for j in range(i + 1, len(df)):
        for col, val in (("open", nouveau), ("close", nouveau),
                         ("high", nouveau + amplitude / 2),
                         ("low", nouveau - amplitude / 2)):
            df.iloc[j, df.columns.get_loc(col)] = val
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# _displacement_dir
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisplacementDir:
    def test_marche_calme_aucun_displacement(self):
        d, force = builder._displacement_dir(_plat(60))
        assert d == 0
        assert force == 0.0

    def test_displacement_haussier_recent_detecte(self):
        df = _avec_displacement(60, sens=+1, position=-2)
        d, force = builder._displacement_dir(df)
        assert d == 1
        assert force > 0.0

    def test_displacement_baissier_recent_detecte(self):
        df = _avec_displacement(60, sens=-1, position=-2)
        d, _ = builder._displacement_dir(df)
        assert d == -1

    def test_displacement_trop_ancien_ignore(self):
        """Hors de la fenêtre de fraîcheur : plus rien à confirmer."""
        df = _avec_displacement(60, sens=+1, position=-18)
        d, _ = builder._displacement_dir(df)
        assert d == 0

    def test_amplitude_insuffisante_ignoree(self):
        """1 ATR n'est pas un displacement — c'est une bougie ordinaire."""
        df = _avec_displacement(60, sens=+1, position=-2, ampleur_atr=1.0)
        d, _ = builder._displacement_dir(df)
        assert d == 0

    def test_drapeau_eteint_neutralise_le_secours(self, monkeypatch):
        df = _avec_displacement(60, sens=+1, position=-2)
        assert builder._displacement_dir(df)[0] == 1
        monkeypatch.setattr(builder, "DISPLACEMENT_FALLBACK", False)
        assert builder._displacement_dir(df) == (0, 0.0)

    def test_ne_leve_jamais(self):
        """Un secours qui casse la chaîne est pire que pas de secours."""
        for mauvais in (_plat(3), pd.DataFrame(), _plat(60).assign(high=np.nan)):
            assert builder._displacement_dir(mauvais)[0] in (-1, 0, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Câblage dans build_feats — la règle du SECOURS
# ═══════════════════════════════════════════════════════════════════════════════

def _feats(df_ltf, monkeypatch, *, formes: tuple[int, float]):
    """Appelle build_feats avec un moteur de formes forcé."""
    direction, score = formes
    monkeypatch.setattr(
        builder.candlesticks, "net_bias_on_df",
        lambda df, **kw: {"direction": direction, "score": score,
                          "patterns": ["forcé"] if direction else [],
                          "pending_confirmation": [], "confirmed_from_prev": []})
    htf = _plat(200)
    return builder.build_feats(df_ltf, htf, with_indicators=False)


class TestCablage:
    def test_moteur_muet_le_secours_parle(self, monkeypatch):
        df = _avec_displacement(200, sens=+1, position=-2)
        f = _feats(df, monkeypatch, formes=(0, 0.0))
        assert f["candle"] == 1
        assert f["_trace"]["candle_source"] == "displacement"
        assert f["strengths"]["candle_confirmed"] > 0.0

    def test_moteur_qui_parle_garde_la_main(self, monkeypatch):
        """Le displacement dit +1, les formes disent −1 : les formes gagnent.

        C'est la règle qui distingue une ALIMENTATION d'un passage en force.
        Sur les 134 setups mesurés, 8 étaient dans ce cas.
        """
        df = _avec_displacement(200, sens=+1, position=-2)
        f = _feats(df, monkeypatch, formes=(-1, -0.8))
        assert f["candle"] == -1
        assert f["_trace"]["candle_source"] == "formes"
        assert f["strengths"]["candle_confirmed"] == pytest.approx(0.8)

    def test_ni_formes_ni_displacement(self, monkeypatch):
        f = _feats(_plat(200), monkeypatch, formes=(0, 0.0))
        assert f["candle"] == 0
        assert f["_trace"]["candle_source"] == ""
        assert f["strengths"]["candle_confirmed"] == 0.0

    def test_drapeau_eteint_restaure_l_ancien_comportement(self, monkeypatch):
        monkeypatch.setattr(builder, "DISPLACEMENT_FALLBACK", False)
        df = _avec_displacement(200, sens=+1, position=-2)
        f = _feats(df, monkeypatch, formes=(0, 0.0))
        assert f["candle"] == 0
        assert f["_trace"]["candle_source"] == ""

    def test_la_trace_porte_toujours_la_source(self, monkeypatch):
        """Sans ce champ, impossible de comparer l'espérance des deux sources."""
        for formes, attendu in (((0, 0.0), "displacement"), ((1, 0.9), "formes")):
            df = _avec_displacement(200, sens=+1, position=-2)
            f = _feats(df, monkeypatch, formes=formes)
            assert f["_trace"]["candle_source"] == attendu


# ═══════════════════════════════════════════════════════════════════════════════
# Non-régression : le secours ne touche QUE G5
# ═══════════════════════════════════════════════════════════════════════════════

def test_les_autres_piliers_sont_inchanges(monkeypatch):
    df = _avec_displacement(200, sens=+1, position=-2)
    avec = _feats(df, monkeypatch, formes=(0, 0.0))
    monkeypatch.setattr(builder, "DISPLACEMENT_FALLBACK", False)
    sans = _feats(df, monkeypatch, formes=(0, 0.0))
    for cle in ("fair_value", "liquidity", "ote", "trend", "setup_side"):
        assert avec[cle] == sans[cle], f"{cle} a bougé"


# ═══════════════════════════════════════════════════════════════════════════════
# Le champ doit ATTEINDRE le journal, pas seulement la trace
# ═══════════════════════════════════════════════════════════════════════════════

class TestJournalisation:
    """Un champ qui reste dans la trace ne sert à rien.

    Le protocole de promotion lit `_stratification`, figée à l'ouverture. Si
    `candle_source` n'y est pas, on ne pourra comparer les deux sources que
    sur des taux de passage — jamais sur des résultats.
    """

    def test_la_source_atteint_la_stratification(self):
        from tools.live_demo import _stratification

        feats = {"data_valid": True, "setup_side": 1, "trend": 1,
                 "fair_value": True, "liquidity": 1, "ote": 1, "candle": 1,
                 "on_sr_level": True,
                 "cost": {"edge_ok": True, "weekend_block": False},
                 "strengths": {}, "emotion": dict(builder.NEUTRAL_EMOTION),
                 "_trace": {"candle_source": "displacement",
                            "timeframe": "M15"}}
        assert _stratification("EURUSD", feats, 1)["candle_source"] == "displacement"

    def test_absence_de_source_donne_une_chaine_vide(self):
        """Les enregistrements d'avant le correctif doivent rester lisibles."""
        from tools.live_demo import _stratification

        feats = {"data_valid": True, "setup_side": 1, "trend": 1,
                 "fair_value": True, "liquidity": 1, "ote": 1, "candle": 1,
                 "on_sr_level": True,
                 "cost": {"edge_ok": True, "weekend_block": False},
                 "strengths": {}, "emotion": dict(builder.NEUTRAL_EMOTION),
                 "_trace": {"timeframe": "M15"}}
        assert _stratification("EURUSD", feats, 1)["candle_source"] == ""
