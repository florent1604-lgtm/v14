"""Adaptateur asynchrone entre Hermès/Claude Code et les organes V14.

Hermès ne tourne jamais dans le chemin chaud MT5. Il reçoit des faits scellés,
rend un avis strictement consultatif, puis le worker publie cet avis dans la
mémoire centrale. Aucun outil fichier, terminal ou MCP ne lui est exposé ici.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium.fundamental_intelligence import Evidence, _balanced, collect
from titanium.organism.contracts import MODEL_VERSION, PROMPT_VERSION, digest
from titanium.position_sentiment import POSITION_PROMPT_VERSION

HERMES_PROVIDER = "claude-code"
# Opus 5 depuis le 05/09/2026, decision de Florent : le cortex principal doit
# raisonner avec le meilleur modele disponible, pas avec le plus rapide.
# `config.yaml` d'Hermes porte deja `claude-opus-5` ; le drapeau explicite
# passe au CLI gagne toujours sur le fichier de reglages, donc les deux doivent
# concorder — sinon le reglage projet ne sert a rien (lecon PRIME_V14.bat).
HERMES_MODEL = "claude-opus-5"
HERMES_SOURCE = f"hermes-cortex/{HERMES_MODEL}"
#: Porte par chaque verdict d'Hermes. Prefixe pour qu'un filtre sur les
#: cloture reelles separe sans ambiguite les deux cortex.
HERMES_MODEL_VERSION = f"hermes:{HERMES_MODEL}"
# Opus reflechit plus longtemps que Sonnet. Mesure le 05/09 : 11 s sur un lot
# unitaire, mais la marge doit couvrir un lot charge et une fenetre de debit
# saturee par les autres clients Claude Code de la machine. Le disjoncteur
# ci-dessous transforme un depassement en repli local, jamais en blocage.
HERMES_TIMEOUT_S = 120.0
HERMES_BACKOFF_S = 60.0
HERMES_QUOTA_BACKOFF_S = 600.0
ROOT = Path(__file__).resolve().parents[1]

_CIRCUIT: dict[str, Any] = {"retry_at": 0.0, "error": ""}


class HermesCortexUnavailable(RuntimeError):
    """Hermès est indisponible; le worker peut employer son repli local."""


def _hermes_executable() -> Path:
    explicit = os.getenv("HERMES_CORTEX_EXECUTABLE", "").strip()
    candidates = [Path(explicit)] if explicit else []
    if os.name == "nt":
        local = os.getenv("LOCALAPPDATA", "").strip()
        if local:
            candidates.append(
                Path(local) / "hermes" / "hermes-agent" / "venv" /
                "Scripts" / "hermes.exe"
            )
    discovered = shutil.which("hermes.exe")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise HermesCortexUnavailable("executable Hermes introuvable")


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lstrip().startswith("json"):
            stripped = stripped.lstrip()[4:].lstrip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise HermesCortexUnavailable("reponse Hermes sans JSON valide")


def _trip(reason: str) -> None:
    lowered = reason.lower()
    quota = any(token in lowered for token in (
        "quota", "credit", "usage", "payment", "402", "429",
    ))
    delay = HERMES_QUOTA_BACKOFF_S if quota else HERMES_BACKOFF_S
    _CIRCUIT.update(retry_at=time.time() + delay, error=reason[:240])


def circuit_status() -> dict[str, Any]:
    """Expose un état sans secret pour les logs et le dashboard."""
    now = time.time()
    return {
        "available": now >= float(_CIRCUIT["retry_at"]),
        "retry_in_s": max(0.0, float(_CIRCUIT["retry_at"]) - now),
        "last_error": str(_CIRCUIT["error"]),
        "provider": HERMES_PROVIDER,
        "model": HERMES_MODEL,
    }


def _ask(prompt: str, *, timeout_s: float = HERMES_TIMEOUT_S) -> dict[str, Any]:
    status = circuit_status()
    if not status["available"]:
        raise HermesCortexUnavailable(
            f"circuit Hermes ouvert encore {status['retry_in_s']:.0f}s"
        )
    command = [
        str(_hermes_executable()),
        "-z", prompt,
        "--provider", HERMES_PROVIDER,
        "--model", HERMES_MODEL,
        "--ignore-rules",
        "-t", "todo",
    ]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout_s)),
            check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _trip(type(exc).__name__)
        raise HermesCortexUnavailable(type(exc).__name__) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "erreur Hermes").strip()
        _trip(detail)
        raise HermesCortexUnavailable(detail[:240])
    try:
        result = _json_object(completed.stdout)
    except HermesCortexUnavailable as exc:
        _trip(str(exc))
        raise
    _CIRCUIT.update(retry_at=0.0, error="")
    return result


def _evidence_by_symbol(symbols: list[str]) -> dict[str, list[Evidence]]:
    unique = list(dict.fromkeys(symbols))
    with ThreadPoolExecutor(max_workers=min(4, len(unique) or 1)) as pool:
        return dict(zip(unique, pool.map(collect, unique), strict=False))


def _facts(evidence: list[Evidence], *, limit: int = 6) -> list[dict[str, str]]:
    return [
        {"source": item.source, "text": item.text[:240],
         "observed_at": item.observed_at}
        for item in _balanced(evidence, limit=limit)
    ]


def analyse_entries(requests: list[dict]) -> list[dict]:
    """Fait d'Hermès le décideur cognitif final des candidats d'entrée."""
    if not requests:
        return []
    evidence = _evidence_by_symbol([str(row.get("symbol", "")) for row in requests])
    prepared = []
    for row in requests:
        symbol = str(row.get("symbol", ""))
        facts = _facts(evidence.get(symbol, []))
        prepared.append({
            "decision_ref": str(row.get("decision_ref", "")),
            "symbol": symbol,
            "side": int(row.get("side", 0) or 0),
            "mechanical_summary": str(row.get("mechanical_summary", ""))[:500],
            "model_version": str(row.get("model_version", MODEL_VERSION)),
            "prompt_version": str(row.get("prompt_version", PROMPT_VERSION)),
            "facts": facts,
            "evidence_digest": digest({"evidence": facts}),
        })
    refs = [item["decision_ref"] for item in prepared]
    prompt = "\n".join([
        "Tu es Hermes, cortex principal du bot V14 sur MT5 DEMO uniquement.",
        "Les organes deterministes ont deja trouve ces candidats. Tu rends le veto cognitif final.",
        "Tu ne peux ni creer un sens, ni fixer prix/lot/SL/TP, ni appeler un outil ou MT5.",
        "Utilise uniquement les faits fournis. N'invente aucune actualite.",
        "ALLOW si les faits n'invalident pas la these mecanique; WAIT si ambigu; BLOCK si contradiction nette.",
        "Reponds seulement en JSON minifie: {\"verdicts\":[{\"decision_ref\":\"exact\",\"action\":\"ALLOW|WAIT|BLOCK\",\"confidence\":0.0,\"summary\":\"francais max 180 caracteres\"}]}",
        json.dumps({"candidates": prepared}, ensure_ascii=False, separators=(",", ":")),
    ])
    parsed = _ask(prompt)
    rows = parsed.get("verdicts")
    if not isinstance(rows, list):
        raise HermesCortexUnavailable("verdicts Hermes absent")
    by_ref = {str(row.get("decision_ref", "")): row for row in rows if isinstance(row, dict)}
    if set(by_ref) != set(refs):
        raise HermesCortexUnavailable("liaison decision_ref Hermes incomplete")
    answers = []
    for item in prepared:
        raw = by_ref[item["decision_ref"]]
        action = str(raw.get("action", "WAIT")).upper()
        if action not in {"ALLOW", "WAIT", "BLOCK"}:
            action = "WAIT"
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        answers.append({
            "action": action,
            "confidence": confidence,
            "summary": str(raw.get("summary", ""))[:240],
            "sources": sorted({fact["source"] for fact in item["facts"]}),
            "evidence_digest": item["evidence_digest"],
            # Le modele qui a REELLEMENT juge, pas celui que la demande avait
            # prevu. `item["model_version"]` vaut MODEL_VERSION (le modele
            # local) parce que l'identite est scellee par l'appelant avant de
            # savoir quel cortex repondra. Le recopier ici attribuait les
            # verdicts d'Hermes a qwen3.5:2b : le champ devient faux exactement
            # la ou on veut comparer les deux cortex sur les cloture reelles.
            "model_version": HERMES_MODEL_VERSION,
            "prompt_version": item["prompt_version"],
            "source": HERMES_SOURCE,
        })
    return answers


def analyse_positions(reviews: list[dict]) -> list[dict]:
    """Demande à Hermès un verdict borné pour chaque position ouverte."""
    selected = list(reviews[:8])
    if not selected:
        return []
    evidence = _evidence_by_symbol([str(row.get("symbol", "")) for row in selected])
    prepared = []
    for row in selected:
        symbol = str(row.get("symbol", ""))
        facts = _facts(evidence.get(symbol, []))
        prepared.append({
            "request_ref": str(row.get("request_ref", "")),
            "ticket": str(row.get("ticket", "")),
            "symbol": symbol,
            "side": int(row.get("side", 0) or 0),
            "entry": row.get("entry"),
            "current": row.get("current"),
            "sl": row.get("sl"),
            "tp": row.get("tp"),
            "fav_r": row.get("fav_r"),
            "peak_fav_r": row.get("peak_fav_r"),
            "mae_r": row.get("mae_r"),
            "context": dict(row.get("context") or {}),
            "facts": facts,
            "evidence_digest": digest({"evidence": facts}),
        })
    refs = [item["request_ref"] for item in prepared]
    prompt = "\n".join([
        "Tu es Hermes, cortex principal de suivi des positions V14 sur MT5 DEMO.",
        "Evalue si la these reste intacte. Tu es consultatif et ne peux appeler aucun outil.",
        "Ne modifie jamais SL/TP, ne ferme rien et n'invente aucun fait.",
        "CALM=these intacte; CAUTION=affaiblie; FEAR=deterioration mecanique et fait independant; PANIC=choc ou invalidation severe; UNKNOWN=faits inutilisables.",
        "Reponds seulement en JSON minifie: {\"verdicts\":[{\"request_ref\":\"exact\",\"state\":\"CALM|CAUTION|FEAR|PANIC|UNKNOWN\",\"confidence\":0.0,\"reason\":\"francais max 180 caracteres\"}]}",
        json.dumps({"positions": prepared}, ensure_ascii=False, separators=(",", ":")),
    ])
    parsed = _ask(prompt)
    rows = parsed.get("verdicts")
    if not isinstance(rows, list):
        raise HermesCortexUnavailable("verdicts positions Hermes absent")
    by_ref = {str(row.get("request_ref", "")): row for row in rows if isinstance(row, dict)}
    if set(by_ref) != set(refs):
        raise HermesCortexUnavailable("liaison request_ref Hermes incomplete")
    rendered = datetime.now(timezone.utc).isoformat()
    answers = []
    for item in prepared:
        raw = by_ref[item["request_ref"]]
        state = str(raw.get("state", "UNKNOWN")).upper()
        if state not in {"CALM", "CAUTION", "FEAR", "PANIC", "UNKNOWN"}:
            state = "UNKNOWN"
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        answers.append({
            "request_ref": item["request_ref"],
            "ticket": item["ticket"],
            "symbol": item["symbol"],
            "state": state,
            "confidence": confidence,
            "reason": str(raw.get("reason", ""))[:240],
            "sources": sorted({fact["source"] for fact in item["facts"]}),
            "evidence_digest": item["evidence_digest"],
            "rendered_at": rendered,
            "model_version": MODEL_VERSION,
            "prompt_version": POSITION_PROMPT_VERSION,
            "source": HERMES_SOURCE,
        })
    return answers
