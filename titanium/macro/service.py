"""Cycle de vie du flux macro dans UN processus — et rien d'autre.

Proprietaire unique de la question « qui relit le calendrier, et quand
s'arrete-t-il ? ». Jusqu'ici la brique savait relire (``feed.py``) mais personne
ne la demarrait : le processus arme restait donc sur ``UNKNOWN``, donc sur un
refus, alors que sa configuration disait ``enabled=true``. Le trou n'etait pas
dans la lecture, il etait dans le demarrage — c'est cette classe qui le ferme.

Trois choix, chacun defendable :

* **Un fil daemon dedie, pas la boucle de trading.** Le fil porte sa propre
  boucle ``asyncio`` et execute ``MacroFeed.run`` ; le chemin critique ne
  partage avec lui que le cache, lu sous verrou. C'est la seule facon d'avoir
  un rafraichissement reseau regulier sans jamais retarder une decision.
* **Arret interruptible.** ``stop()`` reveille la boucle au lieu d'attendre le
  prochain rafraichissement : sans cela, quitter le processus attendrait
  ``refresh_s`` — jusqu'a une heure. Le delai d'attente est borne, et son
  depassement est *rendu*, jamais avale.
* **Eteint, il ne demarre rien.** La politique par defaut a ``enabled=False`` :
  ``start()`` rend ``False`` et ne cree aucun fil. Une installation qui n'a pas
  branché le flux se comporte donc exactement comme avant, fil compris.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from typing import Any

from titanium.macro.cache import MacroCache
from titanium.macro.feed import MacroFeed
from titanium.macro.policy import MacroPolicy
from titanium.macro.sources import MacroSource, build_source
from titanium.macro.telemetry import macro_telemetry

#: Delai d'arret maximal. Le fil a une boucle qui se reveille sur l'evenement
#: d'arret : au-dela, c'est que quelque chose bloque, et le dire vaut mieux que
#: d'attendre en silence.
ARRET_S = 5.0


class MacroService:
    """Demarre, sert et arrete le flux macro. Une instance par processus."""

    def __init__(
        self,
        *,
        policy: MacroPolicy | None = None,
        cache: MacroCache | None = None,
        source: MacroSource | None = None,
        thread_name: str = "macro-feed",
    ) -> None:
        from titanium.macro import get_cache, load_policy

        self.policy = policy or load_policy()
        self.cache = cache or get_cache()
        self.source = source or build_source(self.policy)
        self.thread_name = thread_name
        self._fil: threading.Thread | None = None
        self._boucle: asyncio.AbstractEventLoop | None = None
        self._arret: asyncio.Event | None = None
        self._verrou = threading.Lock()
        self._erreur = ""

    # ───────────────────────────── etat ─────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._fil is not None and self._fil.is_alive()

    @property
    def erreur(self) -> str:
        return self._erreur

    def etat(self) -> dict[str, Any]:
        """Ce que l'observabilite a le droit de lire. Ne leve jamais."""
        return {
            "enabled": bool(self.policy.enabled),
            "provider": self.policy.provider,
            "running": self.running,
            "thread": self.thread_name,
            "refresh_s": float(self.policy.refresh_s),
            "error": self._erreur,
        }

    # ──────────────────────────── demarrage ─────────────────────────────────

    def start(self) -> bool:
        """Demarre le service. ``False`` si le flux est eteint ou deja en place.

        Le flux eteint n'est PAS une erreur : c'est le defaut du depot, et un
        poste sans calendrier doit se comporter comme avant.
        """
        if not self.policy.enabled:
            return False
        with self._verrou:
            if self.running:
                return True
            self._erreur = ""
            self._fil = threading.Thread(
                target=self._servir, name=self.thread_name, daemon=True
            )
            self._fil.start()
        return True

    def _servir(self) -> None:
        """Corps du fil. Rien de ce qui s'y passe ne remonte dans la boucle."""
        flux = MacroFeed(self.source, self.cache, policy=self.policy)
        try:
            asyncio.run(self._boucle_macro(flux))
        except Exception as exc:  # noqa: BLE001 — un flux mort ne tue pas le bot
            self._erreur = f"{type(exc).__name__}: {exc}"
        finally:
            with self._verrou:
                self._arret = None

    async def _boucle_macro(self, flux: MacroFeed) -> None:
        # La boucle est memorisee pour que `stop()` puisse reveiller l'evenement
        # depuis un AUTRE fil : un `asyncio.Event` n'est pas thread-safe.
        self._boucle = asyncio.get_running_loop()
        with self._verrou:
            self._arret = asyncio.Event()
            arret = self._arret
        await flux.run(arret)

    # ────────────────────────────── arret ───────────────────────────────────

    def stop(self, *, timeout_s: float = ARRET_S) -> bool:
        """Arrete le service. Rend ``True`` si le fil est bien termine.

        Ne leve jamais, et n'attend pas plus que ``timeout_s``.
        """
        with self._verrou:
            fil, boucle, arret = self._fil, self._boucle, self._arret
        if fil is None:
            return True
        if arret is not None and boucle is not None and not boucle.is_closed():
            with suppress(RuntimeError):  # boucle deja arretee : rien a reveiller
                boucle.call_soon_threadsafe(arret.set)
        fil.join(timeout=max(0.0, float(timeout_s)))
        termine = not fil.is_alive()
        with self._verrou:
            if termine:
                self._fil = None
        return termine


def macro_bloc_indisponible(motif: str) -> dict[str, Any]:
    """Le bloc rouge, quand la configuration macro elle-meme est illisible.

    UN SEUL exemplaire : la boucle le publie et la sonde l'affiche. Deux copies
    divergeraient, et c'est justement le cas ou le tableau de bord doit dire la
    verite — un calendrier qu'on ne peut pas lire n'est PAS un calendrier serein.
    """
    return {
        "disponible": False,
        "enabled": False,
        "state": "UNKNOWN",
        "label": "Inconnu",
        "severity": "crit",
        "allows_new_risk": False,
        "conservative": True,
        "score_pct": 100,
        "freshness_pct": 0,
        "imminence_pct": 0,
        "next_event": None,
        "reasons": [f"flux macro illisible : {motif}"],
        "service": {"enabled": False, "running": None, "provider": "",
                    "thread": "", "error": motif},
        "error": motif,
    }


def macro_publication(
    *,
    policy: MacroPolicy | None = None,
    cache: MacroCache | None = None,
    service: MacroService | None = None,
) -> dict[str, Any]:
    """Bloc macro normalisé, prêt à publier dans un battement ou à afficher.

    UN SEUL endroit fabrique ce bloc : la boucle armée le publie, la sonde du
    tableau de bord le recalcule à defaut. Deux calculs separes finiraient par
    raconter deux histoires differentes du meme calendrier.

    **Peut lever** si la configuration est illisible : c'est voulu, et c'est aux
    appelants de decider quoi en faire (``battre`` avale, la sonde affiche un
    rouge). Avaler ici transformerait une faute de configuration en silence.
    """
    from titanium.macro import get_cache, load_policy, macro_risk

    politique = policy or load_policy()
    memoire = cache or get_cache()
    bloc = macro_telemetry(
        macro_risk(policy=politique, cache=memoire), policy=politique, view=memoire.view()
    )
    bloc["disponible"] = True
    bloc["enabled"] = bool(politique.enabled)
    bloc["service"] = (
        service.etat()
        if service is not None
        else {"enabled": bool(politique.enabled), "running": None,
              "provider": politique.provider, "thread": "", "error": ""}
    )
    return bloc
