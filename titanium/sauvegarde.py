"""Sauvegarde tournante et verifiable des donnees de mesure V14.

`results/` est entierement ignore par git : c'est le seul endroit ou vivent les
clotures, les excursions, le journal des limites et les candidats. Une purge
malheureuse ou un disque qui ment effacerait des semaines de collecte sans
laisser de trace. Ce module copie ces fichiers dans un instantane horodate,
verifie chaque copie par empreinte, et ne garde que les N derniers.

Deux regles tenues ici :

1. **Une sauvegarde non verifiee n'est pas une sauvegarde.** Chaque copie est
   relue et son sha256 compare a la source. Un ecart leve `SauvegardeError` au
   lieu de rendre un instantane silencieusement corrompu.
2. **La rotation ne touche jamais l'instantane courant.** La purge s'applique
   apres ecriture du manifeste complet, donc un plantage en cours de copie
   laisse les anciennes sauvegardes intactes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

#: Fichiers de mesure sauvegardes par defaut, relatifs au dossier source.
FICHIERS_PAR_DEFAUT: tuple[str, ...] = (
    "trades.ndjson",
    "journal_rejets.ndjson",
    "excursions.ndjson",
    "shadow_prod.ndjson",
    "limit_lifecycle.ndjson",
    "candidats_grappe.ndjson",
    "avis_rendus.ndjson",
    "cout_llm.ndjson",
    "positions.json",
    "pending_limits.json",
    "grappes.json",
    "loop_heartbeat.json",
)

#: Nombre d'instantanes conserves par defaut.
RETENTION_PAR_DEFAUT = 24

TAILLE_BLOC = 1 << 20


class SauvegardeError(RuntimeError):
    """Une copie ne correspond pas a sa source, ou le manifeste est illisible."""


@dataclass(frozen=True)
class FichierSauvegarde:
    """Une entree du manifeste : ce qui a ete copie, et sa preuve."""

    nom: str
    octets: int
    sha256: str
    lignes: int | None


def empreinte(chemin: Path) -> str:
    """sha256 d'un fichier, lu par blocs (shadow_prod depasse les 6 Mo)."""
    digest = hashlib.sha256()
    with Path(chemin).open("rb") as flux:
        for bloc in iter(lambda: flux.read(TAILLE_BLOC), b""):
            digest.update(bloc)
    return digest.hexdigest()


def _compter_lignes(chemin: Path) -> int | None:
    """Nombre de lignes non vides d'un NDJSON ; None pour les autres formats."""
    if chemin.suffix != ".ndjson":
        return None
    total = 0
    with chemin.open("r", encoding="utf-8", errors="replace") as flux:
        for ligne in flux:
            if ligne.strip():
                total += 1
    return total


def _horodatage(maintenant: datetime | None = None) -> str:
    instant = maintenant or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sauvegarder(source: Path, destination: Path, *,
                fichiers: tuple[str, ...] = FICHIERS_PAR_DEFAUT,
                retention: int = RETENTION_PAR_DEFAUT,
                maintenant: datetime | None = None) -> dict:
    """Cree un instantane verifie et applique la rotation.

    Un fichier absent n'est pas une erreur : la collecte n'a pas forcement
    encore produit `journal_rejets.ndjson`. Il est simplement declare absent
    dans le manifeste, ce qui evite de confondre "pas encore ecrit" et
    "efface depuis la derniere sauvegarde".
    """
    source = Path(source)
    destination = Path(destination)
    dossier = destination / _horodatage(maintenant)
    dossier.mkdir(parents=True, exist_ok=False)

    copies: list[FichierSauvegarde] = []
    absents: list[str] = []
    for nom in fichiers:
        origine = source / nom
        if not origine.is_file():
            absents.append(nom)
            continue
        cible = dossier / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origine, cible)
        attendu = empreinte(origine)
        obtenu = empreinte(cible)
        if attendu != obtenu:
            raise SauvegardeError(
                f"copie corrompue pour {nom}: {attendu} attendu, {obtenu} obtenu")
        copies.append(FichierSauvegarde(
            nom=nom,
            octets=cible.stat().st_size,
            sha256=obtenu,
            lignes=_compter_lignes(cible),
        ))

    manifeste = {
        "cree_le": (maintenant or datetime.now(timezone.utc))
                   .astimezone(timezone.utc).isoformat(),
        "source": str(source),
        "fichiers": [asdict(copie) for copie in copies],
        "absents": absents,
        "octets_total": sum(copie.octets for copie in copies),
    }
    # Le manifeste est ecrit EN DERNIER : sa presence signe un instantane
    # complet. La rotation ne supprime que des dossiers manifestes.
    (dossier / "manifeste.json").write_text(
        json.dumps(manifeste, ensure_ascii=False, indent=2), encoding="utf-8")

    manifeste["dossier"] = str(dossier)
    manifeste["supprimes"] = [str(chemin) for chemin in
                              purger(destination, retention=retention)]
    return manifeste


def instantanes(destination: Path) -> list[Path]:
    """Instantanes COMPLETS, du plus ancien au plus recent.

    Un dossier sans manifeste est un instantane interrompu : il est ignore
    plutot que compte, sinon la rotation supprimerait une bonne sauvegarde
    pour garder une mauvaise.
    """
    destination = Path(destination)
    if not destination.is_dir():
        return []
    return sorted(
        (dossier for dossier in destination.iterdir()
         if dossier.is_dir() and (dossier / "manifeste.json").is_file()),
        key=lambda dossier: dossier.name,
    )


def purger(destination: Path, *, retention: int = RETENTION_PAR_DEFAUT) -> list[Path]:
    """Supprime les instantanes complets les plus anciens au-dela de la retention."""
    if retention <= 0:
        return []
    complets = instantanes(destination)
    surplus = complets[:-retention] if len(complets) > retention else []
    for dossier in surplus:
        shutil.rmtree(dossier, ignore_errors=True)
    return surplus


def verifier(dossier: Path) -> dict:
    """Relit un instantane et confronte chaque fichier a son empreinte."""
    dossier = Path(dossier)
    chemin_manifeste = dossier / "manifeste.json"
    if not chemin_manifeste.is_file():
        raise SauvegardeError(f"manifeste absent: {chemin_manifeste}")
    try:
        manifeste = json.loads(chemin_manifeste.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SauvegardeError(f"manifeste illisible: {exc}") from exc

    corrompus: list[str] = []
    manquants: list[str] = []
    for entree in manifeste.get("fichiers", []):
        cible = dossier / entree["nom"]
        if not cible.is_file():
            manquants.append(entree["nom"])
            continue
        if empreinte(cible) != entree["sha256"]:
            corrompus.append(entree["nom"])
    return {
        "dossier": str(dossier),
        "cree_le": manifeste.get("cree_le"),
        "verifies": len(manifeste.get("fichiers", [])),
        "corrompus": corrompus,
        "manquants": manquants,
        "ok": not corrompus and not manquants,
    }
