#!/usr/bin/env python
"""Verifie la prediction publiee avant la porte de granularite reelle.

Le 23/08, avant de relancer le backfill sous le moteur ``051f50ad``, une
prediction verifiable a ete ecrite : la porte de granularite ne doit changer
les chiffres que de sept symboles -- ceux dont le prefixe HTF tombe sous les
400 barres necessaires a l'amorcage, plus les deux sortis de l'univers. Les
autres doivent retomber a l'identique, car une troncature qui laisse assez de
prefixe est neutre.

Une prediction qui n'est pas confrontee a la mesure n'est qu'une intention.
Cet outil fait la confrontation, symbole par symbole, sur les chiffres de
RESULTAT et non sur ceux de FENETRE : la fenetre est justement ce que la porte
deplace, la mesurer comme un ecart ferait echouer la verification par
construction.

Il ne fait que LIRE des artefacts. Il n'appartient pas a ``FICHIERS_MOTEUR`` :
le modifier ne perime aucun rejeu.

Usage :
    python tools/valider_predictions_granularite.py
    python tools/valider_predictions_granularite.py --json
    python tools/valider_predictions_granularite.py --reference <dossier>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from tools import epoque_rejeu  # noqa: E402

REJEU = RACINE / "results" / "rejeu_univers"
REJEU_BRUT = RACINE / "results" / "rejeu_univers_brut"
HORS_UNIVERS = REJEU_BRUT / "_HORS_UNIVERS.json"
REFERENCE = (RACINE / "collab" / "prime_agent" / "runs"
             / "artefacts-rejeu-20260822" / "avant_granularite")
SORTIE = RACINE / "results" / "validation_granularite.json"

#: Les sept symboles dont le prefixe HTF tombe sous les 400 barres d'amorcage
#: apres la porte (GER40 375, COCOA.fs 100, COFFEE.fs 86, IT40 85, SPA35 93,
#: USDCLP 0, USDCOP 0). Eux seuls peuvent changer de chiffres.
ATTENDUS = ("COCOA.fs", "COFFEE.fs", "GER40", "IT40", "SPA35",
            "USDCLP", "USDCOP")

#: Chiffres de RESULTAT : ce que la porte ne doit pas deplacer.
CHAMPS_RACINE = ("n_enter", "barres_evaluees", "erreurs", "coupure")
SEGMENTS = ("global", "calibration", "verification")
MESURES = ("n", "esperance_r", "ecart_type_r", "winrate", "profit_factor",
           "somme_r")

#: Chiffres de FENETRE : la porte les deplace par construction, ils sont
#: rapportes pour lecture et n'entrent pas dans le verdict.
CHAMPS_FENETRE = ("debut", "fin", "barres_ltf")

#: Deux esperances en R ne se distinguent pas en dessous : le rejeu publie
#: six decimales.
TOLERANCE = 1e-9


def _json(chemin: Path) -> dict | None:
    try:
        valeur = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return valeur if isinstance(valeur, dict) else None


def _ecart(avant, apres) -> bool:
    """Vrai si les deux valeurs different vraiment."""
    if isinstance(avant, bool) or isinstance(apres, bool):
        return avant != apres
    if isinstance(avant, (int, float)) and isinstance(apres, (int, float)):
        return abs(float(avant) - float(apres)) > TOLERANCE
    return avant != apres


def comparer(reference: dict, courant: dict,
             champs: tuple[str, ...] = CHAMPS_RACINE,
             *, segments: bool = True) -> list[dict]:
    """Ecarts entre deux resumes, champ par champ.

    ``segments=False`` limite la comparaison aux champs demandes : la liste des
    ecarts de FENETRE ne doit pas rejouer les mesures de RESULTAT, sans quoi un
    lecteur croit voir deux constats la ou il n'y en a qu'un.
    """
    ecarts: list[dict] = []
    for champ in champs:
        avant, apres = reference.get(champ), courant.get(champ)
        if _ecart(avant, apres):
            ecarts.append({"champ": champ, "avant": avant, "apres": apres})
    if not segments:
        return ecarts
    for segment in SEGMENTS:
        bloc_avant = reference.get(segment) or {}
        bloc_apres = courant.get(segment) or {}
        for mesure in MESURES:
            avant, apres = bloc_avant.get(mesure), bloc_apres.get(mesure)
            if _ecart(avant, apres):
                ecarts.append({"champ": f"{segment}.{mesure}",
                               "avant": avant, "apres": apres})
    return ecarts


def registre_hors_univers(chemin: Path = HORS_UNIVERS) -> dict:
    return _json(chemin) or {}


def valider(*, reference: Path = REFERENCE, rejeu: Path = REJEU,
            brut: Path = REJEU_BRUT, hors_univers: Path = HORS_UNIVERS,
            attendus: tuple[str, ...] = ATTENDUS,
            empreinte_courante: str | None = None) -> dict:
    """Confronte la prediction aux artefacts de l'epoque courante."""
    reference, rejeu, brut = Path(reference), Path(rejeu), Path(brut)
    courante = (empreinte_courante if empreinte_courante is not None
                else epoque_rejeu.empreinte_courante())
    hors = registre_hors_univers(hors_univers)

    symboles = sorted({p.stem for p in reference.glob("*.json")}
                      | {p.stem for p in rejeu.glob("*.json")})
    details: list[dict] = []
    for symbole in symboles:
        ref = _json(reference / f"{symbole}.json")
        cur = _json(rejeu / f"{symbole}.json")
        epoque = epoque_rejeu.empreinte_artefact(brut, symbole)
        entree = {"symbole": symbole, "attendu": symbole in attendus,
                  "ecarts": [], "fenetre": []}
        if symbole in hors:
            entree["statut"] = "hors_univers"
            entree["raison"] = (hors.get(symbole) or {}).get("raison", "")
        elif epoque != courante:
            entree["statut"] = "en_attente"
        elif cur is None:
            entree["statut"] = "manquant"
        elif ref is None:
            entree["statut"] = "sans_reference"
        else:
            entree["ecarts"] = comparer(ref, cur)
            entree["fenetre"] = comparer(ref, cur, champs=CHAMPS_FENETRE,
                                         segments=False)
            entree["statut"] = "change" if entree["ecarts"] else "identique"
        details.append(entree)

    par_statut: dict[str, list[str]] = {}
    for entree in details:
        par_statut.setdefault(entree["statut"], []).append(entree["symbole"])

    changes = par_statut.get("change", [])
    inattendus = sorted(s for s in changes if s not in attendus)
    attendus_inchanges = sorted(
        s for s in par_statut.get("identique", []) if s in attendus)
    en_attente = par_statut.get("en_attente", [])
    manquants = par_statut.get("manquant", []) + par_statut.get("sans_reference", [])

    if inattendus:
        verdict = "NON_CONFORME"
    elif en_attente:
        verdict = "PARTIEL"
    else:
        verdict = "CONFORME"

    avertissements: list[str] = []
    if inattendus:
        avertissements.append(
            f"{len(inattendus)} symbole(s) hors prediction ont change: "
            + ", ".join(inattendus[:12]))
    if attendus_inchanges:
        avertissements.append(
            "prediction trop large, ces symboles attendus n'ont pas bouge: "
            + ", ".join(attendus_inchanges))
    if manquants:
        avertissements.append(
            "artefacts sans vis-a-vis: " + ", ".join(sorted(manquants)[:12]))

    return {
        "schema_version": 1,
        "verifie_le": datetime.now(timezone.utc).isoformat(),
        "empreinte_moteur_courante": courante,
        "reference": str(reference),
        "attendus": list(attendus),
        "verdict": verdict,
        "comptes": {statut: len(symboles_)
                    for statut, symboles_ in sorted(par_statut.items())},
        "inattendus": inattendus,
        "attendus_inchanges": attendus_inchanges,
        "en_attente": sorted(en_attente),
        "avertissements": avertissements,
        "details": details,
    }


