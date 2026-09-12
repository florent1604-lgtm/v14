"""Contrat asynchrone entre le suivi MT5 et le cerveau local GLM.

La boucle live ne contacte jamais Ollama. Elle depose des instantanes scelles,
relit un verdict deja calcule et exige deux verdicts de peur distincts avant
qu'une sortie puisse etre proposee au gestionnaire DEMO.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium.organism.contracts import CORTEX_DECISION_MODEL_VERSION, MODEL_VERSION, digest

POSITION_PROMPT_VERSION = "position-fear-v2-market-context"
FEAR_STATES = frozenset({"FEAR", "PANIC"})
VALID_STATES = frozenset({"CALM", "CAUTION", "FEAR", "PANIC", "UNKNOWN"})


def _utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
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
    """Rend un instantane frais par ticket, en priorisant les moins servis.

    Le producteur publie un nouvel instantane a chaque tour alors que le LLM
    peut n'en traiter qu'un. Trier uniquement par ``observed_at`` affamait
    alors toujours les tickets situes plus loin dans le snapshot MT5. La date
    du dernier verdict impose ici une rotation equitable entre positions.
    """
    request_rows = _records(request_path)
    verdict_rows = _records(verdict_path)
    rendered = {str(row.get("request_ref", "")) for row in verdict_rows}
    current = now or datetime.now(timezone.utc)
    ticket_by_ref = {
        str(row.get("request_ref", "")): str(row.get("ticket", ""))
        for row in request_rows
        if row.get("request_ref") and row.get("ticket")
    }
    last_verdict_at: dict[str, datetime] = {}
    for row in verdict_rows:
        ticket = str(row.get("ticket", "")) or ticket_by_ref.get(
            str(row.get("request_ref", "")), "",
        )
        rendered_at = _utc(str(row.get("rendered_at", "")))
        if ticket and rendered_at is not None:
            previous = last_verdict_at.get(ticket)
            if previous is None or rendered_at > previous:
                last_verdict_at[ticket] = rendered_at
    latest: dict[str, dict[str, Any]] = {}
    for row in request_rows:
        ref = str(row.get("request_ref", ""))
        ticket = str(row.get("ticket", ""))
        observed = _utc(str(row.get("observed_at", "")))
        if not ref or not ticket or ref in rendered or observed is None:
            continue
        if not 0 <= (current - observed).total_seconds() <= max_age_s:
            continue
        latest[ticket] = row
    never = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        latest.values(),
        key=lambda row: (
            last_verdict_at.get(str(row.get("ticket", "")), never),
            str(row.get("observed_at", "")),
        ),
    )[:limit]


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
    try:
        if not math.isfinite(float(verdict.get("confidence", 0.0))):
            confidence = 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    rendered = _utc(str(verdict.get("rendered_at", "")))
    current = now or datetime.now(timezone.utc)
    if rendered is None or not 0 <= (current - rendered).total_seconds() <= max_age_s:
        return FearConfirmation(state=state, confidence=confidence, streak=0,
                                last_ref=ref, reason="VERDICT_PERIME")
    # Le calcul ne rafraichit pas les faits : l'age inclut l'attente des lots
    # et la latence du fournisseur. Un ancien verdict sans date source attend
    # un nouvel instantane, jamais une migration qui inventerait sa fraicheur.
    observed = _utc(str(verdict.get("observed_at", "")))
    if (observed is None or observed > rendered
            or not 0 <= (current - observed).total_seconds() <= max_age_s):
        return FearConfirmation(state=state, confidence=confidence, streak=0,
                                last_ref=ref, reason="INSTANTANE_PERIME_OU_INVALIDE")
    if str(verdict.get("model_version", "")) != CORTEX_DECISION_MODEL_VERSION:
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
