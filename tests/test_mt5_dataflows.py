"""Tests du vendeur MT5 branché sur le registre de V13.

L'enjeu n'est pas « MT5 sait lire des bougies » — c'est déjà couvert par
`test_mt5_vendor.py`. C'est : **MT5 est-il correctement enfiché dans le routage
de V13, et bascule-t-il sur yfinance quand il ne peut pas servir ?**

Le contrat qu'on vérifie :
  · MT5 est le PREMIER vendeur de la chaîne pour cours et indicateurs ;
  · un symbole absent du courtier lève ``NoMarketDataError`` — le type exact
    que `_route` attrape pour passer au vendeur suivant ;
  · terminal fermé → ``Mt5NotAvailableError`` (un `VendorNotConfiguredError`),
    même effet ;
  · fondamentaux et news ne sont PAS servis par MT5 (un courtier CFD n'en a pas).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.dataflows.interface import VENDOR_METHODS
from titanium.data import mt5_dataflows as md

CATALOGUE = ["EURUSD", "XAUUSD", "US500", "GER40", "NAS100.fs", "HSI.fs", "BTCUSD"]


@pytest.fixture(autouse=True)
def _cache_propre():
    md.reset_symbol_cache()
    yield
    md.reset_symbol_cache()


@pytest.fixture
def catalogue(monkeypatch):
    """Remplace le catalogue courtier — aucun appel MT5."""
    def _install(symboles=None):
        monkeypatch.setattr(md, "list_symbols", lambda: list(symboles or CATALOGUE))
        md.reset_symbol_cache()
    return _install


def bougies(n: int, debut="2026-07-01") -> pd.DataFrame:
    """n bougies journalières, index UTC, colonnes brutes MT5."""
    idx = pd.date_range(debut, periods=n, freq="D", tz="UTC")
    return pd.DataFrame({
        "open": [1.10 + i * 0.001 for i in range(n)],
        "high": [1.11 + i * 0.001 for i in range(n)],
        "low": [1.09 + i * 0.001 for i in range(n)],
        "close": [1.105 + i * 0.001 for i in range(n)],
        "tick_volume": [1000 + i for i in range(n)],
        "spread": [2] * n,
        "real_volume": [0] * n,
    }, index=idx)


# ═══════════════════ enregistrement dans le registre V13 ════════════════════

def test_mt5_est_le_premier_vendeur_des_cours():
    vendeurs = list(VENDOR_METHODS["get_stock_data"])
    assert vendeurs[0] == "mt5", f"MT5 doit être prioritaire, chaîne = {vendeurs}"
    assert "yfinance" in vendeurs, "yfinance doit rester en secours"


def test_mt5_est_le_premier_vendeur_des_indicateurs():
    vendeurs = list(VENDOR_METHODS["get_indicators"])
    assert vendeurs[0] == "mt5"
    assert "yfinance" in vendeurs


@pytest.mark.parametrize("methode", [
    "get_fundamentals", "get_balance_sheet", "get_cashflow",
    "get_income_statement", "get_news",
])
def test_mt5_ne_sert_pas_ce_quun_courtier_na_pas(methode):
    """Un courtier CFD n'a ni bilan ni news. L'y inscrire produirait une
    bascule inutile à chaque requête."""
    assert "mt5" not in VENDOR_METHODS[methode]


def test_la_config_par_defaut_met_mt5_en_tete():
    from tradingagents.default_config import DEFAULT_CONFIG
    v = DEFAULT_CONFIG["data_vendors"]
    assert v["core_stock_apis"] == "mt5,yfinance"
    assert v["technical_indicators"] == "mt5,yfinance"
    assert v["fundamental_data"] == "yfinance"


# ═════════════════════════ résolution de symbole ════════════════════════════

def test_symbole_exact(catalogue):
    catalogue()
    assert md.resolve_mt5_symbol("EURUSD") == "EURUSD"


def test_casse_ignoree(catalogue):
    catalogue()
    assert md.resolve_mt5_symbol("eurusd") == "EURUSD"


def test_suffixe_courtier_trouve(catalogue):
    """NAS100 chez Axi s'appelle NAS100.fs — l'agent ne le sait pas."""
    catalogue()
    assert md.resolve_mt5_symbol("NAS100") == "NAS100.fs"
    assert md.resolve_mt5_symbol("HSI") == "HSI.fs"


def test_tiret_crypto_retire(catalogue):
    """Les agents écrivent BTC-USD (convention Yahoo), le courtier BTCUSD."""
    catalogue()
    assert md.resolve_mt5_symbol("BTC-USD") == "BTCUSD"


def test_marqueur_cfd_retire(catalogue):
    catalogue()
    assert md.resolve_mt5_symbol("XAUUSD+") == "XAUUSD"


def test_action_absente_leve_no_market_data(catalogue):
    """LE test qui compte : c'est ce type d'erreur qui déclenche yfinance."""
    catalogue()
    with pytest.raises(NoMarketDataError) as e:
        md.resolve_mt5_symbol("AAPL")
    assert e.value.symbol == "AAPL"
    assert "catalogue courtier" in e.value.detail


def test_symbole_vide_leve(catalogue):
    catalogue()
    for mauvais in ("", "   ", None):
        with pytest.raises(NoMarketDataError):
            md.resolve_mt5_symbol(mauvais)


def test_catalogue_lu_une_seule_fois(monkeypatch):
    """Itérer ~150 symboles à chaque requête affamerait le verrou MT5."""
    appels = []
    monkeypatch.setattr(md, "list_symbols", lambda: (appels.append(1), CATALOGUE)[1])
    md.reset_symbol_cache()
    md.resolve_mt5_symbol("EURUSD")
    md.resolve_mt5_symbol("XAUUSD")
    md.resolve_mt5_symbol("US500")
    assert len(appels) == 1


# ═════════════════════════════ cours (D1) ═══════════════════════════════════

@pytest.fixture
def rates(monkeypatch):
    def _install(df=None, erreur=None):
        def faux(symbole, tf, debut, fin):
            if erreur:
                raise erreur
            return df if df is not None else bougies(10)
        monkeypatch.setattr(md, "get_rates_range", faux)
    return _install


def test_cours_format_v13(catalogue, rates):
    """L'agent doit lire la même forme que pour yfinance : en-tête # puis CSV."""
    catalogue(); rates(bougies(5))
    out = md.get_mt5_stock_data("EURUSD", "2026-07-01", "2026-07-05")
    lignes = out.splitlines()
    assert lignes[0].startswith("# Stock data for EURUSD")
    assert "# Source: MetaTrader 5" in out
    assert "# Total records: 5" in out
    assert "Date,Open,High,Low,Close,Volume" in out


