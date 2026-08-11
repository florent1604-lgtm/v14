"""Tests du dimensionnement par actif.

Ce module décide combien risquer, actif par actif. Une erreur ici coûte de
l'argent directement — pas une opportunité manquée, de l'argent. Les tests
visent donc d'abord ce qui doit être **refusé**.

Le cas central, mesuré sur la démo à 67 USD : le lot minimum de XAUUSD coûte
17.32 USD, soit 25.8 % de l'equity. Un système qui l'accepte en croyant risquer
1 % est le piège du micro-capital de V12.
"""

from __future__ import annotations

import pytest

from titanium.data.mt5_vendor import SymbolSpec
from titanium.sizing import (
    MAX_RISK_PCT,
    TARGET_RISK_PCT,
    budget_for,
    loss_per_lot,
)


def spec(**over) -> SymbolSpec:
    """EURUSD : lot min 0.01, pas 0.01, tick 1e-5 valant 1 USD."""
    base = dict(name="EURUSD", digits=5, point=1e-5, volume_min=0.01,
                volume_max=100.0, volume_step=0.01, trade_contract_size=100_000.0,
                spread=12, tick_value=1.0, tick_size=1e-5)
    base.update(over)
    return SymbolSpec(**base)


# Stop de 84 pips → perte de 84 USD par lot. Sur 1 000 USD à 1 %, lot = 0.11.
STOP = 0.00084


# ═══════════════════ ce qui doit être REFUSÉ ════════════════════════════════

def test_actif_trop_gros_est_refuse():
    """XAUUSD sur un petit compte : lot minimum à 25.8 % de l'equity."""
    xau = spec(name="XAUUSD", volume_min=0.01, volume_step=0.01,
               tick_value=1.0, tick_size=0.01)
    b = budget_for(xau, 17.32, equity=67.0)
    assert not b.tradable
    assert "hors de portée" in b.reason
    assert b.effective_pct > MAX_RISK_PCT
    assert b.lot == 0.0, "aucun lot ne doit être proposé sur un actif refusé"


def test_le_refus_nomme_le_chiffre():
    """Un refus muet ne sert à rien : il doit dire combien ça coûterait."""
    b = budget_for(spec(volume_min=1.0), STOP, equity=67.0)
    assert not b.tradable
    assert "%" in b.reason and "plafond" in b.reason


def test_juste_au_dessus_du_plafond_refuse():
    """Le plafond est dur : 2.01 % passe déjà à la trappe."""
    s = spec(volume_min=0.01, tick_value=1.0, tick_size=1e-5)
    # lot min coûte 2.01 sur 100 d'equity → 2.01 %
    b = budget_for(s, 0.00201, equity=100.0)
    assert not b.tradable


def test_juste_sous_le_plafond_accepte():
    s = spec(volume_min=0.01, tick_value=1.0, tick_size=1e-5)
    b = budget_for(s, 0.00199, equity=100.0)
    assert b.tradable
    assert b.at_min_lot


@pytest.mark.parametrize("equity", [0, -100, float("nan"), float("inf")])
def test_equity_invalide_refuse(equity):
    b = budget_for(spec(), STOP, equity=equity)
    assert not b.tradable
    assert b.reason == "EQUITY_INVALIDE"


@pytest.mark.parametrize("stop", [0, -1, float("nan"), float("inf")])
def test_stop_invalide_refuse(stop):
    assert not budget_for(spec(), stop, equity=1000.0).tradable


@pytest.mark.parametrize("champ", ["tick_value", "tick_size"])
def test_specs_incompletes_refusent(champ):
    b = budget_for(spec(**{champ: 0.0}), STOP, equity=1000.0)
    assert not b.tradable
    assert "SPECS_INCOMPLETES" in b.reason


def test_volume_min_nul_refuse():
    b = budget_for(spec(volume_min=0.0), STOP, equity=1000.0)
    assert not b.tradable


def test_plafond_sous_la_cible_est_incoherent():
    b = budget_for(spec(), STOP, equity=1000.0, target_pct=3.0, max_pct=2.0)
    assert not b.tradable
    assert b.reason == "PARAMETRES_INCOHERENTS"


