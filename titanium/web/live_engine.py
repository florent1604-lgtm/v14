"""Lecteur partagé, en lecture seule, pour le tableau de bord temps réel.

Ce module est la seule façade entre le serveur HTTP du tableau de bord et
``titanium.web.state``. Il n'ouvre aucun ordre, n'arme rien, ne construit
jamais d'exécuteur : il lit, met en cache, et sert le même instantané à tous
les clients connectés.

Trois règles, chacune corrige un piège mesuré sur V14 :

1. **Un seul lecteur.** ``state.positions()`` prend ``mt5_lock`` et ouvre une
   session MT5 à chaque appel, sans cache. Un front qui rafraîchit toutes les
   deux secondes prendrait ce verrou trente fois par minute — celui dont la
   boucle armée a besoin pour passer ses ordres. Ici, un unique rafraîchisseur
   interroge MT5 ; les clients lisent le cache. Dix clients coûtent autant
   qu'un seul.
2. **Jamais d'appel MT5 depuis une requête HTTP.** Une requête lit le cache,
   point. Si le cache est froid, elle reçoit un instantané vide marqué
   ``stale`` plutôt que d'aller prendre le verrou.
3. **Une donnée périmée est annoncée comme telle.** Chaque bloc porte son âge
   et son drapeau ``stale``. Le tableau de bord ne montre jamais une valeur
   vieille en la faisant passer pour fraîche.

Le mode réel n'est pas atteignable depuis ce module : il ne dépend d'aucun
symbole capable d'agir sur le courtier. ``tests/test_dashboard_readonly.py``
le vérifie par analyse syntaxique, pour que la garantie survive aux
refactorisations.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from titanium.web import state

#: Durées de fraîcheur, en secondes. ``positions`` est le bloc coûteux : il
#: prend le verrou MT5. Cinq secondes suffisent à un opérateur humain et
#: divisent par dix la pression sur le verrou par rapport à un front à 500 ms.
TTL_POSITIONS = 5.0
TTL_ACCOUNT = 5.0
TTL_WALL = 10.0
TTL_META = 30.0
TTL_LOOP = 5.0
TTL_RISK = 15.0
TTL_CHART = 45.0
TTL_ANALYSES = 10.0
TTL_UNIVERS = 600.0

#: Au-delà de cet âge, un bloc n'est plus servi comme une information : il est
#: marqué périmé et le tableau de bord doit le griser.
STALE_FACTOR = 3.0


@dataclass
class Bloc:
    """Un morceau d'état, avec l'heure à laquelle il a réellement été lu."""

    nom: str
    ttl: float
    valeur: Any = None
    lu_a: float = 0.0
    erreur: str = ""
    lectures: int = 0

    @property
    def age(self) -> float:
        return float("inf") if self.lu_a == 0.0 else time.monotonic() - self.lu_a

    @property
    def frais(self) -> bool:
        return self.age <= self.ttl

    @property
    def perime(self) -> bool:
        return self.age > self.ttl * STALE_FACTOR

    def enveloppe(self) -> dict:
        age = self.age
        return {
            "data": self.valeur,
            "age_s": None if age == float("inf") else round(age, 2),
            "stale": self.perime,
            "erreur": self.erreur,
            "jamais_lu": self.lu_a == 0.0,
        }


class LiveEngine:
    """Lecteur unique, partagé par tous les clients du tableau de bord.

    Aucune méthode publique ne mute quoi que ce soit côté courtier. Les seuls
    effets de bord sont l'écriture du cache en mémoire et le journal interne.
    """

    def __init__(self, *, symbole: str = "US500", timeframe: str = "M15") -> None:
        self.symbole = symbole
        self.timeframe = timeframe
        # Un verrou unique sérialise TOUTES les lectures MT5. Deux blocs ne
        # peuvent pas se disputer mt5_lock depuis ce process.
        self._verrou = asyncio.Lock()
        self._blocs: dict[str, Bloc] = {
            "meta": Bloc("meta", TTL_META),
            "wall": Bloc("wall", TTL_WALL),
            "account": Bloc("account", TTL_ACCOUNT),
            "positions": Bloc("positions", TTL_POSITIONS),
            "loop": Bloc("loop", TTL_LOOP),
            "risque": Bloc("risque", TTL_RISK),
            "chart": Bloc("chart", TTL_CHART),
            "runs": Bloc("runs", TTL_ANALYSES),
            "analystes": Bloc("analystes", TTL_ANALYSES),
            "univers": Bloc("univers", TTL_UNIVERS),
        }
        self._sources: dict[str, Callable[[], Any]] = {
            "meta": state.meta,
            "wall": state.wall,
            "account": state.account,
            "positions": state.positions,
            "loop": state.loop,
            "risque": state.risque,
            "chart": lambda: state.chart(
                self.symbole, timeframe=self.timeframe, barres=180
            ),
            "runs": state.runs,
            "analystes": state.analystes,
            "univers": state.univers_disponible,
        }
        self._journal: list[dict] = []
        self._demarre = time.monotonic()
        self._tache: asyncio.Task | None = None

    # ────────────────────────────── lecture ──────────────────────────────

    async def _rafraichir(self, nom: str) -> Bloc:
        """Relit un bloc depuis MT5 si son TTL est écoulé, sinon rend le cache.

        Le double contrôle autour du verrou évite la ruée : dix clients qui
        arrivent ensemble sur un cache froid déclenchent une seule lecture, les
        neuf autres trouvent le résultat déjà posé.
        """
        bloc = self._blocs[nom]
        if bloc.frais:
            return bloc
        async with self._verrou:
            if bloc.frais:  # posé pendant l'attente du verrou
                return bloc
            source = self._sources[nom]
            try:
                # to_thread : state.* est bloquant (verrou MT5, entrées/sorties).
                # L'appeler directement figerait la boucle asyncio et tous les
                # flux SSE avec elle.
                bloc.valeur = await asyncio.to_thread(source)
                bloc.erreur = ""
            except Exception as exc:  # noqa: BLE001 — un bloc mort ne tue pas la page
                bloc.erreur = f"{type(exc).__name__}: {exc}"
                self._logger("ERROR", f"lecture {nom} impossible : {bloc.erreur}")
            bloc.lu_a = time.monotonic()
            bloc.lectures += 1
            return bloc

    async def bloc(self, nom: str) -> dict:
        """Instantané enveloppé d'un bloc, avec son âge et son drapeau périmé."""
        if nom not in self._blocs:
            raise KeyError(f"bloc inconnu : {nom}")
        return (await self._rafraichir(nom)).enveloppe()

    async def instantane(self) -> dict:
        """Tous les blocs d'un coup, sérialisés par le même verrou unique."""
        noms = ["meta", "wall", "account", "positions", "loop", "risque"]
        for nom in noms:  # séquentiel : le verrou MT5 n'aime pas la concurrence
            await self._rafraichir(nom)
        return {nom: self._blocs[nom].enveloppe() for nom in noms}

    async def chart(self, symbole: str | None = None, timeframe: str | None = None) -> dict:
        """Chandeliers + zones. Changer d'actif invalide le cache."""
        if (symbole and symbole != self.symbole) or (timeframe and timeframe != self.timeframe):
            self.symbole = symbole or self.symbole
            self.timeframe = timeframe or self.timeframe
            self._blocs["chart"].lu_a = 0.0
        return await self.bloc("chart")

    async def analyses(self, symbole: str | None = None) -> dict:
        """Décisions multi-agents et avis LLM déjà produits par les boucles V14."""
        runs, analystes = await self.bloc("runs"), await self.bloc("analystes")
        sym = str(symbole or "").upper().strip()
        runs_data = list(runs.get("data") or [])
        analystes_data = dict(analystes.get("data") or {})
        avis = list(analystes_data.get("recents") or [])
        if sym:
            runs_data = [r for r in runs_data if str(r.get("ticker", "")).upper() == sym]
            avis = [a for a in avis if str(a.get("symbol", "")).upper() == sym]
        analystes_data["recents"] = avis
        return {
            "symbol": sym or None,
            "runs": runs_data,
            "analystes": analystes_data,
            "stale": bool(runs.get("stale") or analystes.get("stale")),
            "age_s": max(runs.get("age_s") or 0.0, analystes.get("age_s") or 0.0),
            "erreurs": [e for e in (runs.get("erreur"), analystes.get("erreur")) if e],
        }

    # ────────────────────────────── journal ──────────────────────────────

    def _logger(self, niveau: str, message: str) -> None:
        self._journal.append({
            "ts": time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}",
            "niveau": niveau,
            "message": message,
        })
        del self._journal[:-500]  # borne dure : le journal ne fuit pas en mémoire

    def noter(self, niveau: str, message: str) -> None:
        """Entrée de journal depuis l'extérieur (dépôt d'intention, etc.)."""
        self._logger(niveau, message)

    def symboles_repli(self) -> list[str]:
        """Retourne l'univers statique V14 sans exposer ``state`` à la couche HTTP."""
        return list(state.SYMBOLES_DEFAUT)

    def journal(self, niveau: str = "ALL", limite: int = 200) -> list[dict]:
        lignes = self._journal if niveau == "ALL" else [
            e for e in self._journal if e["niveau"] == niveau
        ]
        return lignes[-limite:]

    # ────────────────────────────── santé ────────────────────────────────

    def sante(self) -> dict:
        """Ce que le lecteur sait de lui-même : utile pour voir s'il ment."""
        return {
            "uptime_s": round(time.monotonic() - self._demarre, 1),
            "lecture_seule": True,
            "blocs": {
                nom: {
                    "age_s": None if b.age == float("inf") else round(b.age, 2),
                    "ttl_s": b.ttl,
                    "stale": b.perime,
                    "lectures": b.lectures,
                    "erreur": b.erreur,
                }
                for nom, b in self._blocs.items()
            },
        }


#: Instance unique du process. Un second lecteur doublerait la pression sur le
#: verrou MT5 sans rien apporter.
_moteur: LiveEngine | None = None


def get_engine() -> LiveEngine:
    global _moteur  # noqa: PLW0603 — singleton assumé, un seul lecteur par process
    if _moteur is None:
        _moteur = LiveEngine()
    return _moteur
