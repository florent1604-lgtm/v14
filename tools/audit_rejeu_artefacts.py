#!/usr/bin/env python
"""Audit technique ET semantique des artefacts du rejeu univers.

Un manifeste scelle peut parfaitement decrire un fichier vide. Cet outil est
la source commune du moniteur et des vues de progression : il ne considere un
artefact termine que si les sceaux, le resume et les compteurs de trades sont
coherents et non nuls.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools import epoque_rejeu  # noqa: E402


def _json(path: Path) -> dict | None:
    try:
        valeur = json.loads(path.read_text(encoding="utf-8"))
        return valeur if isinstance(valeur, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _baseline(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    resultat: dict[str, int] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fichier:
            for ligne in csv.DictReader(fichier):
                symbole = str(ligne.get("symbole") or "").strip()
                if symbole:
                    resultat[symbole] = int(float(ligne.get("n") or 0))
    except (OSError, TypeError, ValueError):
        return {}
    return resultat


def _fingerprint_moteur(manifeste: dict) -> str:
    """Empreinte du moteur scellee dans le manifeste; vide s'il n'y en a pas.

    La definition vit dans ``tools/epoque_rejeu.py`` : l'audit et l'analyse
    doivent trancher l'epoque d'un artefact avec la MEME regle.
    """
    return epoque_rejeu.empreinte_manifeste(manifeste)


def auditer(racine: Path = RACINE, ltf: str = "M15") -> dict:
    from tools import rejeu_univers as ru

    racine = Path(racine)
    archives = racine / "results" / "barres" / ltf
    resumes = racine / "results" / "rejeu_univers"
    bruts = racine / "results" / "rejeu_univers_brut"
    baseline_path = (racine / "collab" / "prime_agent" / "runs"
                     / "strategie-entree-20260819" / "rejeu_intermediaire_43.csv")
    baseline = _baseline(baseline_path)
    symboles = sorted(p.stem for p in archives.glob("*.parquet")) if archives.is_dir() else []

    details = []
    moteurs: Counter[str] = Counter()
    # Empreinte du moteur SUR DISQUE : la seule a laquelle un artefact doit
    # etre compare. Un dossier homogene mais perime est tout aussi trompeur
    # qu'un dossier melange.
    courant = epoque_rejeu.empreinte_courante()
    for symbole in symboles:
        resume_path = resumes / f"{symbole}.json"
        manifeste_path = bruts / symbole / "manifest.json"
        trades_path = bruts / symbole / "trades.ndjson"
        resume = _json(resume_path)
        manifeste = _json(manifeste_path)
        raisons: list[str] = []
        status = "missing"
        n = None
        empreinte = ""

        if resume is not None:
            try:
                n = int((resume.get("global") or {}).get("n"))
            except (TypeError, ValueError):
                raisons.append("summary_global_n_invalid")

        if resume is None and resume_path.exists():
            status = "invalid"
            raisons.append("summary_unreadable")
        elif manifeste is None and manifeste_path.exists():
            status = "invalid"
            raisons.append("manifest_unreadable")
        elif manifeste is None:
            if resume is not None and isinstance(n, int) and n > 0:
                status = "legacy"
            elif resume is not None:
                status = "invalid"
                raisons.append("legacy_zero_or_invalid")
        elif resume is None:
            status = "invalid"
            raisons.append("summary_missing")
        else:
            technique = ru.artefact_brut_valide(
                bruts, symbole, resume_path=resume_path)
            if not technique:
                raisons.append("seal_or_contract_invalid")
            try:
                compte = manifeste.get("counts") or {}
                n_manifeste = int(compte.get("trades"))
                n_cal = int(compte.get("calibration"))
                n_ver = int(compte.get("verification"))
                lignes = sum(1 for ligne in trades_path.read_bytes().splitlines()
                             if ligne.strip())
                if n != n_manifeste or n != lignes:
                    raisons.append("summary_manifest_trades_mismatch")
                if n_cal + n_ver != n_manifeste:
                    raisons.append("split_mismatch")
            except (OSError, TypeError, ValueError):
                raisons.append("counts_invalid")

            baseline_n = baseline.get(symbole, 0)
            if n == 0:
                raisons.append("zero_trades")
                if baseline_n > 0:
                    raisons.append("collapsed_vs_baseline")
            status = "accepted" if technique and not raisons else "invalid"
            empreinte = _fingerprint_moteur(manifeste)
            if empreinte:
                moteurs[empreinte] += 1

        details.append({
            "symbol": symbole,
            "status": status,
            "trades": n,
            "baseline_trades": baseline.get(symbole),
            "reasons": raisons,
            "engine_fingerprint": empreinte,
            # Scelle par une AUTRE version du moteur que celle presente sur
            # disque : l'artefact est techniquement valide et pourtant
            # incomparable aux suivants.
            "stale": bool(empreinte) and empreinte != courant,
        })

    comptes = Counter(item["status"] for item in details)
    invalides = [item["symbol"] for item in details if item["status"] == "invalid"]
    avertissements = []
    if invalides:
        avertissements.append(
            f"{len(invalides)} artefact(s) invalide(s): {', '.join(invalides[:12])}")
    if len(moteurs) > 1:
        avertissements.append(
            f"empreintes moteur mixtes: {len(moteurs)} generations")
    perimes = [item["symbol"] for item in details if item["stale"]]
    if perimes:
        avertissements.append(
            f"{len(perimes)} artefact(s) scelles par une autre generation que "
            f"le moteur courant {courant[:16]}: {', '.join(perimes[:12])}")
    if comptes["legacy"]:
        avertissements.append(
            f"{comptes['legacy']} resume(s) sans manifeste: aucune epoque, "
            "aucun trade brut, incomparables aux artefacts scelles")

    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ltf": ltf,
        "target": len(symboles),
        "counts": {
            "accepted": comptes["accepted"],
            "legacy": comptes["legacy"],
            "invalid": comptes["invalid"],
            "missing": comptes["missing"],
        },
        "engine_fingerprints": dict(moteurs),
        "engine_fingerprint_courant": courant,
        "stale": len([item for item in details if item["stale"]]),
        "warnings": avertissements,
        "details": details,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=RACINE)
    ap.add_argument("--ltf", default="M15")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rapport = auditer(args.root, args.ltf)
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=1))
    else:
        c = rapport["counts"]
        print(f"artefacts acceptes {c['accepted']}/{rapport['target']} | "
              f"legacy {c['legacy']} | invalides {c['invalid']} | manquants {c['missing']}")
        for avertissement in rapport["warnings"]:
            print(f"ALERTE: {avertissement}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
