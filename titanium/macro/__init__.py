"""Flux macroeconomique externe de Titanium V14.

Le paquet repond a une seule question, posee a chaque decision : « une
publication a fort impact nous menace-t-elle maintenant ? » — et il y repond
sans jamais bloquer, sans jamais inventer, et en refusant le risque neuf quand
il ne sait pas.

Cinq proprietes, une par module :

* ``contracts``  — objets geles ; le contrat est la seule verite partagee ;
* ``policy``     — bornes numeriques, refus des cles de configuration inconnues ;
* ``cache``      — ce que le processus croit savoir, publie en un geste atomique ;
* ``sources``    — comment lire un fournisseur ; aucun fournisseur decide ;
* ``risk``       — fonction pure vue + instant -> verdict, donc rejouable ;
* ``feed``       — quand relire, hors du chemin critique ;
* ``telemetry``  — comment l'interface voit ce verdict, sans lire de texte.

Utilisation nominale (boucle live, tableau de bord) ::

    from titanium.macro import macro_risk

    risk = macro_risk()              # lit le cache du processus
    if not risk.allows_new_risk:
        ...                          # aucun risque neuf

Rien ici ne leve au moment de la lecture : ``macro_risk`` rend toujours un
objet, et l'etat ``UNKNOWN`` est un refus, pas une exception a attraper.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium.macro.cache import MacroCache, MacroCacheView
from titanium.macro.contracts import (
    MACRO_CONTRACT_VERSION,
    MacroCalendar,
    MacroEvent,
    MacroImpact,
    MacroRisk,
    MacroState,
    currencies_of,
)
from titanium.macro.feed import MacroFeed
from titanium.macro.gate import MACRO_BLOCK_KEYS, macro_block, macro_features
from titanium.macro.policy import MacroPolicy
from titanium.macro.risk import evaluate_macro_risk
from titanium.macro.sources import (
    FileMacroSource,
    HttpMacroSource,
    MacroSource,
    UnavailableMacroSource,
    build_source,
    parse_events,
)
from titanium.macro.telemetry import macro_telemetry

__all__ = [
    "MACRO_BLOCK_KEYS",
    "MACRO_CONTRACT_VERSION",
    "FileMacroSource",
    "HttpMacroSource",
    "MacroCache",
    "MacroCacheView",
    "MacroCalendar",
    "MacroEvent",
    "MacroFeed",
    "MacroImpact",
    "MacroPolicy",
    "MacroRisk",
    "MacroSource",
    "MacroState",
    "UnavailableMacroSource",
    "build_source",
    "currencies_of",
    "evaluate_macro_risk",
    "get_cache",
    "load_policy",
    "macro_block",
    "macro_features",
    "macro_risk",
    "macro_telemetry",
    "parse_events",
]

RACINE = Path(__file__).resolve().parent.parent.parent
FICHIER_CONFIG = RACINE / "config" / "macro.json"

_cache: MacroCache | None = None
_cache_lock = threading.Lock()
_policy: MacroPolicy | None = None


def get_cache() -> MacroCache:
    """Cache partage du processus, cree au premier acces (thread-safe)."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = MacroCache()
    return _cache


def load_policy(path: str | Path | None = None, *, force: bool = False) -> MacroPolicy:
    """Politique macro : ``config/macro.json`` s'il existe, sinon les defauts.

    Un fichier absent n'est pas une erreur — c'est le cas normal d'un poste qui
    n'a pas encore branche le flux, et la politique par defaut a ``enabled=False``.
    Le systeme se comporte alors exactement comme avant l'arrivee du macro :
    aucune cle dans les features, aucun veto. Allumer le flux est une ligne de
    configuration, et cette ligne est le seul interrupteur.
    """
    global _policy
    if _policy is not None and not force and path is None:
        return _policy
    fichier = Path(path) if path is not None else FICHIER_CONFIG
    donnees: dict[str, Any] = {}
    if fichier.is_file():
        donnees = json.loads(fichier.read_text(encoding="utf-8"))
    politique = MacroPolicy.from_mapping(donnees)
    if path is None:
        _policy = politique
    return politique


def macro_risk(
    *,
    now: datetime | None = None,
    symbols: str | list[str] | None = None,
    policy: MacroPolicy | None = None,
    cache: MacroCache | None = None,
) -> MacroRisk:
    """Verdict macro courant. Rend TOUJOURS un objet ; ``UNKNOWN`` est un refus."""
    instant = now or datetime.now(timezone.utc)
    politique = policy or load_policy()
    vue = (cache or get_cache()).view()
    return evaluate_macro_risk(vue, now=instant, policy=politique, symbols=symbols)


def build_feed(
    *,
    policy: MacroPolicy | None = None,
    cache: MacroCache | None = None,
    source: MacroSource | None = None,
) -> MacroFeed:
    """Assemble un service de rafraichissement pret a servir."""
    politique = policy or load_policy()
    return MacroFeed(
        source or build_source(politique),
        cache or get_cache(),
        policy=politique,
    )
