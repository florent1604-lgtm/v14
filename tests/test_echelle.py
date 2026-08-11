"""Échelle adaptative — mesurer la volatilité là où elle existe encore.

Le 08/08/2026, les TRENTE cryptos étaient refusées par la porte de coût. La
cause n'était pas le spread mais l'effondrement du dénominateur : sur
BTCUSD, l'amplitude d'une barre M15 tombe à 58 % de la semaine (mesuré sur
4 805 barres de week-end, 6 mois). Le stop devenait plus petit que le
spread.

    BTCUSD samedi    M15  18 %      H1  4 %      H4  2 %

Ces tests verrouillent deux choses : qu'on élargit du STRICT nécessaire, et
qu'un actif réellement injouable reste refusé — assouplir le plafond aurait
ramené les pertes du 07/08.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from titanium.echelle import BARRES_ATR, ECHELLE, choisir, cout_relatif_stop


class _Spec:
    def __init__(self, spread, point=0.00001):
        self.spread = spread
        self.point = point


def _lecteur(amplitudes):
    """Rend un lecteur dont chaque unite a l'amplitude demandee."""
    def lire(sym, tf, n):
        a = amplitudes.get(tf, 0.0)
        idx = pd.date_range(datetime.now(timezone.utc) - timedelta(hours=n),
                            periods=n, freq="15min", tz="UTC")
        return pd.DataFrame({
            "open": [1.0] * n, "close": [1.0] * n,
            "high": [1.0 + a / 2] * n, "low": [1.0 - a / 2] * n}, index=idx)
    return lire


class TestChoix:
    def test_un_aller_retour_paie_un_spread_complet(self):
        """`spec.spread` est déjà ask-bid : ne pas le doubler une seconde fois.

        C'est la même convention que le backtest : demi-spread à l'entrée et
        demi-spread à la sortie, soit un spread complet au total.
        """
        assert cout_relatif_stop(_Spec(10), 0.0010) == pytest.approx(0.10)

    def test_m15_suffit_on_ne_bouge_pas(self):
        """L'unité de référence gagne toujours si elle passe : un stop plus
        large immobilise un créneau plus longtemps."""
        c = choisir("X", _Spec(10), plafond=0.25,
                    lecteur=_lecteur({"M15": 0.0020, "H1": 0.0100,
                                      "H4": 0.0400}))
        assert c.timeframe == "M15"
        assert c.elargie is False
        assert c.motif == ""

    def test_elargit_quand_la_volatilite_s_effondre(self):
        """Le cas BTCUSD du samedi : M15 injouable, H1 confortable."""
        c = choisir("X", _Spec(200), plafond=0.25,
                    lecteur=_lecteur({"M15": 0.0020, "H1": 0.0300,
                                      "H4": 0.1000}))
        assert c.timeframe == "H1"
        assert c.elargie is True
        assert "élargi" in c.motif

    def test_prend_la_PLUS_PETITE_qui_passe(self):
        """H1 et H4 passent tous deux : on retient H1.

        Un stop H4 tient des heures et bloque un créneau sur huit ; on
        n'élargit que du strict nécessaire.
        """
        c = choisir("X", _Spec(200), plafond=0.25,
                    lecteur=_lecteur({"M15": 0.0020, "H1": 0.0300,
                                      "H4": 0.2000}))
        assert c.timeframe == "H1"

    def test_injouable_a_toute_echelle_reste_refuse(self):
        """Assouplir le plafond aurait ramené les pertes du 07/08."""
        c = choisir("X", _Spec(4000), plafond=0.25,
                    lecteur=_lecteur({"M15": 0.0020, "H1": 0.0050,
                                      "H4": 0.0100}))
        assert c.cout > 0.25
        assert "injouable" in c.motif

    def test_le_refus_est_chiffre(self):
        """« trop cher » ne se vérifie pas ; « meilleur H4 à 69 % » si."""
        c = choisir("X", _Spec(4000), plafond=0.25,
                    lecteur=_lecteur({"M15": 0.0020, "H1": 0.0050,
                                      "H4": 0.0100}))
        assert "%" in c.motif and "H4" in c.motif

    def test_retour_automatique_quand_la_volatilite_remonte(self):
        """Aucun calendrier : le choix se refait sur l'ATR courant.

        Dimanche soir, ou simplement au pic récurrent de 16-18h (×1.19 sur
        BTCUSD, mesuré), M15 repasse sous le plafond et redevient l'échelle
        retenue — sans qu'aucune heure soit codée nulle part.
        """
        spec = _Spec(200)
        creux = choisir("X", spec, plafond=0.25,
                        lecteur=_lecteur({"M15": 0.0020, "H1": 0.0300,
                                          "H4": 0.1000}))
        reprise = choisir("X", spec, plafond=0.25,
                          lecteur=_lecteur({"M15": 0.0200, "H1": 0.0600,
                                            "H4": 0.2000}))
        assert creux.timeframe == "H1"
        assert reprise.timeframe == "M15"
        assert reprise.elargie is False

    def test_atr_nul_n_est_pas_gratuit(self):
        """Un actif figé ne doit pas paraître bon marché par division."""
        c = choisir("X", _Spec(10), plafond=0.25,
                    lecteur=_lecteur({"M15": 0.0, "H1": 0.0, "H4": 0.0}))
        assert c is None

    def test_lecteur_qui_leve_ne_casse_pas(self):
        def casse(sym, tf, n):
            if tf == "M15":
                raise RuntimeError("indisponible")
            return _lecteur({"H1": 0.0300, "H4": 0.1})(sym, tf, n)
        c = choisir("X", _Spec(200), plafond=0.25, lecteur=casse)
        assert c.timeframe == "H1"

    def test_un_gap_entre_dans_l_atr(self):
        """La moyenne high-low ignorait les gaps, contrairement à RiskGate."""
        n = BARRES_ATR + 10
        idx = pd.date_range("2026-08-01", periods=n, freq="15min", tz="UTC")
        close = [1.0] * (n - 1) + [1.05]
        df = pd.DataFrame({
            "open": close,
            "high": [x + 0.001 for x in close],
            "low": [x - 0.001 for x in close],
            "close": close,
        }, index=idx)

        c = choisir("X", _Spec(100), plafond=0.25,
                    lecteur=lambda *_: df)
        assert c is not None
        assert c.atr > 0.002

    def test_ne_descend_jamais_sous_la_reference(self):
        """Partir de H1 ne doit pas rendre M15, même s'il serait moins cher :
        l'appelant a demandé H1 pour une raison."""
        c = choisir("X", _Spec(10), plafond=0.25, reference="H1",
                    lecteur=_lecteur({"M15": 0.5, "H1": 0.0300,
                                      "H4": 0.1000}))
        assert c.timeframe == "H1"


