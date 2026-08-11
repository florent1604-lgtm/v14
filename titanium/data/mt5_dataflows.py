"""Vendeur MT5 branché sur le registre de V13 — MT5 prioritaire, yfinance en secours.

C'est ce module qui rend MT5 visible **par les agents**. Le module frère
`mt5_vendor` parle à MetaTrader ; celui-ci traduit ses sorties au contrat que
`tradingagents.dataflows.interface` attend d'un vendeur : mêmes signatures,
mêmes chaînes de caractères, mêmes erreurs typées.

POURQUOI CE MODULE EXISTE
-------------------------
Sans lui, `mt5_vendor` ne servait qu'à l'exécuteur : les agents lisaient
yfinance et rien d'autre. Le choix « MT5 prioritaire » restait lettre morte.

LA BASCULE DE SECOURS EST GRATUITE
----------------------------------
Le routeur de V13 (`_route`) attrape `VendorNotConfiguredError` et
`NoMarketDataError` et passe au vendeur suivant de la chaîne. Il suffit donc de
lever le bon type :

* terminal fermé / module absent  → ``Mt5NotAvailableError`` (un
  `VendorNotConfiguredError`) → yfinance prend la main ;
* symbole absent du courtier (AAPL, une action)  → ``NoMarketDataError`` →
  yfinance prend la main.

Aucun `try/except` supplémentaire côté appelant. C'est exactement ce que la
taxonomie de V13 promet, et c'est pour ça qu'on s'y branche au lieu d'écrire
notre propre routage.

CE QUE MT5 NE FOURNIRA JAMAIS
-----------------------------
Fondamentaux, bilans, news, sentiment : un courtier CFD n'en a pas. Ces
catégories restent sur yfinance — ce n'est pas un manque, c'est la répartition
correcte.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.y_finance import INDICATOR_DESCRIPTIONS
from titanium.data.mt5_vendor import get_rates_range, list_symbols

# Suffixes que les courtiers ajoutent à un même sous-jacent (Axi : NAS100.fs,
# HSI.fs ; d'autres : EURUSD.pro, XAUUSD+). Essayés dans l'ordre.
_BROKER_SUFFIXES = ("", ".fs", ".s", ".pro", "+", "m", ".cash", ".spot")

_symbol_cache: dict[str, str] = {}
_catalogue: list[str] | None = None


def _known_symbols() -> list[str]:
    """Catalogue du courtier, lu une fois par processus.

    Itérer ~150 symboles à chaque requête monopoliserait le verrou MT5 — la
    leçon d'affamement du verrou héritée de V12.
    """
    global _catalogue
    if _catalogue is None:
        _catalogue = list_symbols()
    return _catalogue


def reset_symbol_cache() -> None:
    """Vide les caches (tests, ou changement de courtier)."""
    global _catalogue
    _catalogue = None
    _symbol_cache.clear()


def resolve_mt5_symbol(symbol: str) -> str:
    """Traduit un ticker d'agent en symbole du courtier.

    ``BTC-USD`` → ``BTCUSD``, ``NAS100`` → ``NAS100.fs``, ``XAUUSD+`` →
    ``XAUUSD``. Un ticker d'action (``AAPL``) n'existe pas chez un courtier CFD.

    Raises:
        NoMarketDataError: symbole introuvable — le routeur bascule sur yfinance.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise NoMarketDataError(str(symbol), detail="symbole vide")

    brut = symbol.strip().upper()
    if brut in _symbol_cache:
        return _symbol_cache[brut]

    connus = _known_symbols()
    index = {s.upper(): s for s in connus}

    # Candidats par ordre de préférence : tel quel, sans tiret, sans suffixe courtier.
    bases = [brut, brut.replace("-", ""), brut.rstrip("+").replace("-", "")]
    for base in dict.fromkeys(bases):          # dédoublonne en gardant l'ordre
        for suffixe in _BROKER_SUFFIXES:
            candidat = f"{base}{suffixe}".upper()
            if candidat in index:
                resolu = index[candidat]
                _symbol_cache[brut] = resolu
                return resolu

    raise NoMarketDataError(
        symbol,
        detail=f"absent du catalogue courtier ({len(connus)} symboles) — "
               "probablement une action ou un indice non couvert",
    )


def _fetch_daily(symbol: str, start_date: str, end_date: str):
    """Bougies journalières entre deux dates, colonnes au format V13."""
    debut = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # MT5 exclut la borne haute : on ajoute un jour pour inclure end_date.
    fin = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    if fin <= debut:
        raise ValueError(f"plage vide : {start_date} → {end_date}")

    courtier = resolve_mt5_symbol(symbol)
    df = get_rates_range(courtier, "D1", debut, fin)

    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "tick_volume": "Volume",
    })
    colonnes = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[colonnes].copy()
    df.index = df.index.tz_convert("UTC").normalize().tz_localize(None)
    df.index.name = "Date"
    return df, courtier


