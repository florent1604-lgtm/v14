"""Contrat de politique du cortex Hermes, sans dependance d'execution.

Le cortex travaille hors du chemin critique. Il peut publier un veto ou une
autorisation temporaire pour un contexte deja produit par Titanium, mais il ne
peut ni creer un trade, ni choisir une taille, ni toucher au SL/TP.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from titanium.organism.contracts import DecisionIdentity, digest

CORTEX_ROLE = "hermes-cortex"
CORTEX_POLICY_VERSION = "hermes-policy-v1"
CORTEX_POLICY_TTL_S = 300
ALLOWED_ACTIONS = frozenset({"ALLOW", "WAIT", "BLOCK"})


@dataclass(frozen=True)
class CortexPolicy:
    """Politique cognitive reutilisable, bornee a un contexte et un TTL."""

    policy_ref: str
    source_decision_ref: str
    symbol: str
    side: int
    context_key: str
    action: str
    confidence: float
    summary: str
    evidence_digest: str
    model_version: str
    prompt_version: str
    policy_version: str
    producer: str
    source_observed_at: str
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_cortex_policy(
    identity: DecisionIdentity,
    *,
    context_key: str,
    action: str,
    confidence: float,
    summary: str,
    evidence_digest: str,
    ttl_s: int = CORTEX_POLICY_TTL_S,
    now: datetime | None = None,
    producer: str = CORTEX_ROLE,
    source_observed_at: str = "",
) -> CortexPolicy:
    """Construit une politique scellee; rejette tout contrat ambigu."""
    normalized_action = str(action).upper()
    if normalized_action not in ALLOWED_ACTIONS:
        raise ValueError(f"action cortex interdite: {normalized_action}")
    if not context_key:
        raise ValueError("context_key cortex vide")
    if not evidence_digest:
        raise ValueError("evidence cortex non scellee")
    ttl = int(ttl_s)
    if ttl < 1 or ttl > CORTEX_POLICY_TTL_S:
        raise ValueError(f"TTL cortex hors borne: {ttl}")
    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observed = created
    if source_observed_at:
        try:
            parsed = datetime.fromisoformat(str(source_observed_at))
            if parsed.tzinfo is None:
                raise ValueError("observation cortex sans fuseau")
            observed = parsed.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ValueError("source_observed_at cortex invalide") from exc
    if observed > created + timedelta(seconds=5):
        raise ValueError("observation cortex situee dans le futur")
    # Une reponse tardive ne rajeunit jamais les faits qui l'ont produite.
    expires = min(
        created + timedelta(seconds=ttl),
        observed + timedelta(seconds=ttl),
    )
    stable = {
        "source_decision_ref": identity.decision_ref,
        "symbol": identity.symbol,
        "side": identity.side,
        "context_key": str(context_key),
        "action": normalized_action,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 6),
        "summary": str(summary)[:240],
        "evidence_digest": str(evidence_digest),
        "model_version": identity.model_version,
        "prompt_version": identity.prompt_version,
        "policy_version": CORTEX_POLICY_VERSION,
        "producer": str(producer),
        "source_observed_at": observed.isoformat(),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return CortexPolicy(policy_ref=digest(stable), **stable)


def policy_is_fresh(policy: CortexPolicy, *, now: datetime | None = None) -> bool:
    try:
        expiry = datetime.fromisoformat(policy.expires_at)
        created = datetime.fromisoformat(policy.created_at)
        observed = datetime.fromisoformat(policy.source_observed_at)
        if expiry.tzinfo is None or created.tzinfo is None or observed.tzinfo is None:
            return False
        if observed > created + timedelta(seconds=5):
            return False
        if expiry > created + timedelta(seconds=CORTEX_POLICY_TTL_S):
            return False
        if expiry > observed + timedelta(seconds=CORTEX_POLICY_TTL_S):
            return False
        return (now or datetime.now(timezone.utc)).astimezone(timezone.utc) <= expiry
    except (TypeError, ValueError):
        return False


EXECUTION_FIELDS = frozenset({
    "lot", "quantity", "price", "entry", "sl", "tp", "order_type",
    "magic", "deviation", "ticket",
})
