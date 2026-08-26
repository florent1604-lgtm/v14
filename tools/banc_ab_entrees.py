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
import random
import re
import tempfile
from collections import Counter
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


class SourceSealError(ContractError):
    """L'artefact, son manifeste ou son identité ne correspond pas au sceau."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    source = spec.get("source") or {}
    for field in ("artifact_sha256", "manifest_sha256"):
        if not _SHA256.fullmatch(str(source.get(field) or "")):
            raise ContractError(f"sceau source absent: {field}")
    inference = spec.get("inference") or {}
    bootstrap = inference.get("bootstrap") or {}
    if bootstrap.get("method") != "two_way_product_symbol_decision_day":
        raise ContractError("bootstrap two-way non preenregistre")
    if int(bootstrap.get("draws") or 0) <= 0 or bootstrap.get("seed") is None:
        raise ContractError("draws ou seed bootstrap invalides")
    return spec


def spec_sha256(spec: Mapping[str, Any]) -> str:
    """Empreinte sémantique canonique de la spécification."""
    return _sha256(dict(spec))


def spec_file_sha256(path: Path = SPEC_PAR_DEFAUT) -> str:
    """Empreinte canonique du JSON sur disque, indépendante des fins de ligne."""
    return spec_sha256(json.loads(Path(path).read_text(encoding="utf-8")))


def load_sealed_cohort(
    spec: Mapping[str, Any],
    *,
    root: Path = RACINE,
    artifact_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Charge une cohorte dont artefact, manifeste et identité sont scellés."""
    root = Path(root).resolve()
    source = spec["source"]
    declared_artifact = Path(source["artifact"])
    if not declared_artifact.is_absolute():
        declared_artifact = root / declared_artifact
    declared_artifact = declared_artifact.resolve()
    selected_artifact = Path(artifact_path or declared_artifact).resolve()
    if selected_artifact != declared_artifact:
        raise SourceSealError("ARTIFACT_PATH_MISMATCH")
    if not selected_artifact.is_file():
        raise SourceSealError("ARTIFACT_ABSENT")
    artifact_sha = _file_sha256(selected_artifact)
    if artifact_sha != source["artifact_sha256"]:
        raise SourceSealError("ARTIFACT_SHA256_MISMATCH")

    declared_manifest = Path(source["manifest"])
    if not declared_manifest.is_absolute():
        declared_manifest = root / declared_manifest
    declared_manifest = declared_manifest.resolve()
    if not declared_manifest.is_file():
        raise SourceSealError("MANIFEST_ABSENT")
    manifest_sha = _file_sha256(declared_manifest)
    if manifest_sha != source["manifest_sha256"]:
        raise SourceSealError("MANIFEST_SHA256_MISMATCH")

    artifact = json.loads(selected_artifact.read_text(encoding="utf-8"))
    manifest = json.loads(declared_manifest.read_text(encoding="utf-8"))
    schema = source["artifact_schema"]
    if artifact.get("schema_version") != schema or manifest.get("schema_version") != schema:
        raise SourceSealError("ARTIFACT_SCHEMA_MISMATCH")
    rows = artifact.get("cohort")
    if not isinstance(rows, list):
        raise SourceSealError("ARTIFACT_COHORT_INVALID")
    artifact_count = artifact.get("cohort_count")
    manifest_artifact = manifest.get("artifact") or {}
    if (
        artifact_count != len(rows)
        or manifest_artifact.get("cohort_count") != len(rows)
    ):
        raise SourceSealError("CARDINALITY_MISMATCH")
    if manifest_artifact.get("sha256") != artifact_sha:
        raise SourceSealError("MANIFEST_ARTIFACT_SHA256_MISMATCH")
    cutoff = source["through_closed_event_id"]
    if (
        artifact.get("through_closed_event_id") != cutoff
        or (manifest.get("cutoff") or {}).get("through_closed_event_id") != cutoff
    ):
        raise SourceSealError("CUTOFF_MISMATCH")

    identity = manifest.get("cohort_identity")
    if not isinstance(identity, dict):
        raise SourceSealError("COHORT_IDENTITY_INCOMPLETE")
    try:
        sealed_id = cohort_id(identity)
    except ContractError as exc:
        raise SourceSealError("COHORT_IDENTITY_INCOMPLETE") from exc
    expected_mode = str(identity["execution_mode"])
    if any(str(row.get("mode")) != expected_mode for row in rows):
        raise SourceSealError("MODE_MISMATCH")
    return rows, dict(identity), {
        "artifact_sha256": artifact_sha,
        "manifest_sha256": manifest_sha,
        "cohort_id": sealed_id,
        "through_closed_event_id": cutoff,
        "cohort_count": len(rows),
    }


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
        "eligible_indexes": [],
        "projection_sha256": None,
    }


