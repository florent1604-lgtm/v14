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
import os
import shutil
import time
import uuid
from contextlib import contextmanager, suppress
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
    """Nom d'instantane a la MICROSECONDE.

    Une resolution a la seconde suffisait a ce que deux sauvegardes lancees
    dans la meme seconde se disputent le meme dossier : la seconde levait
    `FileExistsError` et ne sauvegardait rien. Le cas est reel — une tache
    planifiee et un appel manuel tombent volontiers ensemble.
    """
    instant = maintenant or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _identifiant(maintenant: datetime | None = None) -> str:
    """Nom ordonnable et unique, meme avec un instant injecte en test."""
    return f"{_horodatage(maintenant)}-{uuid.uuid4().hex}"


@contextmanager
def verrou(destination: Path, *, attente_s: float = 30.0,
           perime_s: float = 300.0):
    """Verrou INTER-PROCESSUS autour d'un instantane.

    `O_CREAT | O_EXCL` est atomique sur Windows comme sur POSIX : c'est la
    seule primitive disponible ici qui empeche deux processus — la boucle, une
    tache planifiee, un appel manuel — de copier en meme temps et de produire
    deux instantanes a moitie ecrits.

    Deux durees, et elles ne doivent PAS etre confondues :

    * `attente_s` — combien de temps j'accepte d'attendre mon tour ;
    * `perime_s` — a partir de quand je considere qu'un verrou a ete abandonne
      par un processus tue.

    Les melanger volait le verrou d'une sauvegarde vivante des que l'appelant
    etait presse. Un verrou perime est retire et l'on reprend la main ; un
    verrou vivant fait lever `SauvegardeError` plutot que de copier a deux.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    chemin = destination / ".verrou"
    debut = time.monotonic()
    fd = None
    while fd is None:
        try:
            fd = os.open(chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                perime = (time.time() - chemin.stat().st_mtime) > perime_s
            except OSError:
                perime = True          # verrou disparu entre-temps : on retente
            if perime:
                with suppress(OSError):
                    chemin.unlink()
                continue
            if time.monotonic() - debut > attente_s:
                raise SauvegardeError(
                    f"verrou de sauvegarde toujours pris apres {attente_s:.1f}s: "
                    f"{chemin}") from None
            time.sleep(0.05)
    try:
        os.write(fd, f"{os.getpid()}".encode())
    except OSError:
        pass
    finally:
        os.close(fd)
    try:
        yield chemin
    finally:
        with suppress(OSError):
            chemin.unlink()


def _empreinte_source(chemin: Path) -> tuple[int, int] | None:
    """(taille, mtime_ns) d'une source, ou None si absente."""
    try:
        st = chemin.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _metadonnees(empreinte_source: tuple[int, int] | None) -> dict | None:
    """Rend les releves avant/apres persistables dans le manifeste."""
    if empreinte_source is None:
        return None
    octets, mtime_ns = empreinte_source
    return {"octets": octets, "mtime_ns": mtime_ns}


def _lire_objet_json(chemin: Path, *, nom: str,
                      incoherences: list[str]) -> dict | None:
    """Lit un etat JSON sans fabriquer de donnees en cas de dommage."""
    if not chemin.is_file():
        return None
    try:
        valeur = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        incoherences.append(f"{nom}: JSON illisible ({type(exc).__name__})")
        return None
    if not isinstance(valeur, dict):
        incoherences.append(f"{nom}: racine non objet")
        return None
    return valeur


def _lire_ndjson(chemin: Path, *, nom: str,
                 incoherences: list[str]) -> list[dict] | None:
    """Lit un journal NDJSON; une ligne invalide rend le snapshot non coherent."""
    if not chemin.is_file():
        return None
    lignes: list[dict] = []
    try:
        contenu = chemin.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        incoherences.append(f"{nom}: lecture impossible ({type(exc).__name__})")
        return None
    for numero, brute in enumerate(contenu, start=1):
        if not brute.strip():
            continue
        try:
            ligne = json.loads(brute)
        except json.JSONDecodeError:
            incoherences.append(f"{nom}: ligne {numero} illisible")
            continue
        if not isinstance(ligne, dict):
            incoherences.append(f"{nom}: ligne {numero} non objet")
            continue
        lignes.append(ligne)
    return lignes


