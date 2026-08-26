#!/usr/bin/env python
"""Banc A/B préenregistré des entrées V14, strictement PAPER/DEMO.

Le module sépare physiquement la préparation ex ante de la lecture des issues.
Il n'importe aucun composant MT5 et ne modifie ni sélection, ni sizing, ni
configuration de trading. Une cohorte invalide, ouverte ou sous-dimensionnée
refuse la mesure au lieu de produire un résultat vide.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
SPEC_PAR_DEFAUT = RACINE / "config" / "banc_ab_variantes.json"

ANALYSIS_BLOCKED = "ANALYSIS_BLOCKED"
NOT_IDENTIFIABLE = "NOT_IDENTIFIABLE"
NOT_POWERED = "NOT_POWERED"
EXPLORATORY_MEASURED = "EXPLORATORY_MEASURED"

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class ContractError(ValueError):
    """La spécification ou la cohorte viole un contrat préenregistré."""


class OutcomeAccessError(ContractError):
    """Une issue a été demandée avant l'ouverture licite de la phase 2."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def load_spec(path: Path = SPEC_PAR_DEFAUT) -> dict[str, Any]:
    """Charge et valide le contrat versionné sans lire de donnée de résultat."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("schema_version") != "v14.banc-ab-entrees.spec.v1":
        raise ContractError("schema de specification incompatible")
    if spec.get("paper_demo_only") is not True:
        raise ContractError("le banc doit rester PAPER/DEMO")
    phases = spec.get("phases") or {}
    ex_ante = set(phases.get("ex_ante_fields") or [])
    resolution = set(phases.get("resolution_fields") or [])
    outcomes = set(phases.get("outcome_fields") or [])
    if not ex_ante or not outcomes or (ex_ante | resolution) & outcomes:
        raise ContractError("allowlists de phases invalides")
    secondary = spec.get("secondary_hypotheses") or {}
    if secondary.get("enabled") is not False:
        raise ContractError("les secondaires v1 doivent etre desactives")
    if secondary.get("cardinality") != 0 or secondary.get("items") != []:
        raise ContractError("famille BH v1 non vide")
    b_r = spec.get("b_r") or {}
    if b_r.get("normalize_by_weight_sum") is not False:
        raise ContractError("normalisation des poids interdite")
    if b_r.get("reallocate") is not False or b_r.get("optimize_r") is not False:
        raise ContractError("reallocation ou optimisation de r interdite")
    if b_r.get("r_endpoints") != [1.35, 2.81]:
        raise ContractError("bornes B(r) non preenregistrees")
    gate = (spec.get("primary") or {}).get("gate") or {}
    if not gate.get("variance_model") or not gate.get("source"):
        raise ContractError("source ou variance de MDE absente")
    if spec.get("states_priority") != [
        ANALYSIS_BLOCKED, NOT_IDENTIFIABLE, NOT_POWERED, EXPLORATORY_MEASURED,
    ]:
        raise ContractError("priorite des etats invalide")
    return spec


def spec_sha256(spec: Mapping[str, Any]) -> str:
    """Empreinte sémantique canonique de la spécification."""
    return _sha256(dict(spec))


def spec_file_sha256(path: Path = SPEC_PAR_DEFAUT) -> str:
    """Empreinte canonique du JSON sur disque, indépendante des fins de ligne."""
    return spec_sha256(json.loads(Path(path).read_text(encoding="utf-8")))


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        instant = value
    else:
        instant = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ContractError("horodatage sans fuseau")
    return instant.astimezone(timezone.utc)


class PhaseOneRow(Mapping[str, Any]):
    """Vue allowlistée qui rend tout accès à une issue impossible en phase 1."""

    def __init__(self, row: Mapping[str, Any], spec: Mapping[str, Any]):
        self._row = row
        phases = spec["phases"]
        self._outcomes = frozenset(phases["outcome_fields"])
        self._allowed = frozenset(
            list(phases["ex_ante_fields"]) + list(phases["resolution_fields"]),
        )

    def __getitem__(self, key: str) -> Any:
        if key in self._outcomes:
            raise OutcomeAccessError(f"champ d'issue interdit en phase 1: {key}")
        if key not in self._allowed:
            raise ContractError(f"champ hors allowlist phase 1: {key}")
        return self._row[key]

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._allowed & set(self._row)))

    def __len__(self) -> int:
        return len(self._allowed & set(self._row))

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


def cohort_id(identity: Mapping[str, Any]) -> str:
    """Identité scellée : toute politique, époque ou configuration la change."""
    fields = (
        "entry_policy", "execution_mode", "policy_epoch",
        "config_sha256", "code_sha256",
    )
    normalized = {field: identity.get(field) for field in fields}
    if any(value is None or str(value).strip() == "" for value in normalized.values()):
        raise ContractError("COHORT_IDENTITY_INCOMPLETE")
    for field in ("config_sha256", "code_sha256"):
        if not _SHA256.fullmatch(str(normalized[field])):
            raise ContractError("COHORT_IDENTITY_INCOMPLETE")
        normalized[field] = str(normalized[field]).lower()
    return _sha256(normalized)


def select_state(
    *,
    integrity_ok: bool,
    identifiable: bool,
    powered: bool,
    resolved: bool,
) -> str:
    """Applique la priorité exhaustive acceptée aux offsets 705/707."""
    if not integrity_ok or not resolved:
        return ANALYSIS_BLOCKED
    if not identifiable:
        return NOT_IDENTIFIABLE
    if not powered:
        return NOT_POWERED
    return EXPLORATORY_MEASURED


def _floor_decision(value: datetime, timeframe: str) -> datetime:
    minutes = {"M15": 15, "H1": 60, "H4": 240}.get(str(timeframe).upper())
    if minutes is None:
        raise ContractError(f"timeframe sans plancher preenregistre: {timeframe}")
    epoch_minutes = int(value.timestamp() // 60)
    floored = epoch_minutes - (epoch_minutes % minutes)
    return datetime.fromtimestamp(floored * 60, tz=timezone.utc)


def _decision_proxy(row: PhaseOneRow) -> datetime:
    left = min(parse_utc(row["ts_open"]), parse_utc(row["placed_at"]))
    return _floor_decision(left, str(row["timeframe"]))


def _blocked(reason: str, *, eligible: int = 0, closed: int = 0) -> dict[str, Any]:
    return {
        "state": ANALYSIS_BLOCKED,
        "reason": reason,
        "counts": {"eligible": eligible, "closed": closed, "open": eligible - closed},
        "gate": None,
        "mask_sha256": None,
        "cohort_id": None,
        "decision_ids": [],
    }


def prepare_phase_one(
    rows: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Scelle masque et gates sans jamais accéder aux champs d'issue."""
    try:
        sealed_cohort_id = cohort_id(identity)
    except ContractError:
        return _blocked("COHORT_IDENTITY_INCOMPLETE")

    decision_ids: list[str] = []
    proxies: list[datetime] = []
    symbols: set[str] = set()
    closed = 0
    four_p = 0
    seen: set[tuple[str, str, str]] = set()
    policy_epoch = str(identity["policy_epoch"])

    try:
        for raw in rows:
            row = PhaseOneRow(raw, spec)
            key = (policy_epoch, str(row["symbol"]), str(row["position_ticket"]))
            if key in seen:
                return _blocked("DUPLICATE_DECISION_ID", eligible=len(rows), closed=closed)
            seen.add(key)
            proxy = _decision_proxy(row)
            proxies.append(proxy)
            symbols.add(str(row["symbol"]))
            pillars = int(row["support_pillars"])
            if pillars not in (2, 3):
                raise ContractError("strate de piliers hors contrat")
            four_p += int(pillars == 3)
            is_closed = bool(row.get("closed_at")) and bool(row.get("ts_exit"))
            closed += int(is_closed)
            decision_ids.append("|".join(key))
    except (KeyError, TypeError, ValueError, ContractError) as exc:
        return _blocked(f"INVALID_EX_ANTE:{exc}", eligible=len(rows), closed=closed)

    total = len(rows)
    days = {instant.date().isoformat() for instant in proxies}
    gate_spec = spec["primary"]["gate"]
    gate = {
        "four_p": four_p,
        "total": total,
        "decision_days": len(days),
        "symbols": len(symbols),
    }
    gate["powered"] = (
        four_p >= int(gate_spec["min_four_p"])
        and total >= int(gate_spec["min_total"])
        and len(days) >= int(gate_spec["min_decision_days"])
        and len(symbols) >= int(gate_spec["min_symbols"])
    )
    resolved = closed == total
    state = select_state(
        integrity_ok=True, identifiable=True,
        powered=bool(gate["powered"]), resolved=resolved,
    )
    return {
        "state": state,
        "reason": "" if resolved else "OPEN_DECISIONS",
        "counts": {"eligible": total, "closed": closed, "open": total - closed},
        "gate": gate,
        "mask_sha256": _sha256(decision_ids),
        "cohort_id": sealed_cohort_id,
        "policy_epoch": policy_epoch,
        "decision_ids": decision_ids,
        "spec_sha256": spec_sha256(spec),
    }


