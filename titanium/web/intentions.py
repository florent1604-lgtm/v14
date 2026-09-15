"""File d'intentions de clôture manuelle — dépôt seul, jamais d'exécution.

Pourquoi un fichier plutôt qu'une route HTTP qui ferme la position ?

L'audit a établi trois faits qui interdisent le chemin direct :

* ``TRADE_ACTION_REMOVE`` est absent de tout le dépôt : annuler un ordre
  demanderait du code d'envoi neuf, écrit sous pression, hors du mur.
* La seule clôture existante, ``position_manager._envoyer_sortie_adaptative``,
  est privée et vit à l'intérieur du cycle de gestion.
* ``assert_can_trade`` est appelé **une fois par cycle** dans ``manage_once``,
  pas dans l'envoi lui-même. L'appeler depuis une requête HTTP ne reproduirait
  pas le mur : cela le contournerait en donnant l'illusion de le respecter.

Donc l'interface **dépose une demande**, et la boucle armée — qui possède déjà
le mur, l'idempotence et le registre — décide de l'honorer ou non. Aucun
chemin HTTP ne va jusqu'au courtier. L'interface reste un lecteur qui laisse
un mot sur la table.

Propriétés tenues par ce module :

* **Ajout seul.** Une ligne JSON par intention, jamais de réécriture : une
  intention déposée ne peut pas être effacée discrètement, elle reste au
  journal.
* **Usage unique.** Chaque intention porte un ``nonce``. Honorée une fois,
  elle est marquée consommée ; une relecture du fichier ne la rejouera pas.
* **Péremption courte.** Cinq minutes. Une demande de clôture vieille de vingt
  minutes ne veut plus rien dire : le marché a bougé, l'opérateur a peut-être
  changé d'avis. Périmée, elle est ignorée — pas exécutée en retard.
* **Ne lève jamais.** Le pont ne doit pas pouvoir casser le trading.

Ce module n'importe rien qui puisse agir sur le courtier.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Cinq minutes. Une intention de clôture est un geste dans l'instant ; au-delà,
#: l'exécuter serait trahir ce que l'opérateur voulait dire.
PEREMPTION_S = 300

#: Les seuls gestes qu'une intention peut porter. Tout le reste est refusé au
#: dépôt : la liste blanche est la frontière, pas un commentaire.
ACTIONS = frozenset({"close_position", "cancel_order"})


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Intention:
    """Une demande humaine, déposée par l'interface, en attente d'arbitrage."""

    action: str
    ticket: str
    symbol: str = ""
    #: Raison saisie par l'opérateur. Sert l'audit : six mois plus tard, on veut
    #: savoir pourquoi cette position a été fermée à la main.
    motif: str = ""
    #: Qui a demandé. ``dashboard`` aujourd'hui ; jamais un agent automatique.
    demande_par: str = "dashboard"
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex)
    demande_a: str = field(default_factory=_maintenant)

    def valide(self) -> tuple[bool, str]:
        """Refus au dépôt plutôt qu'à la consommation : l'erreur remonte à
        l'opérateur tant qu'il est devant l'écran."""
        if self.action not in ACTIONS:
            return False, f"action inconnue : {self.action!r}"
        if not str(self.ticket).strip():
            return False, "ticket manquant"
        return True, ""

    def age_s(self) -> float:
        try:
            depuis = datetime.fromisoformat(self.demande_a)
        except (TypeError, ValueError):
            return float("inf")
        if depuis.tzinfo is None:
            depuis = depuis.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - depuis).total_seconds()

    def fraiche(self, *, peremption: int = PEREMPTION_S) -> bool:
        return self.age_s() <= peremption

    def to_dict(self) -> dict:
        return asdict(self)


