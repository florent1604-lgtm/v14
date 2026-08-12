"""Vendeur de données MetaTrader 5 — LECTURE SEULE, sans exception.

Ce module ne contient **aucun** appel de passage d'ordre. `order_send`,
`order_check` et `positions_*` n'y apparaissent pas et ne doivent jamais y
apparaître : l'exécution vit dans `titanium/execution/`, derrière le mur
démo↔réel. Séparer les deux est ce qui permet de lire les données d'un compte
réel sans risque de déclencher quoi que ce soit.

LEÇON DE V12, REPORTÉE ICI TELLE QUELLE
---------------------------------------
**MT5 n'est pas thread-safe.** V12 sérialise *tous* les appels derrière un
`mt5_lock` (RLock) et le documente comme CRITIQUE. Chaque fonction publique
d'ici prend ce verrou. Un appel MT5 hors verrou est un bug, pas un raccourci.

Corollaire mesuré dans V12 : un balayage complet de ~141 actifs prend plusieurs
minutes sur cette machine et **affame** les autres consommateurs du verrou. Les
fonctions de balayage doivent rester hors du chemin temps réel.

INTÉGRATION AU SOCLE V13
------------------------
Les erreurs dérivent de la taxonomie de `tradingagents.dataflows.errors`, pour
que la couche de routage réagisse au comportement (« vendeur indisponible »,
« pas de données ») sans connaître MT5.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
)

# ─────────────────────────────────────────────────────────────────────────────
# MT5 n'est pas thread-safe : UN verrou réentrant sérialise tous les appels.
# Réentrant parce que les fonctions publiques s'appellent entre elles
# (ex. `get_rates` → `ensure_symbol`).
# ─────────────────────────────────────────────────────────────────────────────
mt5_lock = threading.RLock()

_initialized = False


class Mt5NotAvailableError(VendorNotConfiguredError):
    """Le module MetaTrader5 est absent, ou le terminal n'est pas joignable.

    Un ``VendorNotConfiguredError`` : la couche de routage bascule sur le
    vendeur suivant (yfinance) au lieu de faire échouer l'analyse.
    """


# Correspondance timeframe lisible → constante MT5, résolue à l'appel pour que
# l'import du module reste possible sans MetaTrader5 installé.
_TIMEFRAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1", "MN1": "TIMEFRAME_MN1",
}


@dataclass(frozen=True)
class SymbolSpec:
    """Spécifications d'un instrument — indispensables au calcul de lot.

    ``tick_value`` / ``tick_size`` sont le couple qui convertit une distance de
    prix en argent : perte_par_lot = distance / tick_size × tick_value. Les
    déduire du seul ``trade_contract_size`` est faux dès que la devise de cotation
    diffère de la devise du compte (XAUUSD sur un compte EUR, par exemple).
    """
    name: str
    digits: int
    point: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_contract_size: float
    spread: int
    tick_value: float
    tick_size: float


@dataclass(frozen=True)
class AccountSnapshot:
    """Photo du compte. ``is_demo`` est lu du broker, jamais déduit du login."""
    login: int
    server: str
    currency: str
    balance: float
    equity: float
    margin_free: float
    is_demo: bool
    trade_mode: int  # 0=DEMO, 1=CONTEST, 2=REAL (constantes MT5)


def _mt5():
    """Retourne le module MetaTrader5, ou lève une erreur de vendeur."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as exc:
        raise Mt5NotAvailableError(
            "module MetaTrader5 absent. Installer avec : pip install -e \".[mt5]\" "
            "(Windows uniquement)."
        ) from exc
    return mt5


@contextmanager
def mt5_session():
    """Garantit un terminal initialisé pour la durée du bloc, sous verrou.

    L'initialisation est faite une seule fois par processus : MT5 n'aime pas
    les cycles initialize/shutdown rapprochés. On ne coupe donc jamais la
    session en cours de route — c'est `shutdown()` qui est explicite.
    """
    global _initialized
    mt5 = _mt5()
    with mt5_lock:
        # ── Le canal IPC survit-il encore ?
        #    Après un redémarrage du terminal, `_initialized` restait vrai et
        #    toutes les lectures échouaient ensuite en boucle sur
        #    « IPC send failed » — la boucle de trading mourait et ne
        #    repartait jamais. Constaté deux fois le 07/08/2026.
        #    `terminal_info()` est un appel local peu coûteux : le payer une
        #    fois par session est le prix d'un flux qui ne s'interrompt pas.
        if _initialized:
            # Vivant par défaut : on ne réinitialise que sur un signal
            # EXPLICITE de mort. Conclure « mort » d'une absence de réponse
            # ferait relancer la session à chaque passage, ce que MT5 supporte
            # mal — il n'aime pas les cycles initialize/shutdown rapprochés.
            vivant = True
            try:
                if mt5.terminal_info() is None:
                    vivant = False
            except Exception:  # noqa: BLE001
                pass
            if not vivant:
                try:
                    mt5.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                _initialized = False

        if not _initialized:
            if not mt5.initialize():
                raise Mt5NotAvailableError(
                    f"terminal MT5 injoignable : {mt5.last_error()}. "
                    "Le terminal doit être ouvert et connecté."
                )
            _initialized = True
        yield mt5


