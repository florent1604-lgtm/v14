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
