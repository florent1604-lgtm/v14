"""Normalisation du risque macro en jauges affichables — fonction PURE.

Proprietaire unique de la question « comment le tableau de bord voit-il le
macro ? ». Le texte libre (« gel autour de FOMC dans 240 s ») ne traverse jamais
l'interface : il est traduit ici en un etat borne, un pourcentage et une
severite de couleur. Une interface qui lit du texte ne peut pas etre testee,
et une jauge qui lit une chaine finit par afficher ce qu'elle croit comprendre.

Sortie stable, entierement serialisable : c'est le meme bloc que la CLI et le
dashboard consomment, donc les deux ne peuvent pas raconter deux histoires.
"""

from __future__ import annotations

from typing import Any

from titanium.macro.cache import MacroCacheView
from titanium.macro.contracts import MacroRisk, MacroState
from titanium.macro.policy import MacroPolicy

#: Severite visuelle, independante de l'etat exact. Trois niveaux suffisent :
#: vert = serein, orange = autorise mais prudent, rouge = refus.
SEVERITY = {
    MacroState.CLEAR: "ok",
    MacroState.ELEVATED: "warn",
    MacroState.BLACKOUT: "crit",
    MacroState.STALE: "crit",
    MacroState.UNKNOWN: "crit",
}

#: Libelles courts, en francais, tels qu'affiches.
LABEL = {
    MacroState.CLEAR: "Calme",
    MacroState.ELEVATED: "Prudence",
    MacroState.BLACKOUT: "Gel",
    MacroState.STALE: "Perime",
    MacroState.UNKNOWN: "Inconnu",
}


def _pourcentage(valeur: float) -> int:
    return max(0, min(100, int(round(100.0 * valeur))))


def _fraicheur_pct(risk: MacroRisk, policy: MacroPolicy) -> int:
    """100 % = lecture a l'instant, 0 % = au bout du TTL, puis plus rien."""
    if risk.data_age_s is None or policy.ttl_s <= 0:
        return 0
    age = max(0.0, float(risk.data_age_s))
    return _pourcentage(1.0 - age / float(policy.ttl_s))


def _imminence_pct(risk: MacroRisk, policy: MacroPolicy) -> int:
    """0 % = aucun evenement dans l'horizon, 100 % = publication imminente."""
    if risk.seconds_to_next is None or policy.elevated_within_s <= 0:
        return 0
    reste = max(0.0, float(risk.seconds_to_next))
    return _pourcentage(1.0 - min(1.0, reste / float(policy.elevated_within_s)))


def macro_telemetry(
    risk: MacroRisk,
    *,
    policy: MacroPolicy,
    view: MacroCacheView | None = None,
) -> dict[str, Any]:
    """Bloc macro pret a afficher : un etat, des pourcentages, des motifs."""
    prochain: dict[str, Any] | None = None
    if risk.next_event_at is not None:
        prochain = {
            "title": risk.next_event_title,
            "at": risk.next_event_at.isoformat(),
            "seconds": risk.seconds_to_next,
        }
    return {
        "state": risk.state.value,
        "label": LABEL.get(risk.state, risk.state.value),
        "severity": SEVERITY.get(risk.state, "crit"),
        "score_pct": _pourcentage(risk.score),
        "freshness_pct": _fraicheur_pct(risk, policy),
        "imminence_pct": _imminence_pct(risk, policy),
        "allows_new_risk": risk.allows_new_risk,
        "conservative": risk.conservative,
        "data_age_s": risk.data_age_s,
        "countdown_s": risk.seconds_to_next,
        "since_last_s": risk.seconds_since_last,
        "next_event": prochain,
        "source": risk.source,
        "source_digest": risk.source_digest,
        "reasons": list(risk.reasons),
        "policy_fingerprint": policy.fingerprint(),
        "contract_version": risk.contract_version,
        "feed": view.to_dict() if view is not None else None,
    }
