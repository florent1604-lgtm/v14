"""Contrats scelles entre les organes de decision de V14."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

MODEL_VERSION = "qwen2.5:3b"
PROMPT_VERSION = "fundamental-gate-v2"


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
        "indicateurs": context.get("indicateurs") or {},
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