def _coherence_metier(dossier: Path) -> dict:
    """Controle les liens explicitement portes par les quatre artefacts.

    Ce n'est pas une reconstitution : un fichier absent reste inconnu et ne
    produit aucune donnee imaginaire. Quand les deux extremites sont presentes,
    un lien causal impossible est conserve dans le manifeste.
    """
    incoherences: list[str] = []
    positions = _lire_objet_json(dossier / "positions.json", nom="positions.json",
                                 incoherences=incoherences)
    pending = _lire_objet_json(dossier / "pending_limits.json",
                               nom="pending_limits.json", incoherences=incoherences)
    trades = _lire_ndjson(dossier / "trades.ndjson", nom="trades.ndjson",
                          incoherences=incoherences)
    lifecycle = _lire_ndjson(dossier / "limit_lifecycle.ndjson",
                             nom="limit_lifecycle.ndjson", incoherences=incoherences)

    tickets_trades: set[str] | None = None
    if trades is not None:
        tickets_trades = set()
        for ligne in trades:
            ticket = str(ligne.get("ticket", "") or "")
            if not ticket:
                incoherences.append("trades.ndjson: ticket absent")
                continue
            tickets_trades.add(ticket)

    filled: set[tuple[str, str]] = set()
    terminaux: set[str] = set()
    if lifecycle is not None:
        for ligne in lifecycle:
            evenement = str(ligne.get("event", "") or "").strip().lower()
            ordre = str(ligne.get("order_ticket", "") or "")
            attendu = f"{ordre}:{evenement}" if ordre and evenement else ""
            if not ordre or not evenement or ligne.get("event_id") != attendu:
                incoherences.append("limit_lifecycle.ndjson: event_id incoherent")
                continue
            position = str(ligne.get("position_ticket", "") or "")
            if evenement == "filled" and position:
                filled.add((ordre, position))
            if evenement in {"filled", "expired", "canceled", "closed"}:
                terminaux.add(ordre)
            if (evenement == "closed" and tickets_trades is not None and position
                    and f"live:{position}" not in tickets_trades):
                incoherences.append(
                    f"limit_lifecycle.ndjson: cloture {ordre} sans trade live:{position}")

    if positions is not None:
        for ticket, etat in positions.items():
            if not isinstance(etat, dict):
                incoherences.append(f"positions.json: position {ticket} non objet")
                continue
            ordre = str(etat.get("limit_order_ticket", "") or "")
            if ordre and lifecycle is not None and (ordre, str(ticket)) not in filled:
                incoherences.append(
                    f"positions.json: position {ticket} sans filled pour ordre {ordre}")
    if pending is not None and lifecycle is not None:
        for ordre in pending:
            if str(ordre) in terminaux:
                incoherences.append(
                    f"pending_limits.json: ordre {ordre} aussi terminal dans lifecycle")

    return {"ok": not incoherences, "incoherences": incoherences}


