"""Contrats scelles du flux macroeconomique.

Un seul proprietaire pour la question « que sait-on du calendrier, et a quel
point y croit-on ? ». Les briques d'execution ne lisent jamais un fournisseur
ni un cache : elles lisent un objet gele, ce qui garantit que deux consommateurs
d'une meme evaluation ont vu exactement la meme chose.

**Echec ferme.** Toute donnee manquante, perimee ou non parsable produit un
``MacroRisk`` d'etat ``UNKNOWN`` ou ``STALE``, dont ``allows_new_risk`` vaut
False. Aucun seuil de repli n'est invente : un calendrier qu'on ne peut pas lire
n'est pas un calendrier vide. ``UNKNOWN`` et ``CLEAR`` ne sont PAS equivalents,
et c'est tout l'interet du contrat.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from typing import Any

#: A incrementer a CHAQUE changement de semantique d'un contrat. Les artefacts
#: scelles portent cette version, donc une mesure ancienne reste interpretable.
MACRO_CONTRACT_VERSION = "macro-contract-v1"

#: Tolerance de comparaison des horodatages (secondes). Deux evaluations de la
#: meme fenetre doivent donner le meme verdict ; sans borne, une microseconde de
#: derive suffirait a changer un etat en frontiere de fenetre.
_EPSILON_S = 1e-6


class MacroImpact(IntEnum):
    """Impact attendu d'une publication. Ordonne : HIGH > MEDIUM > LOW."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def parse(cls, raw: Any) -> MacroImpact:
        """Convertit un impact declare ; leve si la valeur est inconnue.

        Un impact non reconnu ne retombe JAMAIS sur LOW : sous-estimer une
        publication est precisement la faute que ce module existe pour eviter.
        """
        if isinstance(raw, MacroImpact):
            return raw
        if isinstance(raw, bool) or raw is None:
            raise ValueError("impact macro manquant")
        if isinstance(raw, (int, float)):
            try:
                return cls(int(raw))
            except ValueError as exc:
                raise ValueError(f"impact macro hors bareme: {raw!r}") from exc
        texte = str(raw).strip().upper()
        aliases = {
            "LOW": cls.LOW, "FAIBLE": cls.LOW, "1": cls.LOW,
            "MEDIUM": cls.MEDIUM, "MOYEN": cls.MEDIUM, "2": cls.MEDIUM,
            "HIGH": cls.HIGH, "ELEVE": cls.HIGH, "ÉLEVÉ": cls.HIGH, "3": cls.HIGH,
        }
        try:
            return aliases[texte]
        except KeyError as exc:
            raise ValueError(f"impact macro inconnu: {raw!r}") from exc


class MacroState(str, Enum):
    """Etat macro publie. Seuls CLEAR et ELEVATED autorisent un nouveau risque."""

    CLEAR = "CLEAR"
    ELEVATED = "ELEVATED"
    BLACKOUT = "BLACKOUT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