def deposer(intention: Intention, chemin: Path) -> tuple[bool, str]:
    """Ajoute une intention à la file. Ne lève jamais.

    Rend ``(False, motif)`` en cas de refus, pour que l'interface puisse dire
    précisément à l'opérateur ce qui n'a pas été pris, au lieu d'un silence.
    """
    ok, motif = intention.valide()
    if not ok:
        return False, motif
    try:
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as f:
            f.write(json.dumps(intention.to_dict(), ensure_ascii=False) + "\n")
            # La boucle peut lire dans la seconde : une ligne à moitié écrite
            # serait ignorée par le parseur, mais autant ne pas la produire.
            f.flush()
            os.fsync(f.fileno())
        return True, intention.nonce
    except Exception as exc:  # noqa: BLE001 — le pont ne casse jamais le trading
        return False, f"{type(exc).__name__}: {exc}"


def _lire(chemin: Path) -> list[dict]:
    try:
        chemin = Path(chemin)
        if not chemin.exists():
            return []
        lignes = []
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                lignes.append(json.loads(ligne))
            except json.JSONDecodeError:
                continue  # ligne tronquée : on l'oublie, on ne devine pas
        return lignes
    except Exception:  # noqa: BLE001
        return []


def nonces_consommes(chemin_journal: Path) -> set[str]:
    """Les intentions déjà honorées, d'après le journal de consommation."""
    return {
        str(d.get("nonce", ""))
        for d in _lire(chemin_journal)
        if d.get("nonce")
    }


def en_attente(chemin: Path, chemin_journal: Path, *,
               peremption: int = PEREMPTION_S) -> list[Intention]:
    """Intentions fraîches, non encore consommées, dans l'ordre de dépôt.

    Une intention périmée n'est pas rendue : mieux vaut qu'un opérateur
    redemande que de fermer une position sur un ordre de cinq minutes trop
    tard.
    """
    deja = nonces_consommes(chemin_journal)
    attente: list[Intention] = []
    for d in _lire(chemin):
        nonce = str(d.get("nonce", ""))
        if not nonce or nonce in deja:
            continue
        champs = {k: v for k, v in d.items() if k in Intention.__dataclass_fields__}
        try:
            intention = Intention(**champs)
        except TypeError:
            continue
        ok, _ = intention.valide()
        if ok and intention.fraiche(peremption=peremption):
            attente.append(intention)
    return attente


def marquer_consommee(intention: Intention, resultat: str, detail: str,
                      chemin_journal: Path) -> bool:
    """Scelle le sort d'une intention : elle ne sera jamais rejouée.

    ``resultat`` est libre côté appelant (``HONOREE``, ``REFUSEE``,
    ``EXPIREE``…). Ce qui compte ici, c'est que le nonce entre au journal :
    c'est lui qui garantit l'usage unique, quel qu'ait été l'issue.
    """
    try:
        chemin_journal = Path(chemin_journal)
        chemin_journal.parent.mkdir(parents=True, exist_ok=True)
        entree = {
            "nonce": intention.nonce,
            "action": intention.action,
            "ticket": intention.ticket,
            "symbol": intention.symbol,
            "resultat": resultat,
            "detail": detail,
            "consomme_a": _maintenant(),
        }
        with chemin_journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:  # noqa: BLE001
        return False


def etat(chemin: Path, chemin_journal: Path) -> dict:
    """Ce que l'interface affiche : demandé, honoré, en attente.

    Permet à l'opérateur de voir que sa demande a bien été déposée *et* qu'elle
    n'est pas encore traitée — sans jamais lui laisser croire qu'elle a été
    exécutée.
    """
    deposees = _lire(chemin)
    consommees = _lire(chemin_journal)
    attente = en_attente(chemin, chemin_journal)
    return {
        "deposees": len(deposees),
        "consommees": len(consommees),
        "en_attente": [i.to_dict() for i in attente],
        "derniers_resultats": consommees[-10:],
        "peremption_s": PEREMPTION_S,
        "fichier": str(chemin),
        "journal": str(chemin_journal),
    }