def shutdown() -> None:
    """Ferme la session MT5. À n'appeler qu'à l'arrêt du processus."""
    global _initialized
    with mt5_lock:
        if _initialized:
            _mt5().shutdown()
            _initialized = False


def account_snapshot() -> AccountSnapshot:
    """Photo du compte connecté.

    C'est la source de vérité du mur démo↔réel : ``trade_mode`` vient du
    broker. Ne jamais déduire le type de compte d'un numéro de login.
    """
    with mt5_session() as mt5:
        info = mt5.account_info()
        if info is None:
            raise Mt5NotAvailableError(f"account_info() vide : {mt5.last_error()}")
        trade_mode = int(getattr(info, "trade_mode", 2))
        return AccountSnapshot(
            login=int(info.login),
            server=str(info.server),
            currency=str(info.currency),
            balance=float(info.balance),
            equity=float(info.equity),
            margin_free=float(info.margin_free),
            is_demo=(trade_mode == 0),
            trade_mode=trade_mode,
        )


def ensure_symbol(symbol: str) -> SymbolSpec:
    """Rend un symbole visible et retourne ses spécifications.

    MT5 exige `symbol_select` avant que l'historique se synchronise — c'est la
    cause classique d'un `copy_rates` vide au premier appel sur un instrument.
    """
    with mt5_session() as mt5:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise NoMarketDataError(symbol, detail="symbole inconnu du terminal")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise NoMarketDataError(symbol, detail=f"symbol_select a échoué : {mt5.last_error()}")
        info = mt5.symbol_info(symbol) or info
        return SymbolSpec(
            name=str(info.name),
            digits=int(info.digits),
            point=float(info.point),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            trade_contract_size=float(info.trade_contract_size),
            spread=int(info.spread),
            tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
            tick_size=float(getattr(info, "trade_tick_size", 0.0) or 0.0),
        )


#: Cache des unités de temps longues. Une barre H4 dure quatre heures : la
#: relire à chaque tour de boucle multiplie le trafic MT5 par le nombre
#: d'actifs, sans jamais rien apprendre de neuf.
_CACHE_HTF: dict = {}
_TTL_HTF = 900.0          # 15 min : bien plus court qu'une barre H4


def get_rates_cache(symbol: str, timeframe: str, count: int):
    """`get_rates` avec cache, réservé aux unités de temps ≥ H1.

    N'appelle jamais MT5 pour du M1..M30 : ces barres changent vite, et un
    cache y ferait décider sur des prix périmés.
    """
    import time

    if timeframe.upper() not in ("H1", "H4", "D1", "W1", "MN1"):
        return get_rates(symbol, timeframe, count)

    cle = (symbol.upper(), timeframe.upper(), count)
    entree = _CACHE_HTF.get(cle)
    if entree and time.time() - entree[0] < _TTL_HTF:
        return entree[1]
    df = get_rates(symbol, timeframe, count)
    _CACHE_HTF[cle] = (time.time(), df)
    return df


def _volumes_en_flottant(df):
    """Convertit les colonnes de volume en flottant.

    ⚠️ MT5 rend `tick_volume` et `real_volume` en **uint64**. Sur un entier
    non signé, une soustraction négative ne donne pas un nombre négatif :
    elle **boucle** à 2⁶⁴ ≈ 1.8446744e19. Un `diff()` ou un `pct_change()`
    sur ces colonnes produit donc des valeurs absurdes de cet ordre —
    signalées par Florent le 08/08/2026 dans les données enregistrées.

    Le danger n'est pas la valeur aberrante, qui se voit ; c'est qu'elle
    contamine toute statistique en aval — moyenne, écart-type, corrélation,
    et surtout l'analyse discriminante, dont la conclusion serait dictée par
    une poignée de 1e19.

    Corrigé ICI, à la lecture, plutôt qu'à chaque point d'usage : un seul
    endroit à ne pas oublier, et aucun consommateur ne peut plus déborder.
    """
    for col in ("tick_volume", "real_volume", "volume", "v"):
        if col in df.columns and df[col].dtype.kind in "ui":
            df[col] = df[col].astype("float64")
    return df