def _aware(value: datetime, champ: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{champ} doit etre un datetime")
    if value.tzinfo is None:
        raise ValueError(f"{champ} doit porter un fuseau")
    return value.astimezone(timezone.utc)


def _texte(value: Any, champ: str) -> str:
    texte = str(value or "").strip()
    if not texte:
        raise ValueError(f"{champ} macro vide")
    return texte


@dataclass(frozen=True)
class MacroEvent:
    """Une publication planifiee. Gelee : une fois publiee, elle ne bouge plus."""

    event_id: str
    title: str
    currency: str
    scheduled_at: datetime
    impact: MacroImpact
    source: str = ""
    forecast: float | None = None
    previous: float | None = None
    actual: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _texte(self.event_id, "event_id"))
        object.__setattr__(self, "title", str(self.title or "").strip())
        object.__setattr__(self, "currency", _texte(self.currency, "devise").upper())
        object.__setattr__(self, "scheduled_at", _aware(self.scheduled_at, "scheduled_at"))
        if not isinstance(self.impact, MacroImpact):
            object.__setattr__(self, "impact", MacroImpact.parse(self.impact))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "currency": self.currency,
            "scheduled_at": self.scheduled_at.isoformat(),
            "impact": self.impact.name,
            "source": self.source,
            "forecast": self.forecast,
            "previous": self.previous,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class MacroCalendar:
    """Ce que le PRODUCTEUR a publie, et quand il l'a publie.

    ``fetched_at`` porte l'horodatage du producteur, jamais l'instant de notre
    lecture : c'est le seul qui puisse vieillir. L'instant de lecture vit dans
    ``MacroCache.last_success_at``, ou il ne se confond avec rien.
    """

    provider: str
    fetched_at: datetime
    events: tuple[MacroEvent, ...] = ()
    contract_version: str = MACRO_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _texte(self.provider, "fournisseur"))
        object.__setattr__(self, "fetched_at", _aware(self.fetched_at, "fetched_at"))
        object.__setattr__(
            self, "events", tuple(sorted(self.events, key=lambda e: (e.scheduled_at, e.event_id)))
        )
        for event in self.events:
            if not isinstance(event, MacroEvent):
                raise ValueError("MacroCalendar n'accepte que des MacroEvent")

    def digest(self) -> str:
        """Empreinte du contenu SEUL : deux lectures identiques partagent tout.

        ``fetched_at`` est volontairement exclu. Il mesure la fraicheur annoncee
        par le producteur, pas ce qu'on a lu ; l'inclure ferait diverger le digest
        a chaque rafraichissement et interdirait toute deduplication des decisions
        aval — le meme defaut que ``jepa_latency_ms`` dans l'identite de decision
        Hermes.
        """
        payload = {
            "provider": self.provider,
            "contract_version": self.contract_version,
            "events": [
                {k: v for k, v in e.to_dict().items() if k != "source"} for e in self.events
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    def upcoming(
        self, now: datetime, *, horizon_s: float, min_impact: MacroImpact = MacroImpact.HIGH
    ) -> tuple[MacroEvent, ...]:
        """Publications d'impact suffisant dans ``[now, now + horizon]``."""
        now = _aware(now, "now")
        fin = now + timedelta(seconds=max(0.0, float(horizon_s)))
        return tuple(
            e for e in self.events if e.impact >= min_impact and now <= e.scheduled_at <= fin
        )

    def next_event(
        self, now: datetime, *, min_impact: MacroImpact = MacroImpact.HIGH
    ) -> MacroEvent | None:
        now = _aware(now, "now")
        for event in self.events:
            if event.impact >= min_impact and event.scheduled_at >= now:
                return event
        return None

    def last_event(
        self, now: datetime, *, min_impact: MacroImpact = MacroImpact.HIGH
    ) -> MacroEvent | None:
        now = _aware(now, "now")
        precedent: MacroEvent | None = None
        for event in self.events:
            if event.impact >= min_impact and event.scheduled_at <= now:
                precedent = event
        return precedent


@dataclass(frozen=True)
class MacroRisk:
    """Le verdict macro, fige. Seul objet que le reste du systeme a le droit de lire.

    ``allows_new_risk`` est la porte : True uniquement en CLEAR ou ELEVATED.
    ELEVATED autorise mais demande un comportement conservateur ; BLACKOUT,
    STALE et UNKNOWN refusent. Un etat inconnu n'est jamais traite comme calme.
    """

    state: MacroState
    score: float
    now: datetime
    contract_version: str = MACRO_CONTRACT_VERSION
    source: str = ""
    source_digest: str = ""
    data_age_s: float | None = None
    next_event_title: str = ""
    next_event_at: datetime | None = None
    seconds_to_next: float | None = None
    seconds_since_last: float | None = None
    high_impact_within_s: float | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "now", _aware(self.now, "now"))
        if not isinstance(self.state, MacroState):
            object.__setattr__(self, "state", MacroState(str(self.state)))
        score = float(self.score)
        if not (0.0 <= score <= 1.0):
            raise ValueError("score macro hors de [0, 1]")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "reasons", tuple(str(r) for r in self.reasons))

    @property
    def allows_new_risk(self) -> bool:
        return self.state in (MacroState.CLEAR, MacroState.ELEVATED)

    @property
    def conservative(self) -> bool:
        """Faut-il reduire l'exposition ? Vrai des qu'on n'est plus serein."""
        return self.state is not MacroState.CLEAR

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "score": self.score,
            "now": self.now.isoformat(),
            "contract_version": self.contract_version,
            "source": self.source,
            "source_digest": self.source_digest,
            "data_age_s": self.data_age_s,
            "next_event_title": self.next_event_title,
            "next_event_at": self.next_event_at.isoformat() if self.next_event_at else None,
            "seconds_to_next": self.seconds_to_next,
            "seconds_since_last": self.seconds_since_last,
            "high_impact_within_s": self.high_impact_within_s,
            "reasons": list(self.reasons),
            "allows_new_risk": self.allows_new_risk,
            "conservative": self.conservative,
        }

    @classmethod
    def _refus(
        cls, state: MacroState, now: datetime, reason: str, score: float,
        extra: dict[str, Any],
    ) -> MacroRisk:
        return cls(
            state=state,
            score=max(0.0, min(1.0, float(score))),
            now=_aware(now, "now"),
            reasons=(f"{reason} — risque macro non evaluable, nouveau risque refuse",),
            **extra,
        )

    @classmethod
    def unavailable(
        cls, now: datetime, reason: str, *, score: float = 1.0, **extra: Any
    ) -> MacroRisk:
        """Risque non evaluable — l'etat d'echec ferme, et le seul repli autorise."""
        return cls._refus(MacroState.UNKNOWN, now, reason, score, extra)

    @classmethod
    def stale(
        cls, now: datetime, reason: str, *, score: float = 1.0, **extra: Any
    ) -> MacroRisk:
        """Lecture trop vieille pour etre crue. Distinguer PERIME de INCONNU
        permet a l'interface de dire *pourquoi* le risque est refuse."""
        return cls._refus(MacroState.STALE, now, reason, score, extra)


def currencies_of(symbol: str) -> frozenset[str]:
    """Devises portees par un symbole FX/metal, sans table externe.

    Le marche FX s'ecrit par paires de codes de trois lettres. On les extrait
    sans inventer : ``EURUSD`` porte EUR et USD, ``XAUUSD`` porte USD et un
    metal XAU qui ne publie aucun calendrier — l'inclure est sans effet, donc
    l'ignorer serait une source de bugs gratuite.
    """
    code = "".join(c for c in str(symbol).upper() if c.isalpha())
    if len(code) < 6:
        return frozenset()
    codes = frozenset(code[i : i + 3] for i in range(0, len(code) - 2, 3))
    return frozenset(c for c in codes if c.isalpha())