def test_ne_leve_jamais():
    """Un objet spec cassé ne doit pas propager d'exception vers le moteur."""
    class Cassee:
        def __getattr__(self, n):
            raise RuntimeError("spec corrompue")
    with pytest.raises(RuntimeError):
        budget_for(Cassee(), STOP, equity=1000.0)  # documenté : l'appelant filtre


# ═══════════════════ dimensionnement nominal ════════════════════════════════

def test_compte_confortable_dimensionne_dans_la_cible():
    """1 000 USD à 1 % = 10 USD ; perte/lot 84 → lot 0.11, risque 9.24."""
    b = budget_for(spec(), STOP, equity=1000.0)
    assert b.tradable
    assert not b.at_min_lot
    assert b.lot == pytest.approx(0.11)
    assert b.risk_money == pytest.approx(9.24, abs=0.01)
    assert b.effective_pct < TARGET_RISK_PCT


def test_arrondi_vers_le_bas():
    """Un arrondi supérieur dépasserait le risque visé : interdit."""
    b = budget_for(spec(), STOP, equity=1000.0)
    assert b.risk_money <= b.target_money


def test_petit_compte_entre_au_lot_minimum_et_le_dit():
    """67 USD à 1 % = 0.67 visé, mais le lot minimum coûte 0.84."""
    b = budget_for(spec(), STOP, equity=67.0)
    assert b.tradable
    assert b.at_min_lot
    assert b.lot == 0.01
    assert b.risk_money == pytest.approx(0.84, abs=0.01)
    assert b.overshoot > 1.0, "le dépassement doit être visible, pas subi"
    assert "au lieu de" in b.reason


def test_le_depassement_est_mesurable():
    b = budget_for(spec(), STOP, equity=67.0)
    assert b.overshoot == pytest.approx(b.risk_money / b.target_money)


def test_lot_plafonne_au_maximum_du_courtier():
    b = budget_for(spec(volume_max=0.05), STOP, equity=100_000.0)
    assert b.lot == 0.05


def test_pas_de_volume_grossier_respecte():
    """Un pas de 0.1 (indices) doit être respecté, pas arrondi à 0.01."""
    s = spec(volume_min=0.1, volume_step=0.1)
    b = budget_for(s, STOP, equity=10_000.0)
    assert b.tradable
    assert round(b.lot / 0.1, 6) == round(b.lot / 0.1)


# ═══════════════════ l'univers suit le compte ═══════════════════════════════

def test_grandir_ouvre_des_actifs():
    """C'est la propriété qui remplace toute liste d'actifs codée en dur :
    un actif refusé à 67 USD devient traçable à 1 500 USD, sans configuration.

    Valeurs relevées sur le vrai GER40 (Axi) : lot min 0.1, stop 1.5×ATR ≈ 54.63,
    lot minimum coûtant 6.29 USD ⇒ perte/lot ≈ 62.9.
    """
    indice = spec(name="GER40", volume_min=0.1, volume_step=0.1,
                  tick_value=0.01152, tick_size=0.01)
    stop = 54.63
    assert budget_for(indice, stop, equity=67.0).effective_pct > MAX_RISK_PCT
    assert not budget_for(indice, stop, equity=67.0).tradable

    grand = budget_for(indice, stop, equity=1500.0)
    assert grand.tradable
    assert not grand.at_min_lot, "à 1 500 USD, le lot doit tenir dans la cible"


def test_actif_fin_reste_tradable_sur_petit_compte():
    b = budget_for(spec(name="EURGBP", tick_value=0.62), 0.00084, equity=67.0)
    assert b.tradable


# ═══════════════════ perte par lot ══════════════════════════════════════════

def test_perte_par_lot():
    assert loss_per_lot(spec(), 0.00084) == pytest.approx(84.0)


def test_perte_par_lot_utilise_le_tick_pas_le_contrat():
    """Déduire la valeur du seul `trade_contract_size` est faux dès que la
    devise de cotation diffère de celle du compte."""
    a = loss_per_lot(spec(tick_value=1.0), 0.001)
    b = loss_per_lot(spec(tick_value=0.62, trade_contract_size=100_000.0), 0.001)
    assert a != b


@pytest.mark.parametrize("mauvais", [0, -1, float("nan")])
def test_perte_par_lot_sur_stop_invalide(mauvais):
    assert loss_per_lot(spec(), mauvais) == 0.0


