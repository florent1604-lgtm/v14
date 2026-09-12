"""Boucle de rafraichissement du calendrier — jamais sur le chemin critique.

Proprietaire unique de la question « quand va-t-on relire la source ? ». Elle
lance le fournisseur **dans un fil separe** (``asyncio.to_thread``) : un appel
``requests`` bloquant qui prend huit secondes ne doit pas figer la boucle
d'evenements, donc pas retarder une decision d'execution.

**Invariant.** ``refresh_once`` ne leve jamais et n'attend jamais indefiniment :
tout echec devient une ligne dans le cache, et le cache reste fail-closed. Une
source injoignable degrade la confiance, elle n'interrompt pas le systeme.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from titanium.macro.cache import MacroCache, monotonic_ms
from titanium.macro.policy import MacroPolicy
from titanium.macro.sources import MacroSource

logger = logging.getLogger(__name__)


class MacroFeed:
    """Relit la source a intervalle regulier et publie dans le cache partage."""

    def __init__(
        self,
        source: MacroSource,
        cache: MacroCache,
        *,
        policy: MacroPolicy,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.source = source
        self.cache = cache
        self.policy = policy
        #: Injectable pour que les tests n'attendent pas reellement.
        self._sleep = sleep or asyncio.sleep
        self._stop: asyncio.Event | None = None

    # ── une lecture, dans un fil d'execution separe ──────────────────────────
    async def refresh_once(self) -> bool:
        """Rend True si la lecture a reussi. Ne leve jamais."""
        depart = monotonic_ms()
        try:
            calendar = await asyncio.to_thread(self.source.fetch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — une panne de source n'est pas une panne du bot
            self.cache.record_failure(getattr(self.source, "name", ""), exc)
            logger.warning("macro: lecture %s en echec: %s: %s",
                           getattr(self.source, "name", "?"), type(exc).__name__, exc)
            return False
        self.cache.publish(calendar, latency_ms=monotonic_ms() - depart)
        return True

    # ── boucle de service ────────────────────────────────────────────────────
    def _delai_suivant(self) -> float:
        """Intervalle nominal, double a chaque echec consecutif, plafonne.

        Le plafond n'est pas un confort : sans lui, une source morte mais
        repondante serait frappee toutes les cinq minutes indefiniment.
        """
        echecs = self.cache.view().consecutive_failures
        if echecs <= 0:
            return float(self.policy.refresh_s)
        return min(
            float(self.policy.refresh_s) * (2 ** min(echecs, 8)),
            float(self.policy.max_backoff_s),
        )

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Sert jusqu'a ``stop``. La premiere lecture est immediate."""
        self._stop = stop or asyncio.Event()
        while not self._stop.is_set():
            await self.refresh_once()
            if self._stop.is_set():
                break
            try:
                await self._attendre(self._delai_suivant())
            except asyncio.CancelledError:
                raise

    async def _attendre(self, delai_s: float) -> None:
        """Dort ``delai_s``, ou jusqu'a l'arret — le premier des deux.

        La difference n'est pas cosmetique. Avec un simple ``await sleep``,
        ``stop()`` ne prendrait effet qu'au reveil naturel : jusqu'a
        ``max_backoff_s``, soit une heure, et l'arret du processus traînerait
        d'autant. Courir les deux taches borne l'arret a la latence d'un
        reveil d'evenement.
        """
        boucle = asyncio.get_running_loop()
        sommeil = boucle.create_task(self._sleep(max(0.0, float(delai_s))))
        reveil = boucle.create_task(self._stop.wait())  # type: ignore[union-attr]
        try:
            faites, _ = await asyncio.wait(
                {sommeil, reveil}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for tache in (sommeil, reveil):
                if not tache.done():
                    tache.cancel()
            # Consomme les annulations : une tache annulee jamais attendue
            # laisserait un avertissement a la fermeture de la boucle.
            await asyncio.gather(sommeil, reveil, return_exceptions=True)
        for tache in faites:  # une exception du sommeil se propage ici
            tache.result()

    def stop(self) -> None:
        """Demande l'arret. Depuis un autre fil, passer par ``MacroService``."""
        if self._stop is not None:
            self._stop.set()

    def refresh_blocking(self) -> bool:
        """Lecture synchrone, pour la CLI ou un job planifie sans boucle asyncio."""
        depart = monotonic_ms()
        try:
            calendar = self.source.fetch()
        except Exception as exc:  # noqa: BLE001
            self.cache.record_failure(getattr(self.source, "name", ""), exc)
            return False
        self.cache.publish(calendar, latency_ms=monotonic_ms() - depart)
        return True