def relative_b_delta(
    outcomes: Sequence[float],
    support_pillars: Sequence[int],
    *,
    r: float,
) -> float:
    """Calcule delta_r avec le dénominateur fixe n, sans normalisation."""
    if len(outcomes) != len(support_pillars) or not outcomes:
        raise ContractError("listes B(r) vides ou non appariees")
    if not math.isfinite(r) or not 1.35 <= r <= 2.81:
        raise ContractError("r hors enveloppe preenregistree")
    delta = 0.0
    for outcome, pillars in zip(outcomes, support_pillars, strict=True):
        value = float(outcome)
        if not math.isfinite(value) or int(pillars) not in (2, 3):
            raise ContractError("issue ou strate B(r) invalide")
        weight = r if int(pillars) == 3 else 1.0
        delta += value - value * weight
    return delta / len(outcomes)


def evaluate_phase_two(
    frozen: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Lit les issues seulement après passage de toutes les portes ex ante."""
    if frozen.get("state") != EXPLORATORY_MEASURED:
        raise OutcomeAccessError(
            f"lecture d'issue interdite dans l'etat {frozen.get('state')}",
        )
    if frozen.get("spec_sha256") != spec_sha256(spec):
        raise ContractError("specification modifiee apres gel")
    decision_ids = list(frozen.get("decision_ids") or [])
    if len(decision_ids) != len(rows):
        raise ContractError("masque et issues non apparies")
    policy_epoch = str(frozen.get("policy_epoch") or "")
    actual_ids = [
        "|".join((policy_epoch, str(row["symbol"]), str(row["position_ticket"])))
        for row in rows
    ]
    if actual_ids != decision_ids or _sha256(actual_ids) != frozen.get("mask_sha256"):
        raise ContractError("masque modifie apres gel")
    outcomes: list[float] = []
    pillars: list[int] = []
    for raw in rows:
        value = float(raw["pnl_r"])
        pillar = int(raw["support_pillars"])
        if not math.isfinite(value) or pillar not in (2, 3):
            raise ContractError("issue phase 2 invalide")
        outcomes.append(value)
        pillars.append(pillar)
    three_p = [value for value, pillar in zip(outcomes, pillars, strict=True) if pillar == 2]
    four_p = [value for value, pillar in zip(outcomes, pillars, strict=True) if pillar == 3]
    if not three_p or not four_p:
        raise ContractError("strate primaire vide")
    quality = sum(four_p) / len(four_p) - sum(three_p) / len(three_p)
    endpoints = spec["b_r"]["r_endpoints"]
    return {
        "status": EXPLORATORY_MEASURED,
        "spec_sha256": frozen["spec_sha256"],
        "cohort_id": frozen["cohort_id"],
        "mask_sha256": frozen["mask_sha256"],
        "primary": {"name": "H_quality", "delta_mean_pnl_r": quality},
        "b_r": [
            {"r": r, "delta_r": relative_b_delta(outcomes, pillars, r=float(r))}
            for r in endpoints
        ],
        "secondary": [],
    }


def purge_overlaps(
    training: Sequence[Mapping[str, Any]],
    *,
    validation_start: datetime,
    validation_end: datetime,
) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    """Retire tout intervalle train intersectant la fenêtre de validation."""
    start = parse_utc(validation_start)
    end = parse_utc(validation_end)
    if end < start:
        raise ContractError("fenetre de validation inversee")
    kept = []
    for row in training:
        left = parse_utc(row["decision_proxy_at"])
        right = parse_utc(row["ts_exit"])
        if right < left:
            raise ContractError("intervalle train inverse")
        overlaps = left <= end and right >= start
        if not overlaps:
            kept.append(row)
    return kept, {
        "before": len(training),
        "after": len(kept),
        "purged": len(training) - len(kept),
    }


def _write_json_atomic(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--spec", type=Path, default=SPEC_PAR_DEFAUT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measure", action="store_true")
    args = parser.parse_args(argv)

    spec = load_spec(args.spec)
    source = json.loads(args.cohort.read_text(encoding="utf-8"))
    rows = source.get("cohort") if isinstance(source, dict) else source
    if not isinstance(rows, list):
        raise ContractError("cohorte JSON invalide")
    identity = json.loads(args.identity.read_text(encoding="utf-8"))
    frozen = prepare_phase_one(rows, spec, identity)
    report = evaluate_phase_two(frozen, rows, spec) if args.measure else frozen
    _write_json_atomic(args.output, report)
    return 0 if report.get("state", report.get("status")) != ANALYSIS_BLOCKED else 2


if __name__ == "__main__":
    raise SystemExit(main())