class TestPorteDeCout:
    """Un actif dont le spread mange le risque est INJOUABLE, pas « petit ».

    Défaut du 07/08/2026 : l'univers ouvert aux 149 symboles du catalogue
    sans ce contrôle. 112 avaient un spread au-dessus de 25 % de la
    distance de stop. Une journée : 16 perdants, 2 gagnants, −318 EUR,
    17 sorties sur 18 au stop.
    """

    def _monter(self, monkeypatch, spread, amplitude=0.0020):
        """Un actif dont chaque barre fait `amplitude`, à toute échelle.

        L'amplitude est identique sur M15, H1 et H4 : l'échelle adaptative
        ne peut donc rien sauver, et seul le spread décide.
        """
        from datetime import datetime, timedelta, timezone

        import pandas as pd

        from titanium import sizing
        from titanium.data.mt5_vendor import SymbolSpec

        spec = SymbolSpec(name="X", digits=5, point=0.00001,
                          volume_min=0.01, volume_max=100.0, volume_step=0.01,
                          trade_contract_size=100_000.0, spread=spread,
                          tick_value=1.0, tick_size=0.00001)

        def rates(sym, tf, n):
            idx = pd.date_range(datetime.now(timezone.utc) - timedelta(hours=n),
                                periods=n, freq="15min", tz="UTC")
            return pd.DataFrame({
                "open": [1.0] * n, "close": [1.0] * n,
                "high": [1.0 + amplitude / 2] * n,
                "low": [1.0 - amplitude / 2] * n}, index=idx)

        monkeypatch.setattr("titanium.data.mt5_vendor.ensure_symbol",
                            lambda s: spec)
        monkeypatch.setattr("titanium.data.mt5_vendor.get_rates", rates)
        monkeypatch.setattr("titanium.features.smc.compute_atr",
                            lambda d: amplitude)
        return sizing

    def test_le_plafond_est_sous_un_tiers(self):
        """La correction d'unité ne doit pas élargir l'univers en silence.

        L'ancienne formule doublait le spread : 25 % affichés équivalaient à
        12,5 % réels. On conserve ce comportement jusqu'à calibration.
        """
        from titanium.sizing import MAX_COUT_SPREAD_PCT
        assert MAX_COUT_SPREAD_PCT == pytest.approx(0.125)

    def test_actif_bon_marche_accepte(self, monkeypatch):
        # 10 points de spread = 0.0001 sur un stop de 0.0030 : 3,3 %.
        s = self._monter(monkeypatch, spread=10)
        b = s.tradable_universe(["X"], 5000.0)["X"]
        assert b.tradable, b.reason
        assert b.cout_spread < 0.25

    def test_spread_prohibitif_refuse(self, monkeypatch):
        # 400 points sur un stop de 0.0030 : 133 % du risque, à toute échelle.
        s = self._monter(monkeypatch, spread=400)
        b = s.tradable_universe(["X"], 5000.0)["X"]
        assert not b.tradable
        assert "%" in b.reason

    def test_le_motif_chiffre_le_refus(self, monkeypatch):
        """« trop cher » n'aide personne ; « 267 % » se vérifie."""
        s = self._monter(monkeypatch, spread=400)
        assert "%" in s.tradable_universe(["X"], 5000.0)["X"].reason