def get_mt5_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Cours journaliers MT5, au format texte attendu par les agents.

    Même forme que ``get_YFin_data_online`` : en-tête commenté puis CSV, pour
    que l'analyste lise la même chose quelle que soit la source.
    """
    df, courtier = _fetch_daily(symbol, start_date, end_date)

    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col].round(5)

    etiquette = courtier if courtier.upper() == symbol.upper() else f"{courtier} (from {symbol})"
    entete = (
        f"# Stock data for {etiquette} from {start_date} to {end_date}\n"
        f"# Source: MetaTrader 5 (broker feed, D1 bars)\n"
        f"# Total records: {len(df)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return entete + df.to_csv()


def get_mt5_indicators(symbol: str, indicator: str, curr_date: str,
                       look_back_days: int) -> str:
    """Indicateur technique calculé sur les bougies MT5.

    Utilise ``stockstats``, comme le vendeur yfinance, et réutilise les mêmes
    descriptions (`INDICATOR_DESCRIPTIONS`) : une seule prose pour l'agent,
    quelle que soit la source des chiffres.
    """
    if indicator not in INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator} is not supported. "
            f"Please choose from: {list(INDICATOR_DESCRIPTIONS)}"
        )

    fin = datetime.strptime(curr_date, "%Y-%m-%d")
    debut_fenetre = fin - timedelta(days=int(look_back_days))
    # Marge d'amorçage : une 200 SMA a besoin de ~200 séances AVANT la fenêtre.
    debut_chargement = debut_fenetre - timedelta(days=400)

    df, _ = _fetch_daily(symbol, debut_chargement.strftime("%Y-%m-%d"), curr_date)

    from stockstats import wrap

    stock = wrap(df.copy())
    stock[indicator]  # déclenche le calcul
    serie = stock[indicator]

    lignes = ""
    for horodate in sorted(serie.index, reverse=True):
        jour = horodate.strftime("%Y-%m-%d")
        if not (debut_fenetre.strftime("%Y-%m-%d") <= jour <= curr_date):
            continue
        valeur = serie.loc[horodate]
        lignes += f"{jour}: {valeur}\n"

    if not lignes:
        raise NoMarketDataError(
            symbol, detail=f"aucune valeur {indicator} sur la fenêtre demandée"
        )

    return (
        f"## {indicator} values from {debut_fenetre.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + lignes
        + "\n\n"
        + INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    )


# ═══════════════════ courtier → ticker public ════════════════════════════

#: Indices et matières : le nom du courtier n'existe nulle part ailleurs.
_PUBLICS = {
    "US500": "^GSPC", "SPX500": "^GSPC", "US30": "^DJI",
    "USTECH": "^NDX", "NAS100": "^NDX", "US100": "^NDX",
    "GER40": "^GDAXI", "GER30": "^GDAXI", "UK100": "^FTSE",
    "FRA40": "^FCHI", "CAC40": "^FCHI", "EU50": "^STOXX50E",
    "JPN225": "^N225", "NK225": "^N225", "AUS200": "^AXJO",
    "HK50": "^HSI", "CN50": "000300.SS", "IT40": "FTSEMIB.MI",
    "USOIL": "CL=F", "WTI": "CL=F", "BRENT": "BZ=F", "UKOIL": "BZ=F",
    "XAUUSD": "GC=F", "XAGUSD": "SI=F", "XPTUSD": "PL=F",
    "COFFEE": "KC=F", "COCOA": "CC=F", "SUGAR": "SB=F",
    "VIX": "^VIX", "USDINDEX": "DX-Y.NYB",
}

#: Bases crypto reconnues par les fournisseurs publics.
_CRYPTO = {"BTC", "ETH", "LTC", "XRP", "ADA", "DOG", "DOGE", "BCH", "SOL",
           "DOT", "AVAX", "LINK", "UNI", "AAVE", "BNB", "MATIC", "ATOM"}

#: Devises, pour reconnaître une paire de change.
_DEVISES = {"EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SGD",
            "NOK", "SEK", "DKK", "HKD", "MXN", "ZAR", "TRY", "PLN", "CZK",
            "HUF", "CNH", "KRW", "THB", "INR"}


def ticker_public(symbole: str) -> str:
    """Nom du courtier → ticker qu'un fournisseur public reconnaît.

    ⚠️ Sans cette traduction, les analystes reçoivent `NK225.fs`, `EURSGD` ou
    `COFFEE.fs` et interrogent Yahoo Finance, qui répond **404**. Ils rendent
    alors un avis sans donnée — donc « sans note ». Toute la couche
    d'analyse tournait ainsi à vide. Constaté le 07/08/2026.

    Rend le symbole nettoyé quand aucune correspondance n'existe : mieux
    vaut une tentative qu'un refus, la chaîne de vendeurs sait déjà gérer
    un ticker inconnu.
    """
    s = str(symbole or "").strip().upper()
    if not s:
        return ""
    # Suffixes de courtier : `.fs`, `.cash`, `+`, `#`, `.r`…
    for suf in (".FS", ".CASH", ".SPOT", ".R", ".PRO", ".ECN"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    s = s.rstrip("+#._")

    if s in _PUBLICS:
        return _PUBLICS[s]

    # Crypto : BTCUSD → BTC-USD, BCH-JPY reste tel quel.
    if "-" in s:
        return s
    for base in sorted(_CRYPTO, key=len, reverse=True):
        if s.startswith(base) and len(s) > len(base):
            return f"{base}-{s[len(base):]}"

    # Change : six lettres, deux devises connues → format Yahoo.
    if len(s) == 6 and s[:3] in _DEVISES and s[3:] in _DEVISES:
        return f"{s}=X"

    return s
