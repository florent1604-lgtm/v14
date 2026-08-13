"""Scelle et analyse une cohorte d'excursions PAPER/DEMO."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MFE_BINS = (
    {"label": "< 0.05 R", "minimum": None, "maximum": 0.05},
    {"label": "0.05 à < 0.20 R", "minimum": 0.05, "maximum": 0.20},
    {"label": "0.20 à < 0.50 R", "minimum": 0.20, "maximum": 0.50},
    {"label": ">= 0.50 R", "minimum": 0.50, "maximum": None},
)
EXIT_REASONS = ("init", "breakeven", "trailing")


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_utc(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} invalide: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} doit inclure un fuseau")
    return parsed.astimezone(timezone.utc)


def _normalize_gaps(runtime_gaps: list[dict]) -> list[dict]:
    normalized = []
    for gap in runtime_gaps:
        start = _parse_utc(gap.get("start"), "runtime_gap.start")
        end = _parse_utc(gap.get("end"), "runtime_gap.end")
        if start >= end:
            raise ValueError("runtime_gap.start doit précéder runtime_gap.end")
        normalized.append({"start": start.isoformat(), "end": end.isoformat()})
    return sorted(normalized, key=lambda gap: (gap["start"], gap["end"]))


def _crosses_gap(row: dict, runtime_gaps: list[dict]) -> bool:
    opened = _parse_utc(row.get("ts_open"), "ts_open")
    closed = _parse_utc(row.get("ts_exit"), "ts_exit")
    if opened > closed:
        raise ValueError(f"timestamps inversés pour {row.get('ticket')}")
    return any(
        opened < _parse_utc(gap["end"], "runtime_gap.end")
        and closed > _parse_utc(gap["start"], "runtime_gap.start")
        for gap in runtime_gaps
    )


def _read_ndjson(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON invalide ligne {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"objet attendu ligne {line_number}")
        rows.append(row)
    return rows


def _seal_payload(artifact: dict) -> str:
    body = {key: value for key, value in artifact.items() if key != "seal_sha256"}
    return _sha256(_canonical_bytes(body))


def seal_excursions(
    source: Path,
    *,
    runtime_gaps: list[dict],
    through_ticket: str | None = None,
) -> dict:
    """Construit un artefact déterministe depuis des gaps fournis explicitement."""
    available_rows = _read_ndjson(source)
    selected_rows = available_rows
    if through_ticket is not None:
        indices = [index for index, row in enumerate(available_rows) if str(row.get("ticket")) == through_ticket]
        if len(indices) != 1:
            raise ValueError("through_ticket doit désigner exactement une ligne")
        selected_rows = available_rows[: indices[0] + 1]

    gaps = _normalize_gaps(runtime_gaps)
    clean: list[dict] = []
    excluded: list[dict] = []
    seen: set[str] = set()
    for source_row in selected_rows:
        ticket = str(source_row.get("ticket") or "")
        if not ticket or ticket in seen:
            raise ValueError(f"ticket absent ou dupliqué: {ticket!r}")
        seen.add(ticket)
        reason = str(source_row.get("exit_reason") or "")
        if reason not in EXIT_REASONS:
            raise ValueError(f"exit_reason non pris en charge pour {ticket}: {reason!r}")
        if not isinstance(source_row.get("pnl_r"), (int, float)):
            raise ValueError(f"pnl_r absent pour {ticket}")
        if not isinstance(source_row.get("mfe_r"), (int, float)):
            raise ValueError(f"mfe_r absent pour {ticket}")
        runtime_censored = _crosses_gap(source_row, gaps)
        row = {
            "ticket": ticket,
            "exit_reason": reason,
            "pnl_r": float(source_row["pnl_r"]),
            "mfe_r": float(source_row["mfe_r"]),
            "ts_open": _parse_utc(source_row["ts_open"], "ts_open").isoformat(),
            "ts_exit": _parse_utc(source_row["ts_exit"], "ts_exit").isoformat(),
            "censored": bool(source_row.get("censored", False)),
            "runtime_gap_censored": runtime_censored,
        }
        (excluded if runtime_censored else clean).append(row)

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "name": source.name,
            "sha256": _sha256(_canonical_bytes(selected_rows)),
            "through_ticket": through_ticket,
        },
        "runtime_gaps": gaps,
        "mfe_bins": [dict(item) for item in MFE_BINS],
        "counts": {
            "source": len(selected_rows),
            "clean": len(clean),
            "runtime_gap_censored": len(excluded),
        },
        "cohort": clean,
        "excluded": excluded,
    }
    artifact["seal_sha256"] = _seal_payload(artifact)
    return artifact


def write_json_deterministic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(value))


def load_sealed_artifact(path: Path) -> dict:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict) or artifact.get("seal_sha256") != _seal_payload(artifact):
        raise ValueError("SHA-256 de l'artefact invalide")
    return artifact


def _round(value: float) -> float:
    return round(value, 4)


def _bin_index(value: float, bins: list[dict]) -> int:
    matches = [
        index
        for index, item in enumerate(bins)
        if (item["minimum"] is None or value >= item["minimum"])
        and (item["maximum"] is None or value < item["maximum"])
    ]
    if len(matches) != 1:
        raise AssertionError(f"mfe_r {value} appartient à {len(matches)} bins")
    return matches[0]


def aggregate_clean_cohort(artifact: dict) -> dict:
    rows = artifact["cohort"]
    by_reason = {}
    for reason in EXIT_REASONS:
        values = [row["pnl_r"] for row in rows if row["exit_reason"] == reason]
        by_reason[reason] = {
            "n": len(values),
            "sum_pnl_r": _round(sum(values)),
            "mean_pnl_r": _round(sum(values) / len(values)) if values else None,
        }

    init_rows = [row for row in rows if row["exit_reason"] == "init"]
    bins = [{"label": item["label"], "n": 0} for item in artifact["mfe_bins"]]
    for row in init_rows:
        bins[_bin_index(row["mfe_r"], artifact["mfe_bins"])]["n"] += 1
    assert sum(item["n"] for item in bins) == len(init_rows), "sum(bins) != n"
    return {
        "n": len(rows),
        "sum_pnl_r": _round(sum(row["pnl_r"] for row in rows)),
        "by_exit_reason": by_reason,
        "init_mfe_bins": bins,
    }


def build_report(artifact: dict) -> str:
    """Rend le rapport uniquement depuis l'artefact vérifié en mémoire."""
    if artifact.get("seal_sha256") != _seal_payload(artifact):
        raise ValueError("SHA-256 de l'artefact invalide")
    analysis = aggregate_clean_cohort(artifact)
    lines = [
        "# Analyse des pertes — cohorte scellée et propre",
        "",
        "> Rapport généré par `tools/analyse_pertes.py`. Aucun nombre analytique n’est saisi manuellement.",
        "",
        f"Artefact SHA-256 : `{artifact['seal_sha256']}`.",
        f"Source scellée : `{artifact['source']['name']}` (`{artifact['source']['sha256']}`).",
        f"Borne du sceau : `{artifact['source']['through_ticket'] or 'fin de source'}`.",
        "",
        "## Trous runtime fournis",
        "",
        "| Début UTC | Fin UTC |",
        "|---|---|",
    ]
    lines.extend(
        f"| {gap['start']} | {gap['end']} |"
        for gap in artifact["runtime_gaps"]
    )
    if not artifact["runtime_gaps"]:
        lines.append("| aucun | aucun |")
    lines.extend([
        "",
        "## Cohorte",
        "",
        f"Lignes scellées : {artifact['counts']['source']}. Trajectoires exclues car elles traversent un trou runtime : {artifact['counts']['runtime_gap_censored']}. Cohorte propre : {analysis['n']}.",
        "",
        "| Sortie | n | Cumul | Moyenne |",
        "|---|---:|---:|---:|",
    ])
    for reason in EXIT_REASONS:
        item = analysis["by_exit_reason"][reason]
        mean = "n/a" if item["mean_pnl_r"] is None else f"{item['mean_pnl_r']:+.4f} R"
        lines.append(f"| {reason} | {item['n']} | {item['sum_pnl_r']:+.4f} R | {mean} |")
    lines.extend(["", "## MFE des stops initiaux", "", "| Bin | n |", "|---|---:|"])
    for item in analysis["init_mfe_bins"]:
        lines.append(f"| {item['label']} | {item['n']} |")
    init_n = analysis["by_exit_reason"]["init"]["n"]
    lines.append(f"| Somme vérifiée (`sum(bins) == n`) | {init_n} |")
    lines.extend([
        "",
        "## Conclusion",
        "",
        "Les pertes observées sont concentrées dans les stops initiaux ; l’hypothèse prioritaire concerne la sélection/entrée.",
        "",
        "Cette observation est descriptive, non causale. Les groupes sont définis par leur issue ; ils ne constituent pas un contrefactuel permettant d’attribuer le résultat à la politique de sortie.",
        "",
        "Le champ source `censored` est conservé séparément de `runtime_gap_censored` et n’est jamais utilisé comme substitut à la censure par trou runtime.",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excursions", type=Path, required=True)
    parser.add_argument("--runtime-epochs", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    epochs = json.loads(args.runtime_epochs.read_text(encoding="utf-8"))
    artifact = seal_excursions(
        args.excursions,
        runtime_gaps=epochs["runtime_gaps"],
        through_ticket=epochs.get("analysis_through_ticket"),
    )
    write_json_deterministic(args.artifact, artifact)
    sealed = load_sealed_artifact(args.artifact)
    args.report.write_text(build_report(sealed), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