def sauvegarder(source: Path, destination: Path, *,
                fichiers: tuple[str, ...] = FICHIERS_PAR_DEFAUT,
                retention: int = RETENTION_PAR_DEFAUT,
                maintenant: datetime | None = None,
                attente_verrou_s: float = 30.0,
                verrou_perime_s: float = 300.0) -> dict:
    """Cree un instantane verifie, verrouille et qualifie, puis applique la rotation.

    Un fichier absent n'est pas une erreur : la collecte n'a pas forcement
    encore produit `journal_rejets.ndjson`. Il est simplement declare absent
    dans le manifeste, ce qui evite de confondre "pas encore ecrit" et
    "efface depuis la derniere sauvegarde".

    CE QUE CET INSTANTANE GARANTIT, ET CE QU'IL NE GARANTIT PAS
    ------------------------------------------------------------
    Chaque copie est relue et comparee a sa source : elle est fidele AU MOMENT
    de sa copie. Mais la boucle continue d'ecrire pendant ce temps, et douze
    fichiers ne sont pas copies au meme instant. Une cloture peut donc entrer
    dans `trades.ndjson` apres sa copie et avant celle de `positions.json`.

    C'est une sauvegarde **crash-consistante**, pas un instantane
    transactionnel. Plutot que de le laisser croire, on le mesure : la taille
    et la date de chaque source sont relevees avant ET apres, et toute source
    qui a bouge pendant la copie est nommee dans `sources_modifiees_pendant`,
    avec `coherent` a faux. Un instantane incoherent reste utilisable pour
    restaurer un fichier ; il ne doit pas servir a raisonner sur l'etat
    conjoint de plusieurs fichiers.
    """
    source = Path(source)
    destination = Path(destination)

    with verrou(destination, attente_s=attente_verrou_s,
                perime_s=verrou_perime_s):
        identifiant = _identifiant(maintenant)
        dossier = destination / identifiant
        staging = destination / ".staging" / identifiant
        staging.mkdir(parents=True, exist_ok=False)

        avant = {nom: _empreinte_source(source / nom) for nom in fichiers}

        copies: list[FichierSauvegarde] = []
        absents: list[str] = []
        mouvantes: list[str] = []
        for nom in fichiers:
            origine = source / nom
            if not origine.is_file():
                absents.append(nom)
                continue
            cible = staging / nom
            cible.parent.mkdir(parents=True, exist_ok=True)
            # Trois empreintes, parce que deux ne suffisent pas a distinguer
            # une copie corrompue d'une source qui bouge. La boucle ecrit
            # pendant la sauvegarde : confondre les deux ferait echouer tout
            # l'instantane precisement quand le systeme travaille, c'est-a-dire
            # quand on en a le plus besoin.
            avant_copie = empreinte(origine)
            shutil.copy2(origine, cible)
            obtenu = empreinte(cible)
            apres_copie = empreinte(origine)
            if obtenu != avant_copie:
                if avant_copie == apres_copie:
                    # La source n'a pas bouge : l'ecart vient donc de la copie.
                    raise SauvegardeError(
                        f"copie corrompue pour {nom}: {avant_copie} attendu, "
                        f"{obtenu} obtenu")
                # La source a bouge pendant la copie. Pour un NDJSON en ajout,
                # la copie reste un prefixe exploitable ; on la garde et on le
                # DIT, au lieu de perdre l'instantane entier.
                mouvantes.append(nom)
            copies.append(FichierSauvegarde(
                nom=nom,
                octets=cible.stat().st_size,
                sha256=obtenu,
                lignes=_compter_lignes(cible),
            ))

        apres = {nom: _empreinte_source(source / nom) for nom in fichiers}
        bouges = sorted(
            set(mouvantes) | {nom for nom in fichiers if avant[nom] != apres[nom]})

        coherence_metier = _coherence_metier(staging)
        manifeste = {
            "cree_le": (maintenant or datetime.now(timezone.utc))
                       .astimezone(timezone.utc).isoformat(),
            "source": str(source),
            "fichiers": [asdict(copie) for copie in copies],
            "absents": absents,
            "octets_total": sum(copie.octets for copie in copies),
            "sources_avant": {nom: _metadonnees(valeur)
                              for nom, valeur in avant.items()},
            "sources_apres": {nom: _metadonnees(valeur)
                              for nom, valeur in apres.items()},
            # Qualification explicite plutot que promesse implicite.
            "garantie": "crash-consistant",
            "coherent": not bouges and coherence_metier["ok"],
            "sources_modifiees_pendant": bouges,
            "coherence_metier": coherence_metier,
        }
        # Le manifeste est ecrit EN DERNIER : sa presence signe un instantane
        # complet. La rotation ne supprime que des dossiers manifestes.
        (staging / "manifeste.json").write_text(
            json.dumps(manifeste, ensure_ascii=False, indent=2), encoding="utf-8")

        # Le staging et le manifeste complet rendent l'instantane publiable en
        # une operation atomique sur le meme volume. Si le processus tombe
        # avant, il reste ignore sous `.staging`, jamais presente comme backup.
        staging.replace(dossier)

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