def test_cours_mentionne_la_resolution(catalogue, rates):
    """Quand le nom courtier diffère, l'agent doit voir lequel a été coté."""
    catalogue(); rates(bougies(3))
    out = md.get_mt5_stock_data("NAS100", "2026-07-01", "2026-07-03")
    assert "NAS100.fs (from NAS100)" in out


def test_cours_index_sans_heure(catalogue, rates):
    """Des bougies D1 doivent produire des dates, pas des horodatages UTC."""
    catalogue(); rates(bougies(3))
    out = md.get_mt5_stock_data("EURUSD", "2026-07-01", "2026-07-03")
    assert "2026-07-01," in out
    assert "00:00:00" not in out


def test_plage_inversee_rejetee(catalogue, rates):
    catalogue(); rates()
    with pytest.raises(ValueError, match="plage vide"):
        md.get_mt5_stock_data("EURUSD", "2026-07-10", "2026-07-01")


def test_absence_de_donnees_remonte_telle_quelle(catalogue, rates):
    """L'erreur ne doit pas être avalée : le routeur en a besoin pour basculer."""
    catalogue(); rates(erreur=NoMarketDataError("EURUSD", detail="marché fermé"))
    with pytest.raises(NoMarketDataError):
        md.get_mt5_stock_data("EURUSD", "2026-07-01", "2026-07-05")


def test_terminal_ferme_remonte_un_vendeur_non_configure(catalogue, rates):
    from titanium.data.mt5_vendor import Mt5NotAvailableError
    catalogue(); rates(erreur=Mt5NotAvailableError("terminal fermé"))
    with pytest.raises(VendorNotConfiguredError):
        md.get_mt5_stock_data("EURUSD", "2026-07-01", "2026-07-05")


# ═══════════════════════════ indicateurs ════════════════════════════════════

def test_indicateur_inconnu_rejete(catalogue, rates):
    catalogue(); rates()
    with pytest.raises(ValueError, match="not supported"):
        md.get_mt5_indicators("EURUSD", "super_moyenne", "2026-07-10", 5)


def test_indicateur_format_v13(catalogue, rates):
    catalogue(); rates(bougies(300, debut="2025-09-01"))
    out = md.get_mt5_indicators("EURUSD", "close_50_sma", "2026-06-25", 5)
    assert out.startswith("## close_50_sma values from")
    assert "2026-06-25:" in out
    # La description vient de la constante partagée avec yfinance.
    assert "50 SMA" in out


def test_description_partagee_avec_yfinance():
    """Une seule prose pour l'agent, quelle que soit la source des chiffres."""
    from tradingagents.dataflows.y_finance import INDICATOR_DESCRIPTIONS
    assert "close_50_sma" in INDICATOR_DESCRIPTIONS
    assert "rsi" in INDICATOR_DESCRIPTIONS


def test_amorcage_charge_plus_que_la_fenetre(catalogue, monkeypatch):
    """Une SMA 200 a besoin de ~200 séances AVANT la fenêtre demandée, sinon
    elle sort des NaN. On vérifie que la plage chargée déborde largement."""
    catalogue()
    vues = {}

    def faux(symbole, tf, debut, fin):
        vues["debut"], vues["fin"] = debut, fin
        return bougies(400, debut="2025-06-01")

    monkeypatch.setattr(md, "get_rates_range", faux)
    md.get_mt5_indicators("EURUSD", "close_200_sma", "2026-07-10", 5)
    demande = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert (demande - vues["debut"]).days > 300


def test_fenetre_vide_leve(catalogue, rates):
    """Des bougies hors de la fenêtre demandée = pas de valeur à rendre."""
    catalogue(); rates(bougies(60, debut="2024-01-01"))
    with pytest.raises(NoMarketDataError, match="aucune valeur"):
        md.get_mt5_indicators("EURUSD", "close_50_sma", "2026-07-10", 5)