def get_rates(symbol: str, timeframe: str = "H4", count: int = 200,
              *, closed_only: bool = True):
    """Retourne les bougies les plus récentes, en DataFrame pandas.

    Args:
        symbol: instrument tel que nommé par le broker (ex. ``EURUSD``).
        timeframe: clé de ``_TIMEFRAMES`` (M1…MN1).
        count: nombre de bougies souhaité.
        closed_only: écarte la bougie en cours. **Par défaut True** — décider
            sur une bougie non clôturée est la source d'erreur la plus commune
            en trading algorithmique (le prix peut encore tout changer).

    Raises:
        Mt5NotAvailableError: terminal indisponible.
        NoMarketDataError: symbole inconnu, ou historique vide.
        ValueError: timeframe non reconnu.
    """
    if timeframe not in _TIMEFRAMES:
        raise ValueError(
            f"timeframe {timeframe!r} inconnu. Valeurs : {sorted(_TIMEFRAMES)}"
        )
    if count <= 0:
        raise ValueError(f"count doit être positif, reçu {count!r}")

    import pandas as pd

    ensure_symbol(symbol)
    with mt5_session() as mt5:
        tf_const = getattr(mt5, _TIMEFRAMES[timeframe])
        # +1 pour pouvoir jeter la bougie en cours sans perdre une barre utile.
        raw = mt5.copy_rates_from_pos(symbol, tf_const, 0, count + (1 if closed_only else 0))

    if raw is None or len(raw) == 0:
        raise NoMarketDataError(symbol, detail=f"aucune bougie {timeframe} retournée")

    df = pd.DataFrame(raw)
    # `rates["time"]` est en heure SERVEUR. L'etiqueter UTC produisait un index
    # en avance de trois heures sur ce courtier : croiser ces barres avec le
    # journal live (lui en vrai UTC) donnait des fenetres decalees, et un rejeu
    # des trades clos ecartait 12 trades sur 37 faute d'intersection. Constate
    # le 12/08/2026. Aucune decision de production ne lit l'heure absolue d'une
    # barre — seul l'ordre des barres compte — mais toute analyse qui melange
    # barres et journal en depend.
    df["time"] = pd.to_datetime(
        df["time"] - decalage_serveur_cache((symbol,)), unit="s", utc=True)
    df = df.set_index("time").sort_index()
    df = _volumes_en_flottant(df)
    if closed_only and len(df) > 0:
        df = df.iloc[:-1]
    if df.empty:
        raise NoMarketDataError(symbol, detail=f"aucune bougie {timeframe} CLÔTURÉE")
    return df.tail(count)


def get_rates_range(symbol: str, timeframe: str, start: datetime, end: datetime):
    """Bougies entre deux dates (bornes incluses), en DataFrame pandas.

    Pendant du contrat des vendeurs V13, qui raisonnent en ``start_date`` /
    ``end_date`` et non en nombre de barres.
    """
    if timeframe not in _TIMEFRAMES:
        raise ValueError(f"timeframe {timeframe!r} inconnu. Valeurs : {sorted(_TIMEFRAMES)}")

    import pandas as pd

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    ensure_symbol(symbol)
    with mt5_session() as mt5:
        tf_const = getattr(mt5, _TIMEFRAMES[timeframe])
        raw = mt5.copy_rates_range(symbol, tf_const, start, end)

    if raw is None or len(raw) == 0:
        raise NoMarketDataError(
            symbol, detail=f"aucune bougie {timeframe} entre {start:%Y-%m-%d} et {end:%Y-%m-%d}"
        )

    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time").sort_index()


def get_tick(symbol: str) -> dict:
    """Dernier tick (bid/ask/spread), horodaté à la milliseconde."""
    ensure_symbol(symbol)
    with mt5_session() as mt5:
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise NoMarketDataError(symbol, detail=f"aucun tick : {mt5.last_error()}")
        bid, ask = float(t.bid), float(t.ask)
        return {
            "symbol": symbol,
            "time": datetime.fromtimestamp(t.time_msc / 1000.0, tz=timezone.utc).isoformat(),
            "bid": bid,
            "ask": ask,
            "spread": round(ask - bid, 10),
            "last": float(t.last),
        }


def list_symbols(pattern: str = "") -> list[str]:
    """Noms des instruments disponibles.

    ⚠️ Hors chemin temps réel : itérer le catalogue complet (~1 200 symboles
    sur ce terminal) monopolise le verrou MT5 et affame les autres appels.
    """
    with mt5_session() as mt5:
        syms = mt5.symbols_get(pattern) if pattern else mt5.symbols_get()
        return sorted(s.name for s in (syms or ()))


def is_available() -> bool:
    """True si le terminal répond. Ne lève jamais — sert aux tests et au /health."""
    try:
        with mt5_session():
            return True
    except Exception:  # noqa: BLE001 — sonde, jamais bloquante
        return False


