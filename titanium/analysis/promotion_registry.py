"""Registre des promotions — la SEULE source possible d'un `edge_ok=True`.

Toute autre voie serait un contournement du protocole. C'est pourquoi ce
module est minuscule et sans intelligence : il lit un fichier, il répond oui
ou non. Le raisonnement vit dans `promotion.py`, la décision dans un geste
humain, et l'autorisation ici.

Fail-closed
-----------
`is_promoted` rend **False** sur la moindre anomalie : fichier absent,
illisible, entrée manquante, révoquée, échelon insuffisant, ou approbation
non signée. Un registre corrompu ne doit jamais ouvrir le mode réel — il
doit le fermer.

Clé humaine à sens unique
--------------------------
`approved_by` est obligatoire. Un humain peut **refuser** une cellule que le
protocole juge éligible ; il ne peut pas **accepter** une cellule que le
protocole refuse — `promote` lève sur un verdict non éligible. L'humain est
un frein supplémentaire, jamais une dérogation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

#: Échelons. Le mode réel n'est atteignable qu'au sommet, et chaque palier
#: exige une accumulation propre.
ECHELON_FANTOME = 1     # PROD évalué et journalisé, jamais exécuté
ECHELON_DEMO = 2        # PROD exécuté sur le compte démo
ECHELON_REEL = 3        # argent réel — hors de portée de ce module

#: Échelon minimal pour qu'`edge_ok=True` soit délivré.
ECHELON_MIN = ECHELON_DEMO


def load(chemin) -> dict:
    """Contenu du registre. Rend {} sur toute anomalie."""
    p = Path(chemin)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def is_promoted(chemin, cell_key: str) -> bool:
    """La cellule est-elle autorisée en PROD ? Fail-closed."""
    try:
        entree = load(chemin).get(str(cell_key))
        if not isinstance(entree, dict):
            return False
        if entree.get("revoked_at"):
            return False
        if int(entree.get("rung", 0) or 0) < ECHELON_MIN:
            return False
        if not str(entree.get("approved_by", "") or "").strip():
            return False
        return True
    except Exception:  # noqa: BLE001 — toute surprise ferme la porte
        return False


def promote(chemin, cell_key: str, verdict, *,
            approved_by: str, rung: int = ECHELON_DEMO) -> None:
    """Inscrit une promotion. Lève si le protocole ne l'autorise pas."""
    if not getattr(verdict, "eligible", False):
        bloquants = ", ".join(getattr(verdict, "bloquants", []) or ["?"])
        raise ValueError(
            f"{cell_key} n'est pas éligible — bloquants : {bloquants}")
    if not str(approved_by or "").strip():
        raise ValueError(
            "approved_by est obligatoire : la promotion exige un geste humain "
            "nommé, traçable et opposable")
    if int(rung) >= ECHELON_REEL:
        raise ValueError(
            "l'échelon réel ne s'accorde pas depuis ce module : il exige une "
            "décision distincte, hors du protocole de mesure")

    p = Path(chemin)
    data = load(p)
    data[str(cell_key)] = {
        "rung": int(rung),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": str(approved_by).strip(),
        "revoked_at": None,
        "revoke_reason": "",
        # Le verdict est archivé tel quel : une promotion doit rester
        # auditable après coup, y compris si le protocole évolue ensuite.
        "verdict": (verdict.to_dict() if hasattr(verdict, "to_dict")
                    else str(verdict)),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                              default=str), encoding="utf-8")
    tmp.replace(p)


def revoke(chemin, cell_key: str, *, reason: str) -> bool:
    """Révoque une promotion. Rend True si une entrée a été modifiée.

    La révocation ne supprime pas l'entrée : l'historique d'une promotion
    révoquée est ce qui permet de comprendre pourquoi elle avait été accordée.
    """
    p = Path(chemin)
    data = load(p)
    entree = data.get(str(cell_key))
    if not isinstance(entree, dict) or entree.get("revoked_at"):
        return False
    entree["revoked_at"] = datetime.now(timezone.utc).isoformat()
    entree["revoke_reason"] = str(reason or "sans motif")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                              default=str), encoding="utf-8")
    tmp.replace(p)
    return True


def actives(chemin) -> dict:
    """Cellules actuellement promues."""
    return {k: v for k, v in load(chemin).items()
            if is_promoted(chemin, k)}