class TestPorteDeVitalite:
    """Un instrument qui ne cote plus n'est pas « peu volatil », il est mort.

    USDCHC n'avait pas coté depuis le 27/03/2026. Son ATR figé à 0.000002
    faisait qu'un spread parfaitement normal de 12 points ressortait à
    12 000 % du risque. Le rejet était juste, le motif faux — et un mort
    dont le spread se resserrerait aurait franchi la porte.
    """

    def _monter(self, monkeypatch, heures):
        from datetime import datetime, timedelta, timezone

        import pandas as pd

        from titanium import sizing
        from titanium.data.mt5_vendor import SymbolSpec

        idx = pd.DatetimeIndex(
            [datetime.now(timezone.utc) - timedelta(hours=heures)])
        df = pd.DataFrame({"open": [1.0], "high": [1.001],
                           "low": [0.999], "close": [1.0]}, index=idx)
        spec = SymbolSpec(name="X", digits=5, point=0.00001,
                          volume_min=0.01, volume_max=100.0, volume_step=0.01,
                          trade_contract_size=100_000.0, spread=8,
                          tick_value=1.0, tick_size=0.00001)
        monkeypatch.setattr("titanium.data.mt5_vendor.ensure_symbol",
                            lambda s: spec)
        monkeypatch.setattr("titanium.data.mt5_vendor.get_rates",
                            lambda *a, **k: df)
        monkeypatch.setattr("titanium.features.smc.compute_atr",
                            lambda d: 0.0020)
        return sizing

    def test_instrument_vivant_accepte(self, monkeypatch):
        s = self._monter(monkeypatch, heures=1)
        assert s.tradable_universe(["X"], 5000.0)["X"].tradable

    def test_instrument_eteint_refuse(self, monkeypatch):
        s = self._monter(monkeypatch, heures=24 * 40)
        b = s.tradable_universe(["X"], 5000.0)["X"]
        assert not b.tradable
        assert "éteint" in b.reason

    def test_le_motif_donne_l_anciennete(self, monkeypatch):
        """« mort » n'aide pas ; « il y a 40 jours » se vérifie."""
        s = self._monter(monkeypatch, heures=24 * 40)
        assert "jour" in s.tradable_universe(["X"], 5000.0)["X"].reason

    def test_weekend_ne_declenche_pas_le_refus(self, monkeypatch):
        """Le forex ferme deux jours : le seuil doit les absorber."""
        from titanium.sizing import MAX_AGE_BARRE_H
        assert MAX_AGE_BARRE_H >= 60
        s = self._monter(monkeypatch, heures=50)
        assert s.tradable_universe(["X"], 5000.0)["X"].tradable


class TestMarcheOuvert:
    """Un actif sain mais FERMÉ ne doit pas être tradé.

    Distinct de « instrument éteint » : le forex ferme 48 h le week-end,
    bien en deçà du seuil de 72 h. Entrer dessus se ferait sur un prix figé,
    et le stop ne pourrait pas être géré avant la réouverture.
    """

    def _mt5(self, ticks, monkeypatch):
        from contextlib import contextmanager

        from titanium import sizing

        @contextmanager
        def faux():
            yield None
        monkeypatch.setattr("titanium.data.mt5_vendor.mt5_session", faux)
        mod = type("M", (), {"symbol_info_tick": staticmethod(
            lambda s: type("T", (), {"time": ticks.get(s, 0)})())})()
        monkeypatch.setitem(__import__("sys").modules, "MetaTrader5", mod)
        return sizing

    def test_tout_frais_tout_ouvert(self, monkeypatch):
        s = self._mt5({"A": 1000, "B": 1000}, monkeypatch)
        assert s.marches_ouverts(["A", "B"]) == {"A": True, "B": True}

    def test_le_retardataire_est_ferme(self, monkeypatch):
        # B a 2 h de retard sur A : marché fermé.
        s = self._mt5({"A": 100_000, "B": 100_000 - 7200}, monkeypatch)
        r = s.marches_ouverts(["A", "B"])
        assert r["A"] is True and r["B"] is False

    def test_comparaison_relative_pas_horloge_locale(self, monkeypatch):
        """MT5 rend les ticks en heure SERVEUR (Axi GMT+3). Comparer à
        `datetime.now()` donnerait des âges négatifs de trois heures."""
        # Horodatages « dans le futur » selon l'horloge locale : tous ouverts.
        futur = 4_000_000_000
        s = self._mt5({"A": futur, "B": futur}, monkeypatch)
        assert all(s.marches_ouverts(["A", "B"]).values())

    def test_sans_mesure_on_n_affirme_rien(self, monkeypatch):
        """Une panne de lecture ne doit pas fermer tout le marché."""
        from contextlib import contextmanager

        from titanium import sizing

        @contextmanager
        def casse():
            raise RuntimeError("MT5 injoignable")
            yield
        monkeypatch.setattr("titanium.data.mt5_vendor.mt5_session", casse)
        assert all(sizing.marches_ouverts(["A", "B"]).values())

    def test_seuil_absorbe_une_pause_courte(self):
        """Un actif peu liquide peut rester 10 min sans tick sans être fermé."""
        from titanium.sizing import RETARD_MAX_MIN
        assert 10 <= RETARD_MAX_MIN <= 60
