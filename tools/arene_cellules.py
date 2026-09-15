"""Comparaison de deux tables d'arene, cellule par cellule — proprietaire unique.

Une seule regle pour « ces deux tables sont-elles les memes ? » : la cle
d'appariement d'une cellule, la lecture du ndjson, le denombrement des ecarts,
la colonne qui porte l'ecart de resultat et les empreintes. Les deux harnais de
cette campagne — budget de risque et posture macro — l'importent ; ce qui les
distingue est une POLITIQUE portee par l'appelant (colonnes comparees, cellules
presentes d'un seul cote comptees ou non), pas deux boucles ecrites separement.
Deux boucles finissaient par diverger sur le point meme qui compte : ce qu'on
appelle « une cellule qui bouge ».

La FORME du rapport publie reste a l'appelant : ce module rend des comptes et
des valeurs, pas une mise en page.

Aucun seuil, aucun ordre, aucune promotion : on lit deux tables et on compare.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

#: Cles d'appariement d'une cellule. Un scenario appartient a un split, qui
#: appartient a une politique : la cle doit porter les trois, sinon deux
#: techniques se compareraient a elles-memes sur le meme scenario. Privee :
#: 0 appelant externe, comme `_cle_cellule` et `_COLONNE_ECART`.
_CLE: tuple[str, ...] = ("policy", "split", "scenario_id")

#: Colonnes de DECISION du harnais de budget : resultat, remplissage, cout,
#: quantite, motif de refus. Comparer le seul net laisserait passer une cellule
#: dont le cout a bouge a resultat constant.
COLONNES_DECISION: tuple[str, ...] = (
    "net_pnl",
    "fill_ratio",
    "total_cost_bps",
    "filled_quantity",
    "rejected_reason",
)

#: Colonne dont l'ecart maximal est rapporte. Privee : aucun appelant externe.
_COLONNE_ECART = "net_pnl"

#: Nom de la pseudo-colonne qui porte les cellules presentes d'un seul cote,
#: quand l'appelant decide de les compter comme des ecarts.
COLONNE_ABSENTE = "cellule_absente"


def _cle_cellule(row: dict[str, Any]) -> str:
    """Cle d'appariement : ``politique|split|scenario`` (privee, 0 appelant externe)."""
    return "|".join(str(row[champ]) for champ in _CLE)


def indexer(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Indexe les lignes par cle d'appariement."""
    return {_cle_cellule(row): dict(row) for row in rows}


def lire_ndjson(chemin: Path | str) -> list[dict[str, Any]]:
    """Les lignes d'une table ndjson, dans l'ordre du fichier.

    Une ligne illisible nomme le FICHIER et la LIGNE : une passe
    interrompue laisse une table tronquee, et c'est exactement la table
    qu'un operateur va vouloir comparer.
    """
    lignes: list[dict[str, Any]] = []
    with Path(chemin).open("r", encoding="utf-8") as handle:
        for numero, ligne in enumerate(handle, 1):
            if not ligne.strip():
                continue
            try:
                lignes.append(json.loads(ligne))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{chemin}: ligne {numero} illisible ({exc.msg})"
                ) from None
    return lignes


def projeter(
    cellules: dict[str, dict[str, Any]], colonnes: Sequence[str]
) -> dict[str, list[Any]]:
    """Reduit chaque cellule aux colonnes demandees, dans leur ordre."""
    return {cle: [row.get(colonne) for colonne in colonnes] for cle, row in cellules.items()}


def empreinte(cellules: Any) -> str:
    """Empreinte stable d'un ensemble de cellules (insensible a l'ordre des cles)."""
    charge = json.dumps(cellules, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(charge.encode("utf-8")).hexdigest()


def sha256_fichier(chemin: Path | str) -> str:
    return hashlib.sha256(Path(chemin).read_bytes()).hexdigest()


def comparer(
    gauche: dict[str, list[Any]],
    droite: dict[str, list[Any]],
    *,
    colonnes: Sequence[str],
    cellules_absentes_comptent: bool = False,
) -> dict[str, Any]:
    """Cellule par cellule, sur les colonnes nommees.

    ``cellules_absentes_comptent`` dit ce qu'une cellule presente d'un seul cote
    represente : un ecart (harnais qui compare deux passes du meme seed) ou rien
    (harnais qui compare une projection publiee).

    ``ecart_net_max`` porte sur la colonne ``_COLONNE_ECART`` et sur TOUTES
    les cellules qui bougent, pas seulement sur les exemples rapportes.
    """
    communes = sorted(set(gauche) & set(droite))
    absentes = sorted(set(gauche) ^ set(droite))
    ecarts: list[tuple[str, tuple[str, ...], list[Any], list[Any]]] = []
    colonnes_bougees: dict[str, int] = {}
    par_politique: dict[str, int] = {}
    ecarts_net: list[float] = []
    index_ecart = colonnes.index(_COLONNE_ECART) if _COLONNE_ECART in colonnes else None
    for cle in communes:
        a, b = gauche[cle], droite[cle]
        champs = tuple(
            nom
            for index, nom in enumerate(colonnes)
            if _valeur(a, index) != _valeur(b, index)
        )
        if not champs:
            continue
        ecarts.append((cle, champs, a, b))
        nom_politique = _politique(cle)
        par_politique[nom_politique] = par_politique.get(nom_politique, 0) + 1
        for nom in champs:
            colonnes_bougees[nom] = colonnes_bougees.get(nom, 0) + 1
        if index_ecart is not None:
            ecart = _nombre(_valeur(a, index_ecart), _valeur(b, index_ecart))
            if ecart is not None:
                ecarts_net.append(ecart)
    bougees = len(ecarts)
    if cellules_absentes_comptent and absentes:
        bougees += len(absentes)
        colonnes_bougees[COLONNE_ABSENTE] = len(absentes)
        for cle in absentes:
            nom_politique = _politique(cle)
            par_politique[nom_politique] = par_politique.get(nom_politique, 0) + 1
    comparees = len(communes) + (len(absentes) if cellules_absentes_comptent else 0)
    return {
        "cellules_comparees": comparees,
        "cellules_identiques": comparees - bougees,
        "cellules_bougees": bougees,
        "cellules_communes": len(communes),
        "cellules_absentes": len(absentes),
        "colonnes_bougees": dict(sorted(colonnes_bougees.items())),
        "bougees_par_politique": dict(sorted(par_politique.items())),
        "ecart_net_max": max(ecarts_net) if ecarts_net else 0.0,
        "ecarts": ecarts,
    }


def _politique(cle: str) -> str:
    return cle.split("|")[0]


def _valeur(cellule: Sequence[Any], index: int) -> Any:
    return cellule[index] if index < len(cellule) else None


def _nombre(gauche: Any, droite: Any) -> float | None:
    """Ecart absolu, ou ``None`` si une valeur n'est pas numerique.

    Un motif de refus qui change n'a pas d'ecart de resultat defini : la cellule
    compte deja comme differente, elle n'entre simplement pas dans le maximum.
    """
    try:
        return abs(float(gauche) - float(droite))
    except (TypeError, ValueError):
        return None