def _phase_one_projection(
    raw: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    policy_epoch: str,
) -> tuple[dict[str, Any], datetime]:
    row = PhaseOneRow(raw, spec)
    proxy = _decision_proxy(row)
    fields = sorted(
        set(spec["phases"]["ex_ante_fields"])
        | set(spec["phases"]["resolution_fields"]),
    )
    projection = {field: row.get(field) for field in fields}
    projection.update({
        "policy_epoch": policy_epoch,
        "decision_proxy_at": proxy.isoformat(),
    })
    return projection, proxy


def prepare_phase_one(
    rows: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    cutoff: str | datetime | None = None,
) -> dict[str, Any]:
    """Scelle masque et gates sans jamais accéder aux champs d'issue."""
    try:
        sealed_cohort_id = cohort_id(identity)
    except ContractError:
        return _blocked("COHORT_IDENTITY_INCOMPLETE")

    decision_ids: list[str] = []
    projections: list[dict[str, Any]] = []
    eligible_indexes: list[int] = []
    proxies: list[datetime] = []
    symbols: set[str] = set()
    closed = 0
    four_p = 0
    seen: set[tuple[str, str, str]] = set()
    policy_epoch = str(identity["policy_epoch"])
    cutoff_at = parse_utc(cutoff) if cutoff is not None else None
    expected_mode = str(identity["execution_mode"])

    try:
        for index, raw in enumerate(rows):
            projection, proxy = _phase_one_projection(
                raw, spec, policy_epoch=policy_epoch,
            )
            if cutoff_at is not None and proxy > cutoff_at:
                continue
            if str(projection["mode"]) != expected_mode:
                raise ContractError("MODE_MISMATCH")
            key = (
                policy_epoch,
                str(projection["symbol"]),
                str(projection["position_ticket"]),
            )
            if key in seen:
                return _blocked(
                    "DUPLICATE_DECISION_ID", eligible=len(eligible_indexes) + 1,
                    closed=closed,
                )
            seen.add(key)
            eligible_indexes.append(index)
            projections.append(projection)
            proxies.append(proxy)
            symbols.add(str(projection["symbol"]))
            pillars = int(projection["support_pillars"])
            if pillars not in (2, 3):
                raise ContractError("strate de piliers hors contrat")
            four_p += int(pillars == 3)
            is_closed = bool(projection.get("closed_at")) and bool(projection.get("ts_exit"))
            closed += int(is_closed)
            decision_ids.append("|".join(key))
    except (KeyError, TypeError, ValueError, ContractError) as exc:
        return _blocked(
            f"INVALID_EX_ANTE:{exc}", eligible=len(eligible_indexes), closed=closed,
        )

    total = len(eligible_indexes)
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
        "mask_sha256": _sha256(projections),
        "projection_sha256": _sha256(projections),
        "cohort_id": sealed_cohort_id,
        "policy_epoch": policy_epoch,
        "decision_ids": decision_ids,
        "eligible_indexes": eligible_indexes,
        "cutoff": cutoff_at.isoformat() if cutoff_at else None,
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


