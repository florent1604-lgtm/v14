#!/usr/bin/env python
"""Attend la fin du backfill de rejeu, puis publie la cloture.

Un backfill de quinze heures se termine sans temoin : les lots meurent un a un
et le dossier cesse simplement de bouger. Trois choses doivent alors etre
faites dans le bon ordre, et personne n'est reveille pour les faire :

1. auditer les artefacts et verifier qu'ils sont tous de l'EPOQUE COURANTE ;
2. confronter la prediction publiee avant la porte de granularite -- seuls
   sept symboles doivent avoir change de chiffres ;
3. seulement ensuite, publier le classement de l'univers.

Ce veilleur ne fait que LIRE et lancer des outils de lecture. Il ne relance
aucun lot, n'ecrit aucun artefact, ne touche ni au trading ni au moteur : le
modifier ne perime aucun rejeu.

Il s'arrete aussi quand le run echoue (sentinelle), quand plus rien ne s'ecrit
(lots morts) ou au bout du delai maximal -- et il publie alors ce qu'il sait,
plutot que d'attendre indefiniment.

Usage :
    python tools/cloture_backfill_rejeu.py                    # veille + cloture
    python tools/cloture_backfill_rejeu.py --maintenant       # cloture directe
    python tools/cloture_backfill_rejeu.py --intervalle 600 --silence-h 3
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from tools import epoque_rejeu  # noqa: E402

REJEU = RACINE / "results" / "rejeu_univers"
REJEU_BRUT = RACINE / "results" / "rejeu_univers_brut"
HORS_UNIVERS = REJEU_BRUT / "_HORS_UNIVERS.json"
SENTINELLE = REJEU_BRUT / "_RUN_FAILED.json"
SORTIE = (RACINE / "collab" / "prime_agent" / "runs" / "cloture-rejeu-20260823")

PYTHON = RACINE / ".venv" / "Scripts" / "python.exe"

#: Sans nouvel artefact pendant ce delai, les lots sont consideres morts.
SILENCE_DEFAUT_S = 3 * 3600
#: Un symbole prend environ une heure par lot : au-dela, quelque chose cloche.
DELAI_MAX_DEFAUT_S = 24 * 3600


def _json(chemin: Path) -> dict | None:
    try:
        valeur = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return valeur if isinstance(valeur, dict) else None


def inventaire(ltf: str = "M15", racine: Path = RACINE) -> list[str]:
    """Univers a rejouer : un symbole par archive LTF."""
    dossier = Path(racine) / "results" / "barres" / ltf
    if not dossier.is_dir():
        return []
    return sorted(p.stem for p in dossier.glob("*.parquet"))


def etat_backfill(symboles: list[str], *, resumes: Path = REJEU,
                  brut: Path = REJEU_BRUT, hors_univers: dict | None = None,
                  sentinelle: bool = False, dernier_ecrit: float | None = None,
                  empreinte_courante: str | None = None) -> dict:
    """Etat du backfill, lu sur les seuls sceaux -- volontairement bon marche.

    La veille tourne pendant que huit lots saturent la machine : elle doit
    couter des millisecondes, pas deux minutes. Elle ne verifie donc que
    l'EPOQUE de chaque artefact ; l'audit semantique complet est fait une seule
    fois, a la cloture.

    Un artefact scelle par une generation precedente est du travail qui reste a
    faire, pas du travail fait.
    """
    hors_univers = hors_univers or {}
    resumes, brut = Path(resumes), Path(brut)
    courante = (empreinte_courante if empreinte_courante is not None
                else epoque_rejeu.empreinte_courante())
    termines = sorted(
        s for s in symboles
        if (resumes / f"{s}.json").is_file()
        and epoque_rejeu.empreinte_artefact(brut, s) == courante)
    hors = sorted(s for s in hors_univers if s not in termines)
    restants = sorted(s for s in symboles
                      if s not in termines and s not in hors)
    return {
        "cible": len(symboles),
        "termines": len(termines),
        "hors_univers": hors,
        "restants": restants,
        "sentinelle": bool(sentinelle),
        "dernier_ecrit": dernier_ecrit,
        "empreinte_moteur_courante": courante,
    }


def raison_arret(etat: dict, *, ecoule_s: float, silence_s: float,
                 delai_max_s: float, maintenant: float | None = None) -> str:
    """Pourquoi cesser d'attendre, "" s'il faut continuer."""
    if etat["sentinelle"]:
        return "sentinelle"
    if not etat["restants"]:
        return "termine"
    if ecoule_s >= delai_max_s:
        return "delai"
    dernier = etat.get("dernier_ecrit")
    if dernier is not None:
        instant = maintenant if maintenant is not None else time.time()
        if instant - dernier >= silence_s:
            return "silence"
    return ""


def dernier_ecrit(dossier: Path = REJEU) -> float | None:
    """Horodatage du resume le plus recent, None si le dossier est vide."""
    dossier = Path(dossier)
    instants = [p.stat().st_mtime for p in dossier.glob("*.json")]
    return max(instants) if instants else None


def _executer(commande: list[str]) -> dict:
    debut = time.time()
    processus = subprocess.run(
        [str(c) for c in commande], cwd=str(RACINE), capture_output=True,
        text=True, encoding="utf-8", errors="replace")
    return {
        "commande": " ".join(str(c) for c in commande),
        "code": processus.returncode,
        "sortie": processus.stdout or "",
        "erreur": processus.stderr or "",
        "secondes": round(time.time() - debut, 1),
    }


def python_projet() -> Path:
    return PYTHON if PYTHON.is_file() else Path(sys.executable)


def cloturer(raison: str, etat: dict, *, sortie: Path = SORTIE,
             min_symboles: int | None = None) -> dict:
    """Audit, validation de la prediction, puis classement -- dans cet ordre."""
    sortie = Path(sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    python = python_projet()

    etapes = [
        ("audit", [python, "-X", "utf8", RACINE / "tools" / "audit_rejeu_artefacts.py"]),
        ("validation", [python, "-X", "utf8",
                        RACINE / "tools" / "valider_predictions_granularite.py"]),
    ]
    classement = [python, "-X", "utf8",
                  RACINE / "tools" / "analyse_rejeu_univers.py"]
    if min_symboles is not None:
        classement += ["--min-symboles", str(min_symboles)]
    etapes.append(("classement", classement))

    resultats = {nom: _executer(commande) for nom, commande in etapes}
    rapport = {
        "schema_version": 1,
        "cloture_le": datetime.now(timezone.utc).isoformat(),
        "raison_arret": raison,
        "etat": etat,
        "etapes": resultats,
    }
    (sortie / "cloture.json").write_text(
        json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    (sortie / "cloture.md").write_text(_markdown(rapport), encoding="utf-8")
    return rapport


def _markdown(rapport: dict) -> str:
    etat = rapport["etat"]
    lignes = [
        "# Cloture du backfill de rejeu — epoque "
        f"{etat.get('empreinte_moteur_courante', '')[:16]}",
        "",
        f"Publie le {rapport['cloture_le']} par `tools/cloture_backfill_rejeu.py`.",
        f"Arret : **{rapport['raison_arret']}**.",
        "",
        "## Etat de l'univers",
        "",
        "```",
        f"cible          {etat['cible']}",
        f"termines       {etat['termines']}",
        f"hors univers   {len(etat['hors_univers'])} "
        f"{', '.join(etat['hors_univers'])}",
        f"restants       {len(etat['restants'])} "
        f"{', '.join(etat['restants'][:12])}",
        f"sentinelle     {etat['sentinelle']}",
        "```",
        "",
    ]
    titres = {"audit": "Audit des artefacts",
              "validation": "Prediction de la porte de granularite",
              "classement": "Classement de l'univers"}
    for nom, etape in rapport["etapes"].items():
        lignes += [f"## {titres.get(nom, nom)}", "",
                   f"`{etape['commande']}` — code {etape['code']}, "
                   f"{etape['secondes']} s", "", "```"]
        texte = (etape["sortie"] or etape["erreur"]).rstrip().splitlines()
        lignes += texte[-120:] or ["(aucune sortie)"]
        lignes += ["```", ""]
    lignes += [
        "## Ce que cette cloture ne fait pas",
        "",
        "Elle ne relance aucun lot, ne reecrit aucun artefact, ne modifie aucun",
        "seuil ni parametre de risque, et n'a aucune autorite d'execution.",
        "",
    ]
    return "\n".join(lignes)


def veiller(*, intervalle: float, silence_s: float, delai_max_s: float,
            sortie: Path, min_symboles: int | None,
            maintenant: bool = False) -> dict:
    debut = time.time()
    symboles = inventaire()
    while True:
        etat = etat_backfill(
            symboles, hors_univers=_json(HORS_UNIVERS) or {},
            sentinelle=SENTINELLE.is_file(), dernier_ecrit=dernier_ecrit())
        raison = "manuel" if maintenant else raison_arret(
            etat, ecoule_s=time.time() - debut, silence_s=silence_s,
            delai_max_s=delai_max_s)
        horodatage = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{horodatage}] termines {etat['termines']}/{etat['cible']} | "
              f"restants {len(etat['restants'])} | "
              f"hors univers {len(etat['hors_univers'])} | "
              f"arret: {raison or 'non'}", flush=True)
        if raison:
            return cloturer(raison, etat, sortie=sortie,
                            min_symboles=min_symboles)
        time.sleep(intervalle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--intervalle", type=float, default=300.0)
    ap.add_argument("--silence-h", type=float, default=SILENCE_DEFAUT_S / 3600)
    ap.add_argument("--max-h", type=float, default=DELAI_MAX_DEFAUT_S / 3600)
    ap.add_argument("--sortie", type=Path, default=SORTIE)
    ap.add_argument("--min-symboles", type=int, default=None)
    ap.add_argument("--maintenant", action="store_true",
                    help="cloture immediatement, sans attendre la fin des lots")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rapport = veiller(
        intervalle=args.intervalle, silence_s=args.silence_h * 3600,
        delai_max_s=args.max_h * 3600, sortie=args.sortie,
        min_symboles=args.min_symboles, maintenant=args.maintenant)
    print(f"cloture publiee : {Path(args.sortie) / 'cloture.md'}")
    codes = {nom: etape["code"] for nom, etape in rapport["etapes"].items()}
    print(f"codes: {codes}")
    return 0 if rapport["raison_arret"] in ("termine", "manuel") else 1


if __name__ == "__main__":
    raise SystemExit(main())