class TestEchelle:
    def test_ordre_croissant(self):
        """`choisir` parcourt la liste dans l'ordre : elle doit aller du
        plus fin au plus large, sinon il retiendrait H4 en premier."""
        assert ECHELLE == ("M15", "H1", "H4")

    def test_ne_va_pas_au_dela_de_h4(self):
        """Au-delà, une position immobiliserait un créneau plus d'une
        journée pour un compte qui n'en a que huit."""
        assert "D1" not in ECHELLE

    def test_fenetre_atr_commune(self):
        """Changer la fenêtre romprait la comparabilité avec le reste du
        moteur, qui raisonne partout sur 20 barres."""
        assert BARRES_ATR == 20


class TestIntegrationSizing:
    def test_le_budget_porte_l_unite_retenue(self, monkeypatch):
        """Sans ça, la boucle analyserait M15 pendant que le stop est
        calibré en H1 — deux mesures sans rapport."""
        from titanium import sizing
        from titanium.data.mt5_vendor import SymbolSpec
        from titanium.sizing import MAX_COUT_SPREAD_PCT

        spec = SymbolSpec(name="X", digits=5, point=0.00001,
                          volume_min=0.01, volume_max=100.0,
                          volume_step=0.01, trade_contract_size=100_000.0,
                          spread=200, tick_value=1.0, tick_size=0.00001)
        monkeypatch.setattr("titanium.data.mt5_vendor.ensure_symbol",
                            lambda s: spec)
        monkeypatch.setattr("titanium.data.mt5_vendor.get_rates",
                            _lecteur({"M15": 0.0020, "H1": 0.0300,
                                      "H4": 0.1000}))
        b = sizing.tradable_universe(["X"], 5000.0)["X"]
        assert b.tradable
        assert b.timeframe == "H1"
        assert 0 < b.cout_spread <= MAX_COUT_SPREAD_PCT