def _quality(records: Sequence[Mapping[str, Any]]) -> float:
    three_p = [float(row["outcome"]) for row in records if int(row["pillars"]) == 2]
    four_p = [float(row["outcome"]) for row in records if int(row["pillars"]) == 3]
    if not three_p or not four_p:
        raise ContractError("strate primaire vide")
    return sum(four_p) / len(four_p) - sum(three_p) / len(three_p)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ContractError("bootstrap sans tirage valide")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _bootstrap_two_way(
    records: Sequence[Mapping[str, Any]],
    bootstrap_spec: Mapping[str, Any],
) -> dict[str, Any]:
    symbols = sorted({str(row["symbol"]) for row in records})
    days = sorted({str(row["decision_day"]) for row in records})
    draws = int(bootstrap_spec["draws"])
    seed = int(bootstrap_spec["seed"])
    confidence = float(bootstrap_spec["confidence"])
    rng = random.Random(seed)
    estimates = []
    for _ in range(draws):
        symbol_weights = Counter(rng.choice(symbols) for _ in symbols)
        day_weights = Counter(rng.choice(days) for _ in days)
        sums = {2: 0.0, 3: 0.0}
        counts = {2: 0, 3: 0}
        for row in records:
            multiplicity = (
                symbol_weights[str(row["symbol"])]
                * day_weights[str(row["decision_day"])]
            )
            if not multiplicity:
                continue
            pillar = int(row["pillars"])
            sums[pillar] += float(row["outcome"]) * multiplicity
            counts[pillar] += multiplicity
        if counts[2] and counts[3]:
            estimates.append(sums[3] / counts[3] - sums[2] / counts[2])
    alpha = (1.0 - confidence) / 2.0
    return {
        "method": bootstrap_spec["method"],
        "draws": draws,
        "valid_draws": len(estimates),
        "seed": seed,
        "confidence": confidence,
        "ci95": [_percentile(estimates, alpha), _percentile(estimates, 1.0 - alpha)],
    }


def _omit_sensitivity(
    records: Sequence[Mapping[str, Any]],
    *,
    field: str,
    label: str,
    full_effect: float,
) -> dict[str, Any]:
    effects = []
    for omitted in sorted({str(row[field]) for row in records}):
        retained = [row for row in records if str(row[field]) != omitted]
        try:
            effect = _quality(retained)
        except ContractError:
            effect = None
        effects.append({"omitted": omitted, "effect": effect})
    finite = [float(item["effect"]) for item in effects if item["effect"] is not None]
    reference_sign = 0 if full_effect == 0 else (1 if full_effect > 0 else -1)
    stable = bool(finite) and all(
        (0 if value == 0 else (1 if value > 0 else -1)) == reference_sign
        for value in finite
    )
    return {
        "method": label,
        "omissions": len(effects),
        "valid_omissions": len(finite),
        "sign_stable": stable,
        "min_effect": min(finite) if finite else None,
        "max_effect": max(finite) if finite else None,
        "effects": effects,
    }