# ─────────────────────────────────────────────────────────────────────────────
# HORLOGE DU SERVEUR
#
# Un serveur MT5 publie TOUS ses horodatages dans SON fuseau, pas en UTC :
# `deal.time`, `order.time_expiration`, `tick.time`. Le courtier Axi est à
# UTC+3. Deux dégâts constatés le 12/08/2026 :
#
#   * une expiration d'ordre calculée en UTC paraît PASSÉE au serveur, qui
#     refuse l'ordre (retcode 10022) — aucune limite n'a jamais été posée ;
#   * `datetime.fromtimestamp(deal.time, tz=utc)` étiquette l'heure serveur
#     « +00:00 » : les 35 clôtures du journal portent une heure fausse de
#     trois heures, et les durées de détention calculées sont gonflées
#     d'autant (7 minutes journalisées 187).
#
# D'où cette mesure unique, partagée par l'exécution et par le journal.
# ─────────────────────────────────────────────────────────────────────────────

#: Au-delà, la mesure est jugée absurde et on préfère ne rien corriger.
DECALAGE_MAX_S = 24 * 3600

#: Les fuseaux des courtiers sont des multiples du quart d'heure. Arrondir
#: absorbe la latence du tick sans jamais déplacer un horodatage réel.
DECALAGE_PAS_S = 900


def decalage_serveur(mt5, symboles=(), *, maintenant: float | None = None) -> int:
    """Écart en secondes entre l'horloge du serveur et l'horloge UTC.

    Renvoie ``0`` dès que la mesure n'est pas crédible : mieux vaut un
    horodatage non corrigé qu'un décalage inventé.

    On retient le tick le PLUS AVANCÉ parmi les symboles proposés. Un marché
    fermé renvoie un tick ancien, donc sous-estime le décalage ; le maximum est
    le seul estimateur qui ne se laisse pas tromper par un symbole endormi.
    """
    reference = float(maintenant if maintenant is not None
                      else datetime.now(timezone.utc).timestamp())
    candidats = []
    for symbole in [s for s in symboles if s]:
        try:
            tick = mt5.symbol_info_tick(symbole)
            brut = float(getattr(tick, "time", 0) or 0) - reference
        except Exception:  # noqa: BLE001 — observabilité, jamais bloquant
            continue
        if brut == -reference:          # tick absent : time=0
            continue
        if abs(brut) <= DECALAGE_MAX_S:
            candidats.append(brut)
    if not candidats:
        return 0
    return int(round(max(candidats) / DECALAGE_PAS_S)) * DECALAGE_PAS_S


def heure_serveur_en_utc(epoch_serveur: float, decalage_s: int) -> str:
    """Convertit un horodatage MT5 (heure serveur) en instant UTC ISO-8601."""
    return datetime.fromtimestamp(
        float(epoch_serveur) - int(decalage_s), tz=timezone.utc).isoformat()


#: Symboles utilises pour mesurer l'horloge du serveur : ils cotent presque
#: toujours, donc leur tick est frais meme quand les bourses dorment.
_SYMBOLES_HORLOGE = ("BTCUSD", "ETHUSD", "EURUSD", "XAUUSD")

#: Le fuseau d'un serveur ne change qu'aux passages heure d'ete/hiver. Une
#: mesure par quart d'heure suffit, et evite un appel MT5 par barre lue.
_HORLOGE_TTL_S = 900

_horloge: dict = {"decalage": 0, "mesure_a": 0.0}


def decalage_serveur_cache(symboles=(), *, force: bool = False) -> int:
    """Decalage serveur↔UTC, mesure au plus une fois par quart d'heure.

    Rend 0 tant qu'aucune mesure credible n'existe : un horodatage non corrige
    reste preferable a un horodatage corrige au hasard.
    """
    maintenant = datetime.now(timezone.utc).timestamp()
    if not force and (maintenant - _horloge["mesure_a"]) < _HORLOGE_TTL_S:
        return int(_horloge["decalage"])
    try:
        with mt5_session() as mt5:
            mesure = decalage_serveur(
                mt5, tuple(symboles) + _SYMBOLES_HORLOGE, maintenant=maintenant)
    except Exception:  # noqa: BLE001 — l'horloge ne casse jamais une lecture
        return int(_horloge["decalage"])
    if mesure:
        _horloge["decalage"] = int(mesure)
    _horloge["mesure_a"] = maintenant
    return int(_horloge["decalage"])


def reinitialiser_horloge_serveur() -> None:
    """Oublie la mesure d'horloge (tests, ou changement d'heure legale)."""
    _horloge["decalage"] = 0
    _horloge["mesure_a"] = 0.0
