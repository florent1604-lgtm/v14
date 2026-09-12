"""Cache partage du calendrier macro — thread-safe, et il ne bloque personne.

Proprietaire unique de la question « que croit savoir ce processus, et qu'est-il
arrive la derniere fois qu'il a essaye ? ». La boucle de trading lit ``view()``,
qui prend un verrou quelques microsecondes et rend un objet gele ; le
rafraichissement reseau se fait ailleurs, dans un fil d'execution separe
(``feed.py``).

**Invariant.** Aucune methode publique ne leve et aucune n'attend le reseau.
Une sonde qui plante ne doit pas noircir le tableau de bord, et un fournisseur
lent ne doit pas retarder une decision : c'est exactement le mode de defaillance
que ce cache existe pour rendre impossible.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from titanium.macro.contracts import MacroCalendar


@dataclass(frozen=True)
class MacroCacheView:
    """Vue gelee du cache. Ce que le reste du systeme a le droit de lire."""

    calendar: MacroCalendar | None
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    consecutive_failures: int
    total_failures: int
    total_successes: int
    last_error: str
    provider: str
    last_latency_ms: float

    @property
    def has_data(self) -> bool:
        return self.calendar is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "has_data": self.has_data,
            "events": len(self.calendar.events) if self.calendar else 0,
            "source_digest": self.calendar.digest() if self.calendar else "",
            "last_success_at": (
                self.last_success_at.isoformat() if self.last_success_at else None
            ),
            "last_attempt_at": (
                self.last_attempt_at.isoformat() if self.last_attempt_at else None
            ),
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MacroCache:
    """Le calendrier courant, son horodatage de lecture, et l'historique des essais."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._calendar: MacroCalendar | None = None
        self._last_success_at: datetime | None = None
        self._last_attempt_at: datetime | None = None
        self._consecutive_failures = 0
        self._total_failures = 0
        self._total_successes = 0
        self._last_error = ""
        self._provider = ""
        self._last_latency_ms = 0.0

    def publish(
        self, calendar: MacroCalendar, *, latency_ms: float = 0.0,
        at: datetime | None = None,
    ) -> None:
        """Publie une lecture reussie. Remplace integralement la precedente.

        On ne fusionne jamais deux lectures : un calendrier partiel annonce
        comme complet est plus dangereux qu'un calendrier absent, parce qu'il
        ressemble a une absence de publication.
        """
        horodatage = at or _utc_now()
        with self._lock:
            self._calendar = calendar
            self._last_success_at = horodatage
            self._last_attempt_at = horodatage
            self._consecutive_failures = 0
            self._total_successes += 1
            self._last_error = ""
            self._provider = calendar.provider
            self._last_latency_ms = max(0.0, float(latency_ms))

    def record_failure(
        self, provider: str, error: BaseException | str, *, at: datetime | None = None
    ) -> None:
        """Enregistre un echec SANS effacer la derniere lecture connue.

        La lecture precedente reste visible : elle pourra etre jugee perimee par
        la politique de fraicheur, ce qui est une conclusion differente de
        « il n'y a jamais eu de donnees ».
        """
        horodatage = at or _utc_now()
        with self._lock:
            self._last_attempt_at = horodatage
            self._consecutive_failures += 1
            self._total_failures += 1
            self._last_error = f"{type(error).__name__}: {error}"
            if provider:
                self._provider = provider

    def view(self) -> MacroCacheView:
        with self._lock:
            return MacroCacheView(
                calendar=self._calendar,
                last_success_at=self._last_success_at,
                last_attempt_at=self._last_attempt_at,
                consecutive_failures=self._consecutive_failures,
                total_failures=self._total_failures,
                total_successes=self._total_successes,
                last_error=self._last_error,
                provider=self._provider,
                last_latency_ms=self._last_latency_ms,
            )

    def clear(self) -> None:
        with self._lock:
            self._calendar = None
            self._last_success_at = None
            self._last_attempt_at = None
            self._consecutive_failures = 0
            self._last_error = ""
            self._last_latency_ms = 0.0


def monotonic_ms() -> float:
    """Chronometre monotone, insensible aux reglages d'horloge."""
    return time.monotonic() * 1000.0
