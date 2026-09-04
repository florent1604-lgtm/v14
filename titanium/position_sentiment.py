"""Contrat asynchrone entre le suivi MT5 et le cerveau local GLM.

La boucle live ne contacte jamais Ollama. Elle depose des instantanes scelles,
relit un verdict deja calcule et exige deux verdicts de peur distincts avant
qu'une sortie puisse etre proposee au gestionnaire DEMO.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium.organism.contracts import MODEL_VERSION, digest

POSITION_PROMPT_VERSION = "position-fear-v2-market-context"
FEAR_STATES = frozenset({"FEAR", "PANIC"})
VALID_STATES = frozenset({"CALM", "CAUTION", "FEAR", "PANIC", "UNKNOWN"})


def _utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def build_review(*, ticket: str, symbol: str, side: int, entry: float,
                 current: float, sl: float | None, tp: float | None,
                 r_unit: float, fav_r: float, peak_fav_r: float, mae_r: float,
                 opened_at: str = "", context: dict | None = None,
                 observed_at: str | None = None) -> dict[str, Any]:
    """Construit un instantane causal sans aucune capacite d'execution."""
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    facts = {
        "ticket": str(ticket),
        "symbol": str(symbol),
        "side": int(side),
        "entry": float(entry),
        "current": float(current),
        "sl": None if sl is None else float(sl),
        "tp": None if tp is None else float(tp),
        "r_unit": float(r_unit),
        "fav_r": float(fav_r),
        "peak_fav_r": float(peak_fav_r),
        "mae_r": float(mae_r),
        "opened_at": str(opened_at),
        "observed_at": observed,
        "context": dict(context or {}),
        "model_version": MODEL_VERSION,
        "prompt_version": POSITION_PROMPT_VERSION,
    }
    return {**facts, "request_ref": digest(facts)}


def append_record(path: Path, payload: dict[str, Any]) -> bool:
    """Ajoute une ligne NDJSON. Une panne d'observabilite ne leve jamais."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        out.append(value)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def pending_reviews(request_path: Path, verdict_path: Path, *, limit: int = 8,
                    max_age_s: float = 600.0,
                    now: datetime | None = None) -> list[dict[str, Any]]:
    """Rend le dernier instantane non traite de chaque ticket."""
    rendered = {str(row.get("request_ref", "")) for row in _records(verdict_path)}
    current = now or datetime.now(timezone.utc)
    latest: dict[str, dict[str, Any]] = {}
    for row in _records(request_path):
        ref = str(row.get("request_ref", ""))
        ticket = str(row.get("ticket", ""))
        observed = _utc(str(row.get("observed_at", "")))
        if not ref or not ticket or ref in rendered or observed is None:
            continue
        if (current - observed).total_seconds() > max_age_s:
            continue
        latest[ticket] = row
    return sorted(latest.values(), key=lambda row: str(row.get("observed_at", "")))[:limit]


def latest_verdict(path: Path, ticket: str) -> dict[str, Any] | None:
    """Relit le verdict le plus recent d'un ticket, sans supposer sa fraicheur."""
    wanted = str(ticket)
    rows = _records(path)
    return next((row for row in reversed(rows)
                 if str(row.get("ticket", "")) == wanted), None)


@dataclass(frozen=True)
class FearConfirmation:
    state: str = "UNKNOWN"
    confidence: float = 0.0
    streak: int = 0
    last_ref: str = ""
    should_exit: bool = False
    reason: str = ""


def confirm_fear(verdict: dict[str, Any] | None, *, last_ref: str,
                 previous_streak: int, now: datetime | None = None,
                 max_age_s: float = 240.0, min_confidence: float = 0.75,
                 required: int = 2) -> FearConfirmation:
    """Confirme une transition de peur sans recompter le meme verdict."""
    if not verdict:
        return FearConfirmation(streak=0, reason="AUCUN_VERDICT")
    ref = str(verdict.get("request_ref", ""))
    if not ref:
        return FearConfirmation(streak=0, reason="VERDICT_SANS_REFERENCE")
    state = str(verdict.get("state", "UNKNOWN")).upper()
    if state not in VALID_STATES:
        state = "UNKNOWN"
    try:
        confidence = max(0.0, min(1.0, float(verdict.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    rendered = _utc(str(verdict.get("rendered_at", "")))
    current = now or datetime.now(timezone.utc)
    if rendered is None or abs((current - rendered).total_seconds()) > max_age_s:
        return FearConfirmation(state=state, confidence=confidence, streak=0,
                                last_ref=ref, reason="VERDICT_PERIME")
    if str(verdict.get("model_version", "")) != MODEL_VERSION:
        return FearConfirmation(state=state, confidence=confidence, streak=0,
                                last_ref=ref, reason="MODELE_INATTENDU")
    if ref == last_ref:
        return FearConfirmation(state=state, confidence=confidence,
                                streak=max(0, int(previous_streak)), last_ref=ref,
                                reason="VERDICT_DEJA_COMPTE")
    if state not in FEAR_STATES or confidence < min_confidence:
        return FearConfirmation(state=state, confidence=confidence, streak=0,
                                last_ref=ref, reason="PEUR_NON_CONFIRMEE")
    streak = max(0, int(previous_streak)) + 1
    return FearConfirmation(
        state=state,
        confidence=confidence,
        streak=streak,
        last_ref=ref,
        should_exit=streak >= max(2, int(required)),
        reason="PEUR_CONFIRMEE" if streak >= max(2, int(required)) else "PEUR_1_SUR_2",
    )
