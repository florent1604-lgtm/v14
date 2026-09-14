"""Evaluation du risque macro — fonction PURE, sans E/S ni horloge implicite.

Proprietaire unique de la question « faut-il prendre du risque neuf maintenant ? ».
Elle ne lit ni fichier ni reseau : elle recoit une vue de cache et un instant, et
rend un objet gele. C'est ce qui la rend rejouable sur un calendrier historique,
donc testable sans terminal et sans reseau.

Ordre d'evaluation, et pourquoi il n'est pas interchangeable :

1. flux desactive par configuration      -> CLEAR (choix humain explicite)
2. aucune lecture / horodatage manquant  -> UNKNOWN
3. producteur trop vieux ou date futur   -> STALE
4. calendrier sans aucun evenement       -> UNKNOWN
5. publication de fort impact en cours   -> BLACKOUT
6. publication imminente dans l'horizon  -> ELEVATED
7. sinon                                 -> CLEAR

Les cas 2 a 4 precedent les cas 5 et 6 : sans donnee fraiche ET exploitable, on
ne peut pas affirmer qu'aucune publication n'arrive, et conclure CLEAR depuis un
calendrier perime ou vide est precisement le faux negatif que ce module existe
pour empecher. STALE precede le calendrier vide : « perime » et « je n'ai rien
lu » sont deux conclusions differentes, et la plus precise doit gagner pour que
l'interface puisse dire POURQUOI le risque est refuse.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from titanium.macro.cache import MacroCacheView
from titanium.macro.contracts import (
    MacroCalendar,
    MacroEvent,
    MacroImpact,
    MacroRisk,
    MacroState,
    currencies_of,
)
from titanium.macro.policy import MacroPolicy

#: Tolerance de derive d'horloge. Au-dela, un horodatage « futur » n'est plus
#: une derive mais une donnee non fiable : un calendrier lu demain ne prouve
#: rien sur maintenant, et une horloge decalee fait expirer les fenetres de gel.
SKEW_TOLERANCE_S = 60.0


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now doit porter un fuseau")
    return now.astimezone(timezone.utc)


def _devises_suivies(symbols: str | Iterable[str] | None) -> frozenset[str]:
    """Devises a considerer, ou ensemble VIDE quand on ne sait pas les deduire.

    Un ensemble vide signifie « ne filtre pas » et non « rien ne compte ». Un
    symbole inconnu (indice, crypto hors format FX) doit conserver TOUTES les
    publications : le contraire — zero evenement pertinent, donc CLEAR — serait
    le pire des replis, un faux calme fabrique par une table incomplete.
    """
    if symbols is None:
        return frozenset()
    liste = [symbols] if isinstance(symbols, str) else list(symbols)
    devises: set[str] = set()
    for symbole in liste:
        devises |= set(currencies_of(str(symbole)))
    return frozenset(devises)


def _pertinents(
    calendar: MacroCalendar, devises: frozenset[str], impact_min: MacroImpact
) -> tuple[MacroEvent, ...]:
    return tuple(
        e
        for e in calendar.events
        if e.impact >= impact_min and (not devises or e.currency in devises)
    )


def _score_eleve(policy: MacroPolicy, proximite: float) -> float:
    """Interpole entre le plancher ELEVATED et le score BLACKOUT, borne a [0, 1]."""
    proximite = max(0.0, min(1.0, proximite))
    plancher = float(policy.score_elevated)
    cible = max(plancher, float(policy.score_blackout))
    return max(0.0, min(1.0, plancher + (cible - plancher) * proximite))


def evaluate_macro_risk(
    view: MacroCacheView | None,
    *,
    now: datetime,
    policy: MacroPolicy,
    symbols: str | Iterable[str] | None = None,
) -> MacroRisk:
    """Rend le verdict macro pour ``symbols`` a l'instant ``now``."""
    instant = _aware(now)

    if not policy.enabled:
        return MacroRisk(
            state=MacroState.CLEAR, score=0.0, now=instant,
            source=policy.provider,
            reasons=("flux macro desactive par configuration",),
        )

    if view is None or view.calendar is None or view.calendar.fetched_at is None:
        return MacroRisk.unavailable(
            instant, "aucun calendrier macro publie", score=policy.score_unavailable
        )

    calendar = view.calendar
    age_s = (instant - calendar.fetched_at).total_seconds()
    base: dict[str, Any] = {
        "source": calendar.provider,
        "source_digest": calendar.digest(),
        "data_age_s": age_s,
    }

    if age_s < -SKEW_TOLERANCE_S:
        return MacroRisk.stale(
            instant,
            f"horodatage producteur dans le futur ({age_s:.0f} s)",
            score=policy.score_unavailable,
            **base,
        )
    if age_s > float(policy.ttl_s):
        return MacroRisk.stale(
            instant,
            f"calendrier perime ({age_s:.0f} s > ttl {policy.ttl_s:.0f} s)",
            score=policy.score_unavailable,
            **{**base, "data_age_s": age_s},
        )

    # ── 4. Un calendrier exploitable ? Zero evenement ne dit pas « rien a
    # signaler », il dit « je n'ai rien lu » : la cle renommee, le schema muet et
    # le producteur casse y ressemblent tous a une journee sereine. Conclure
    # CLEAR ferait exactement le faux calme que `_devises_suivies` nomme.
    if not calendar.events:
        return MacroRisk.unavailable(
            instant,
            f"calendrier macro sans aucun evenement (fournisseur {calendar.provider})",
            score=policy.score_unavailable,
            **base,
        )

    devises = _devises_suivies(symbols)
    evenements = _pertinents(calendar, devises, policy.min_impact)
    prochain = next((e for e in evenements if e.scheduled_at >= instant), None)
    dernier = None
    for event in evenements:
        if event.scheduled_at <= instant:
            dernier = event
    seconds_to_next = (
        (prochain.scheduled_at - instant).total_seconds() if prochain else None
    )
    seconds_since_last = (
        (instant - dernier.scheduled_at).total_seconds() if dernier else None
    )
    timeline: dict[str, Any] = {
        **base,
        "next_event_title": prochain.title if prochain else "",
        "next_event_at": prochain.scheduled_at if prochain else None,
        "seconds_to_next": seconds_to_next,
        "seconds_since_last": seconds_since_last,
    }

    # ── 4. Fenetre de gel : autour d'une publication, avant comme apres.
    #
    # On retient l'evenement le plus PROCHE, pas le premier de la liste :
    # plusieurs publications peuvent tomber dans la meme fenetre, et annoncer
    # la mauvaise ferait perdre au motif sa valeur de preuve.
    en_gel: MacroEvent | None = None
    delta_gel = 0.0
    for event in evenements:
        delta = (event.scheduled_at - instant).total_seconds()
        if not (-float(policy.blackout_after_s) <= delta <= float(policy.blackout_before_s)):
            continue
        if en_gel is None or abs(delta) < abs(delta_gel):
            en_gel, delta_gel = event, delta
    if en_gel is not None:
        position = (
            f"dans {delta_gel:.0f} s" if delta_gel > 0 else f"il y a {abs(delta_gel):.0f} s"
        )
        return MacroRisk(
            state=MacroState.BLACKOUT,
            score=max(0.0, min(1.0, float(policy.score_blackout))),
            now=instant,
            reasons=(
                f"gel autour de « {en_gel.title} » ({en_gel.currency}, "
                f"{en_gel.impact.name}) {position} — aucun risque neuf",
            ),
            high_impact_within_s=max(0.0, delta_gel),
            **timeline,
        )

    # ── 5. Publication imminente : on autorise, en demandant la prudence.
    horizon = float(policy.elevated_within_s)
    if prochain is not None and seconds_to_next is not None and seconds_to_next <= horizon:
        proximite = 1.0 - (seconds_to_next / horizon) if horizon > 0 else 1.0
        return MacroRisk(
            state=MacroState.ELEVATED,
            score=_score_eleve(policy, proximite),
            now=instant,
            reasons=(
                f"« {prochain.title} » ({prochain.currency}, {prochain.impact.name}) "
                f"dans {seconds_to_next:.0f} s — exposition a reduire",
            ),
            high_impact_within_s=seconds_to_next,
            **timeline,
        )

    # ── 6. Rien de notable dans l'horizon.
    return MacroRisk(
        state=MacroState.CLEAR, score=0.0, now=instant,
        reasons=(
            f"aucune publication {policy.min_impact.name}+ "
            f"({len(evenements)} suivie(s)) dans l'horizon",
        ),
        **timeline,
    )