def _walk_forward(
    records: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    days = sorted({str(row["decision_day"]) for row in records})
    requested = int(spec["inference"]["walk_forward"]["folds"])
    chunk_size = max(1, math.ceil(len(days) / requested))
    chunks = [days[index:index + chunk_size] for index in range(0, len(days), chunk_size)]
    folds = []
    for fold_index, validation_days in enumerate(chunks[1:], start=1):
        validation = [row for row in records if row["decision_day"] in validation_days]
        training = [row for row in records if row["decision_day"] < validation_days[0]]
        if not validation or not training:
            continue
        validation_start = min(parse_utc(row["decision_proxy_at"]) for row in validation)
        validation_end = max(parse_utc(row["ts_exit"]) for row in validation)
        retained, counts = purge_overlaps(
            training,
            validation_start=validation_start,
            validation_end=validation_end,
        )
        zero_overlap = all(
            parse_utc(row["ts_exit"]) < validation_start
            or parse_utc(row["decision_proxy_at"]) > validation_end
            for row in retained
        )
        try:
            effect = _quality(validation)
        except ContractError:
            effect = None
        folds.append({
            "fold": fold_index,
            "validation_days": validation_days,
            "validation_count": len(validation),
            "effect": effect,
            "purge": counts,
            "zero_overlap": zero_overlap,
        })
    return {
        "method": "calendar_walk_forward_exact_overlap_purge",
        "requested_folds": requested,
        "purge_applied": bool(folds),
        "folds": folds,
    }


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
    indexes = list(frozen.get("eligible_indexes") or [])
    policy_epoch = str(frozen.get("policy_epoch") or "")
    projections = [
        _phase_one_projection(rows[index], spec, policy_epoch=policy_epoch)[0]
        for index in indexes
    ]
    if _sha256(projections) != frozen.get("projection_sha256"):
        raise ContractError("projection phase 1 ou masque modifie apres gel")

    records = []
    outcomes: list[float] = []
    pillars: list[int] = []
    for index, projection in zip(indexes, projections, strict=True):
        raw = rows[index]
        value = float(raw["pnl_r"])
        pillar = int(projection["support_pillars"])
        if not math.isfinite(value) or pillar not in (2, 3):
            raise ContractError("issue phase 2 invalide")
        outcomes.append(value)
        pillars.append(pillar)
        records.append({
            "outcome": value,
            "pillars": pillar,
            "symbol": projection["symbol"],
            "decision_day": parse_utc(projection["decision_proxy_at"]).date().isoformat(),
            "decision_proxy_at": projection["decision_proxy_at"],
            "ts_exit": projection["ts_exit"],
        })
    quality = _quality(records)
    bootstrap = _bootstrap_two_way(records, spec["inference"]["bootstrap"])
    endpoints = spec["b_r"]["r_endpoints"]
    return {
        "status": EXPLORATORY_MEASURED,
        "spec_sha256": frozen["spec_sha256"],
        "cohort_id": frozen["cohort_id"],
        "mask_sha256": frozen["mask_sha256"],
        "primary": {
            "name": "H_quality",
            "status": EXPLORATORY_MEASURED,
            "delta_mean_pnl_r": quality,
            "mde": dict(spec["primary"]["gate"]),
            "bootstrap": bootstrap,
            "loso": _omit_sensitivity(
                records, field="symbol", label="LOSO", full_effect=quality,
            ),
            "lodo": _omit_sensitivity(
                records, field="decision_day", label="LODO", full_effect=quality,
            ),
        },
        "b_r": [
            {
                "r": r,
                "delta_r": relative_b_delta(outcomes, pillars, r=float(r)),
                "monetary_status": NOT_IDENTIFIABLE,
                "interpretation": "RELATIVE_SENSITIVITY_ONLY",
            }
            for r in endpoints
        ],
        "walk_forward": _walk_forward(records, spec),
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


def _sidecar_path(output: Path, state: str) -> Path:
    suffix = {
        ANALYSIS_BLOCKED: "blocked",
        NOT_IDENTIFIABLE: "not_identifiable",
        NOT_POWERED: "not_powered",
    }.get(state, "status")
    output = Path(output)
    return output.with_name(f"{output.stem}.{suffix}.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--spec", type=Path, default=SPEC_PAR_DEFAUT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--measure", action="store_true")
    args = parser.parse_args(argv)

    spec = load_spec(args.spec)
    source_path = Path(spec["source"]["artifact"])
    if source_path.is_absolute():
        root = source_path.parent
    else:
        repo_candidate = (RACINE / source_path).resolve()
        root = RACINE if repo_candidate == args.cohort.resolve() else args.spec.parent
    try:
        rows, identity, provenance = load_sealed_cohort(
            spec, root=root, artifact_path=args.cohort,
        )
        frozen = prepare_phase_one(rows, spec, identity, cutoff=args.cutoff)
        frozen["provenance"] = provenance
    except (SourceSealError, json.JSONDecodeError, OSError) as exc:
        frozen = _blocked(f"SOURCE_SEAL:{exc}")
        frozen["spec_sha256"] = spec_sha256(spec)

    state = str(frozen["state"])
    exit_codes = spec["cli_exit_codes"]
    if not args.measure:
        _write_json_atomic(args.output, frozen)
        return int(exit_codes[state])
    if state != EXPLORATORY_MEASURED:
        _write_json_atomic(_sidecar_path(args.output, state), frozen)
        return int(exit_codes[state])
    report = evaluate_phase_two(frozen, rows, spec)
    report["provenance"] = provenance
    report["cutoff"] = frozen["cutoff"]
    _write_json_atomic(args.output, report)
    return int(exit_codes[EXPLORATORY_MEASURED])


if __name__ == "__main__":
    raise SystemExit(main())
