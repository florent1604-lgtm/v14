"""Contrats scelles entre les organes de decision de V14."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

MODEL_VERSION = "qwen3.5:2b"
CORTEX_DECISION_MODEL_VERSION = "hermes:claude-opus-5"
CORTEX_DECISION_PRODUCER = "hermes-cortex/claude-opus-5"
PROMPT_VERSION = "fundamental-gate-v4-qwen35"


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str) + "\n").encode("utf-8")


def digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class DecisionIdentity:
    """Identite causale d'une demande cognitive, stable et rejouable."""

    decision_ref: str
    context_digest: str
    symbol: str
    side: int
    bar_time: str
    model_version: str = MODEL_VERSION
    prompt_version: str = PROMPT_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


#: Cles du panel qui decrivent COMMENT le code a tourne, pas ce que le marche
#: a montre. Elles n'ont rien a faire dans une identite de decision.
#:
#: MESURE DU 07/09/2026 QUI A IMPOSE CE FILTRE. Sur 869 demandes d'avis, il
#: n'existait que 31 couples (symbole, barre) distincts : un facteur de
#: repetition de 28. `AAVE-USD` sur la barre de 12:00 a ete soumis 87 fois au
#: cortex. La barre n'ayant pas change, la reponse ne pouvait pas changer.
#:
#: Cause : sur les 108 cles du panel, UNE SEULE variait entre deux demandes de
#: la meme barre — `jepa_latency_ms`, le temps d'inference de Market-JEPA
#: (0,925 ms puis 0,874 ms). Elle suffisait a faire deriver le `decision_ref`,
#: donc a rendre chaque demande unique et a annuler toute deduplication.
#:
#: C'est le defaut du 07/08/2026 qui revient sous une autre forme : une cle
#: d'identite s'ancre sur l'EVENEMENT observe, jamais sur l'instant ni sur le
#: cout du calcul. Un chronometre n'est pas une donnee de marche.
MESURES_INSTRUMENTALES = frozenset({"jepa_latency_ms"})


def _faits_de_marche(indicateurs: Any) -> Any:
    """Retire du panel ce qui mesure le code au lieu de mesurer le marche."""
    if not isinstance(indicateurs, Mapping):
        return indicateurs or {}
    return {k: v for k, v in indicateurs.items()
            if k not in MESURES_INSTRUMENTALES}


def build_decision_identity(context: Mapping[str, Any]) -> DecisionIdentity:
    """Scelle les faits deterministes sans inclure les champs volatils."""
    stable = {
        "symbol": str(context.get("symbol", "")),
        "side": int(context.get("side", 0) or 0),
        "bar_time": str(context.get("bar_time", "")),
        "verdict": str(context.get("verdict", "")),
        "code": str(context.get("code", "")),
        "piliers": int(context.get("piliers", 0) or 0),
        "total_piliers": int(context.get("total_piliers", 0) or 0),
        "famille": str(context.get("famille", "")),
        "engine_context": str(context.get("engine_context", "")),
        "zones": context.get("zones") or [],
        "indicateurs": _faits_de_marche(context.get("indicateurs")),
    }
    context_digest = digest(stable)
    versions = {
        "context_digest": context_digest,
        "model_version": MODEL_VERSION,
        "prompt_version": PROMPT_VERSION,
    }
    return DecisionIdentity(
        decision_ref=digest(versions),
        context_digest=context_digest,
        symbol=stable["symbol"],
        side=stable["side"],
        bar_time=stable["bar_time"],
    )
