#!/usr/bin/env python
"""Epoque d'un artefact de rejeu : l'empreinte du moteur qui l'a produit.

Un resultat de rejeu ne veut rien dire hors du code qui l'a calcule. Deux
artefacts produits par deux versions du moteur ne se comparent pas, ne se
classent pas ensemble et ne se moyennent pas : le classement obtenu decrirait
alors l'historique des commits, pas les actifs.

``results/rejeu_univers`` est un dossier vivant. Un backfill le remplit symbole
par symbole pendant des heures et les resumes de la generation precedente y
restent jusqu'a leur reecriture. Sans borne d'epoque, toute lecture du dossier
melange les generations en silence.

Ce module est la source unique de cette empreinte, pour l'audit d'artefacts
comme pour l'analyse. Il ne fait que LIRE : il ne touche a aucun artefact.

Il n'appartient volontairement pas a ``FICHIERS_MOTEUR`` : le modifier ne doit
jamais perimer un rejeu en cours.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))


def empreinte(engine: list | None) -> str:
    """Empreinte d'un bloc ``engine``; chaine vide si le bloc est absent."""
    if not engine:
        return ""
    brut = json.dumps(
        engine, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(brut).hexdigest()


def bloc_moteur_courant() -> list[dict]:
    """Bloc ``engine`` des fichiers moteur tels qu'ils sont SUR DISQUE.

    ``tools.rejeu_univers`` est importe pour sa seule liste de fichiers et
    n'est jamais modifie : ses octets entrent dans le ``snapshot_id`` de tout
    artefact, donc y toucher perime le rejeu en cours.
    """
    from tools import rejeu_univers as ru

    return [
        {
            "name": ru._nom_stable(chemin),
            "bytes": chemin.stat().st_size,
            "sha256": ru._sha256_fichier(chemin),
        }
        for chemin in sorted(ru.FICHIERS_MOTEUR, key=lambda p: ru._nom_stable(p))
    ]


def empreinte_courante() -> str:
    """Empreinte du moteur present sur disque."""
    return empreinte(bloc_moteur_courant())


def empreinte_manifeste(manifeste: dict | None) -> str:
    """Empreinte scellee dans un manifeste brut; chaine vide sans manifeste."""
    if not isinstance(manifeste, dict):
        return ""
    return empreinte((manifeste.get("snapshot") or {}).get("engine"))


def empreinte_artefact(racine: Path, symbole: str) -> str:
    """Empreinte de l'artefact brut d'un symbole; chaine vide si illisible.

    Un resume sans manifeste vient d'une generation anterieure au scellement :
    il n'est rattachable a aucun moteur, donc a aucune epoque.
    """
    chemin = Path(racine) / symbole / "manifest.json"
    try:
        manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return empreinte_manifeste(manifeste)


class EpoqueCorpusError(RuntimeError):
    """Le corpus demande ne porte pas une generation unique et lisible.

    Cette erreur est un ECHEC FERME : elle interdit la mesure au lieu de
    rendre un rapport vide. Un rapport a zero symbole n'est pas un resultat
    nul, c'est une mesure qui n'a pas eu lieu.
    """

    def __init__(self, motif: str, detail: dict | None = None) -> None:
        self.motif = motif
        self.detail = dict(detail or {})
        super().__init__(f"{motif}: {json.dumps(self.detail, sort_keys=True)}")


def _symboles_corpus(racine: Path, symboles: list[str] | None) -> list[str]:
    if symboles is not None:
        return sorted(dict.fromkeys(str(s) for s in symboles))
    try:
        return sorted(p.name for p in Path(racine).iterdir() if p.is_dir())
    except OSError:
        return []


def manifestes_corpus(racine: Path, symboles: list[str] | None = None) -> list[dict]:
    """Manifestes du corpus DEMANDE, avec leur generation, ou echec ferme.

    Le corpus est la liste explicitement demandee, jamais le contenu du
    dossier quand une liste est fournie : un symbole hors demande ne doit ni
    sauver ni casser une mesure.
    """
    noms = _symboles_corpus(racine, symboles)
    if not noms:
        raise EpoqueCorpusError("CORPUS_VIDE", {"racine": str(racine)})
    retenus: list[dict] = []
    illisibles: list[str] = []
    sans_epoque: list[str] = []
    orphelins: list[str] = []
    for symbole in noms:
        chemin = Path(racine) / symbole / "manifest.json"
        try:
            octets = chemin.read_bytes()
            manifeste = json.loads(octets.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            illisibles.append(symbole)
            continue
        if not isinstance(manifeste, dict):
            illisibles.append(symbole)
            continue
        signee = empreinte_manifeste(manifeste)
        if not signee:
            sans_epoque.append(symbole)
            continue
        # Le sceau porte sur le COUPLE manifeste + donnees : un manifeste
        # orphelin certifierait un vide. La verification complete des octets
        # reste au validateur du rejeu; ici on refuse l'absence et la taille
        # incoherente, qui sont bon marche et suffisent a l'orphelin.
        declare = manifeste.get("trades") if isinstance(
            manifeste.get("trades"), dict) else {}
        donnees = Path(racine) / symbole / str(
            declare.get("name") or "trades.ndjson")
        try:
            taille = donnees.stat().st_size
        except OSError:
            orphelins.append(symbole)
            continue
        if declare.get("bytes") is not None and int(declare["bytes"]) != taille:
            orphelins.append(symbole)
            continue
        retenus.append({
            "symbol": symbole,
            "manifest_sha256": str(manifeste.get("manifest_sha256", "") or ""),
            # Octets REELLEMENT lus pour trancher l'epoque : sans eux, le
            # rapport scelle une decision prise sur un fichier qu'il ne
            # decrit pas (TOCTOU, reserve Hermes A.6).
            "manifest_bytes_sha256": hashlib.sha256(octets).hexdigest(),
            "engine_epoch": signee,
        })
    if illisibles:
        raise EpoqueCorpusError("MANIFESTE_ILLISIBLE", {"symboles": illisibles})
    if sans_epoque:
        raise EpoqueCorpusError("EPOQUE_ABSENTE", {"symboles": sans_epoque})
    if orphelins:
        raise EpoqueCorpusError("ARTEFACT_ABSENT_OU_INCOHERENT",
                                {"symboles": orphelins})
    return retenus


def epoque_corpus(racine: Path, symboles: list[str] | None = None, *,
                  pin: str | None = None) -> str:
    """Generation commune au corpus demande; echec ferme sinon.

    ``pin`` est une ASSERTION, jamais une autorisation : une valeur differente
    de celle du corpus refuse la mesure, et un pin juste ne rattrape jamais un
    corpus melange ou illisible.
    """
    manifestes = manifestes_corpus(racine, symboles)
    epoques = sorted({m["engine_epoch"] for m in manifestes})
    if len(epoques) > 1:
        # Nommer les symboles fautifs : un compteur ne dit pas quoi rejouer.
        raise EpoqueCorpusError("GENERATIONS_MIXTES", {
            "epoques": [e[:16] for e in epoques],
            "symboles": len(manifestes),
            "par_epoque": {
                epoque[:16]: [m["symbol"] for m in manifestes
                              if m["engine_epoch"] == epoque][:20]
                for epoque in epoques
            },
        })
    corpus = epoques[0]
    if pin and pin != corpus:
        raise EpoqueCorpusError("PIN_DIFFERENT_DU_CORPUS", {
            "pin": pin[:16], "corpus": corpus[:16],
        })
    return corpus


def etat_epoque(racine: Path, symboles: list[str] | None = None, *,
                pin: str | None = None) -> dict:
    """Bloc publiable : generation mesuree, arbre de travail et ecart.

    L'ecart entre le corpus et l'arbre de travail est PERMIS — un commit qui
    ne touche pas la semantique du rejeu ne perime pas une mesure — mais il
    est toujours VISIBLE dans le rapport.
    """
    demandes = _symboles_corpus(racine, symboles)
    manifestes = manifestes_corpus(racine, symboles)
    corpus = epoque_corpus(racine, symboles, pin=pin)
    arbre = empreinte_courante()
    retenus = sorted(m["symbol"] for m in manifestes)
    return {
        "corpus_epoch": corpus,
        "workspace_engine_epoch": arbre,
        "workspace_matches_corpus": corpus == arbre,
        "reading": "analyse courante d'un corpus scelle; jamais une "
                   "reproduction du moteur present sur disque",
        "pin": pin or None,
        # Un corpus partiel est homogene : sans la liste DEMANDEE, une mesure
        # sur 135 des 147 symboles se lit exactement comme une mesure sur 147.
        "requested_symbols": demandes,
        "retained_symbols": retenus,
        "requested_count": len(demandes),
        "retained_count": len(retenus),
        "manifests": sorted(manifestes, key=lambda m: m["symbol"]),
    }


def epoque_reference(racine: Path, symboles: list[str] | None = None) -> tuple[str, dict]:
    """Generation DOMINANTE d'un dossier vivant, et le decompte par epoque.

    ``results/rejeu_univers_brut`` peut legitimement melanger deux generations
    pendant un backfill : la reference y est la generation majoritaire, et le
    decompte publie rend le melange visible. Le depart d'egalite est
    lexicographique pour rester deterministe d'un appel a l'autre.
    """
    noms = _symboles_corpus(racine, symboles)
    if not noms:
        raise EpoqueCorpusError("CORPUS_VIDE", {"racine": str(racine)})
    tri: dict[str, int] = {}
    for symbole in noms:
        signee = empreinte_artefact(racine, symbole)
        if not signee:
            continue
        tri[signee] = tri.get(signee, 0) + 1
    if not tri:
        raise EpoqueCorpusError("EPOQUE_ABSENTE", {"symboles": noms[:10]})
    reference = min(tri, key=lambda e: (-tri[e], e))
    return reference, dict(sorted(tri.items()))


STATUT_BLOQUE = "ANALYSIS_BLOCKED"


def chemin_blocage(sortie: Path) -> Path:
    """Fichier frere ou un banc declare son blocage.

    Un tableau de bord ne lit pas un code de retour : c'est exactement ce mode
    de panne qui a dure du 25/08 08:08 au lendemain matin. Le blocage est donc
    PUBLIE dans un fichier, a cote du rapport, sans jamais ecraser le dernier
    rapport valide -- un rapport vide qui remplace une mesure fait passer une
    panne pour une absence de signal.
    """
    sortie = Path(sortie)
    return sortie.with_name(sortie.stem + ".blocked.json")


def publier_blocage(sortie: Path, charge: dict) -> Path:
    """Ecrit la declaration de blocage et rend son chemin."""
    from datetime import datetime, timezone

    chemin = chemin_blocage(sortie)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    corps = {
        "status": STATUT_BLOQUE,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "report_not_written": str(sortie),
        **charge,
    }
    chemin.write_text(json.dumps(corps, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return chemin


def lever_blocage(sortie: Path) -> None:
    """Efface une declaration de blocage perimee apres une mesure reussie.

    Sans cela, un blocage resolu resterait visible indefiniment et la prochaine
    panne, elle, passerait pour un simple reste.
    """
    with contextlib.suppress(OSError):
        chemin_blocage(sortie).unlink()