def resumer(rapport: dict) -> str:
    lignes = [
        f"validation granularite : {rapport['verdict']} "
        f"(moteur {rapport['empreinte_moteur_courante'][:16]})",
        "  " + " | ".join(f"{statut} {compte}"
                          for statut, compte in rapport["comptes"].items()),
    ]
    for entree in rapport["details"]:
        if entree["statut"] != "change":
            continue
        marque = "attendu" if entree["attendu"] else "INATTENDU"
        lignes.append(f"  {entree['symbole']} [{marque}] "
                      f"{len(entree['ecarts'])} ecart(s)")
        for ecart in entree["ecarts"][:6]:
            lignes.append(f"      {ecart['champ']}: "
                          f"{ecart['avant']} -> {ecart['apres']}")
    for avertissement in rapport["avertissements"]:
        lignes.append(f"ALERTE: {avertissement}")
    return "\n".join(lignes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reference", type=Path, default=REFERENCE)
    ap.add_argument("--rejeu", type=Path, default=REJEU)
    ap.add_argument("--brut", type=Path, default=REJEU_BRUT)
    ap.add_argument("--sortie", type=Path, default=SORTIE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sans-ecriture", action="store_true")
    args = ap.parse_args()

    rapport = valider(reference=args.reference, rejeu=args.rejeu,
                      brut=args.brut)
    if not args.sans_ecriture:
        args.sortie.parent.mkdir(parents=True, exist_ok=True)
        args.sortie.write_text(
            json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rapport, ensure_ascii=False, indent=1) if args.json
          else resumer(rapport))
    return 0 if rapport["verdict"] != "NON_CONFORME" else 1


if __name__ == "__main__":
    raise SystemExit(main())
