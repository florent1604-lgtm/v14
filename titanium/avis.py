"""Pont Titanium → analystes LLM, hors du chemin critique.

Le problème
-----------
Une délibération complète (4 analystes, débat haussier/baissier, comité de
risque) prend des minutes. La boucle tourne en 60 secondes. Appeler le
graphe depuis la boucle la ferait attendre, et le flux — que Florent veut
constant et sans coupure — s'arrêterait à chaque décision.

Pire : un appel LLM peut échouer, expirer, ou tomber sur un fournisseur
saturé. Le mettre dans le chemin de décision rendrait le trading dépendant
de la disponibilité d'un service externe.

La solution
-----------
Deux flux découplés par une file et une mémoire centrale scellée.

1. La boucle **dépose** ce que Titanium a vu (portes, piliers, zones,
   indicateurs) et continue immédiatement. Coût : une écriture.
2. Un travailleur séparé **consomme** les demandes et écrit une proposition
   liée à l'identité exacte de la décision.
3. La boucle relit uniquement cette proposition exacte, sans attendre.
   Absence, autre barre ou empreinte différente → `WAIT` et alerte.

Autorité cognitive, exécution séparée
-------------------------------------
Hermès choisit ALLOW, WAIT ou BLOCK parmi les candidats produits par les
organes et peut moduler sa conviction. Il ne peut inventer un sens, fixer une
taille, appeler MT5, fermer une position ou modifier un SL. Toute autorisation
reste soumise aux gardes techniques de mémoire, risque, coût et compte DEMO.

Un avis périmé est traité comme une absence d'avis. Sans cette péremption,
une analyse d'il y a six heures continuerait à peser sur une décision prise
maintenant — le marché ayant changé entre-temps.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from titanium.organism.contracts import (
    MODEL_VERSION,
    PROMPT_VERSION,
    DecisionIdentity,
    build_decision_identity,
)

#: Au-delà, un avis ne décrit plus le marché courant. Trois barres M15.
PEREMPTION_S = 45 * 60
HERMES_RETRY_AFTER_S = 600
HERMES_MAX_FAILURES_PER_DECISION = 2

#: Valeur rendue quand aucun avis exploitable n'existe. Le point mort de
#: l'échelle de confiance : ni bonus, ni malus.
NEUTRE = 0.5


@dataclass
class Demande:
    """Ce que Titanium a vu, transmis aux analystes."""

    symbol: str
    side: int
    verdict: str = ""
    code: str = ""
    piliers: int = 0
    total_piliers: int = 4
    famille: str = ""
    prix: float = 0.0
    stop_distance: float = 0.0
    rr: float = 0.0
    bar_time: str = ""
    engine_context: str = ""
    zones: list = field(default_factory=list)
    indicateurs: dict = field(default_factory=dict)
    #: Résumé d'auscultation — quels organes du bot sont dégradés.
    sante: str = ""
    demande_a: str = ""
    decision_ref: str = ""
    context_digest: str = ""
    model_version: str = MODEL_VERSION
    prompt_version: str = PROMPT_VERSION

    def sceller(self) -> DecisionIdentity:
        identity = build_decision_identity(asdict(self))
        self.decision_ref = identity.decision_ref
        self.context_digest = identity.context_digest
        self.model_version = identity.model_version
        self.prompt_version = identity.prompt_version
        return identity

    def to_dict(self) -> dict:
        self.sceller()
        d = asdict(self)
        d["demande_a"] = self.demande_a or _maintenant()
        return d

    def resume(self) -> str:
        """Le brief que liront les analystes, en clair.

        Volontairement factuel et sans conclusion : on demande un avis
        indépendant, pas une validation. Écrire « setup excellent » ici
        obtiendrait un accord poli et sans valeur.
        """
        sens = "haussier" if self.side > 0 else "baissier"
        lignes = [
            f"Analyse déterministe de {self.symbol} (barre {self.bar_time}) :",
            f"- direction retenue : {sens}",
            f"- confluence : {self.piliers}/{self.total_piliers} piliers, "
            f"famille « {self.famille or 'non classée'} »",
            f"- verdict de la porte : {self.verdict} ({self.code})",
        ]
        if self.prix and self.stop_distance:
            lignes.append(
                f"- plan : entrée {self.prix:.5f}, stop à "
                f"{self.stop_distance:.5f} du prix, objectif {self.rr:.1f} R")
        if self.zones:
            noms = ", ".join(str(z.get("kind", "?")) for z in self.zones[:6])
            lignes.append(f"- zones repérées : {noms}")
        if self.indicateurs:
            paires = [f"{k}={v:.4g}" for k, v in
                      list(self.indicateurs.items())[:10]
                      if isinstance(v, (int, float))]
            if paires:
                lignes.append("- indicateurs : " + ", ".join(paires))
        if self.sante:
            # L'état du bot qui a produit cette lecture. Un verdict rendu
            # alors qu'un organe est hors service mérite d'être pondéré :
            # l'analyste doit pouvoir en tenir compte plutôt que de traiter
            # toute lecture comme également fiable.
            lignes.append(f"\nÉtat du système à cet instant : {self.sante}")

        lignes.append(
            "\nCette lecture est purement mécanique. Confronte-la à ta propre "
            "analyse du marché et dis si l'entrée te paraît pertinente, en "
            "justifiant les points de désaccord s'il y en a.")
        return "\n".join(lignes)


@dataclass
class Avis:
    """Ce que les analystes ont répondu."""

    symbol: str
    side: int
    conviction: float = NEUTRE
    rating: str = ""
    accord: bool | None = None      # d'accord avec la direction déterministe ?
    resume: str = ""
    bar_time: str = ""
    rendu_a: str = ""
    source: str = ""
    action: str = "WAIT"
    sources: list[str] = field(default_factory=list)
    decision_ref: str = ""
    context_digest: str = ""
    evidence_digest: str = ""
    model_version: str = MODEL_VERSION
    prompt_version: str = PROMPT_VERSION

    def frais(self, *, maintenant: float | None = None,
              peremption: int = PEREMPTION_S) -> bool:
        """Un avis périmé décrit un marché qui n'existe plus."""
        import time
        try:
            t = datetime.fromisoformat(self.rendu_a).timestamp()
        except (ValueError, TypeError):
            return False
        return (maintenant or time.time()) - t <= peremption


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


# ────────────────────────────────────────────────────────────────────────
# Dépôt (côté boucle — doit être instantané)
# ────────────────────────────────────────────────────────────────────────

def deposer(demande: Demande, chemin: Path) -> bool:
    """Ajoute une demande à la file. Ne lève jamais.

    Écriture en ajout simple : la boucle ne doit pas payer plus qu'une
    ligne. Le travailleur se charge de la déduplication — c'est lui qui a
    le temps.
    """
    try:
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as f:
            f.write(json.dumps(demande.to_dict(), ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 — le pont ne casse jamais le trading
        return False


# ────────────────────────────────────────────────────────────────────────
# Lecture (côté boucle — doit être instantané et tolérant)
# ────────────────────────────────────────────────────────────────────────

def dernier_avis(symbole: str, chemin: Path, *,
                 identity: DecisionIdentity | None = None,
                 peremption: int = PEREMPTION_S) -> Avis | None:
    """Le dernier avis frais pour ce symbole, ou None.

    Ne bloque jamais et ne déclenche aucun appel : si le travailleur est
    arrêté, en retard, ou en panne, la boucle continue exactement comme si
    le pont n'existait pas. C'est la propriété qui rend ce couplage sûr.
    """
    try:
        chemin = Path(chemin)
        if not chemin.exists():
            return None
        trouve = None
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne or f'"{symbole}"' not in ligne:
                continue
            try:
                d = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            if d.get("symbol") != symbole:
                continue
            if identity is not None:
                expected = identity.to_dict()
                if any(d.get(key) != value for key, value in expected.items()):
                    continue
                if not str(d.get("evidence_digest", "")):
                    continue
            trouve = d          # le dernier gagne : le fichier est ordonné
        if trouve is None:
            return None
        a = Avis(**{k: v for k, v in trouve.items()
                    if k in Avis.__dataclass_fields__})
        return a if a.frais(peremption=peremption) else None
    except Exception:  # noqa: BLE001
        return None


def conviction_pour(symbole: str, side: int, chemin: Path,
                    *, identity: DecisionIdentity | None = None,
                    peremption: int = PEREMPTION_S) -> tuple[float, str]:
    """Conviction utilisable par le sizing, et son motif.

    Un avis en **désaccord de direction** ne renverse rien — il abaisse la
    conviction, donc la taille. C'est la seule autorité qu'un LLM possède
    ici : rendre une position plus petite, jamais l'annuler ni l'inverser.
    """
    a = dernier_avis(symbole, chemin, identity=identity, peremption=peremption)
    if a is None:
        return NEUTRE, "aucun avis"
    if a.side and side and a.side != side:
        # Désaccord franc : on plafonne bas sans descendre à zéro, la
        # décision déterministe restant souveraine.
        return min(a.conviction, 0.2), f"analystes en désaccord ({a.rating})"
    return a.conviction, f"analystes {a.rating or 'sans note'}"


def autorisation_pour(symbole: str, side: int, chemin: Path,
                      *, identity: DecisionIdentity | None = None,
                      peremption: int = PEREMPTION_S) -> tuple[str, str]:
    """Verdict fondamental frais; toute ambiguite attend plutot que trader."""
    a = dernier_avis(symbole, chemin, identity=identity, peremption=peremption)
    if a is None:
        return "WAIT", "analyse fondamentale absente ou perimee"
    if a.side and side and a.side != side:
        return "BLOCK", "analyse fondamentale rendue pour le sens oppose"
    action = str(a.action or "WAIT").upper()
    if action not in {"ALLOW", "WAIT", "BLOCK"}:
        action = "WAIT"
    sources = ", ".join(a.sources[:4]) if a.sources else "aucune source"
    return action, f"{a.resume or a.rating or action} [{sources}]"


# ────────────────────────────────────────────────────────────────────────
# Écriture des avis (côté travailleur)
# ────────────────────────────────────────────────────────────────────────

def enregistrer(avis: Avis, chemin: Path) -> bool:
    try:
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        avis.rendu_a = avis.rendu_a or _maintenant()
        with chemin.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(avis), ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def demandes_en_attente(file_demandes: Path, file_avis: Path,
                        *, maxi: int = 20,
                        peremption: int = PEREMPTION_S) -> list[Demande]:
    """Demandes non encore traitées, dédupliquées par (symbole, barre).

    La déduplication vit ici et non côté boucle : le travailleur a le temps,
    la boucle ne l'a pas. Une même barre relue dix fois ne coûte donc
    qu'une seule délibération.
    """
    file_demandes, file_avis = Path(file_demandes), Path(file_avis)
    if not file_demandes.exists():
        return []

    import time as _t
    maintenant = _t.time()
    faits = set()
    outages: dict[str, tuple[int, float]] = {}
    if file_avis.exists():
        for ligne in file_avis.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ligne)
                cle = d.get("decision_ref") or (d.get("symbol"), d.get("bar_time"))
                if (isinstance(cle, str) and cle and d.get("source") == "hermes-unavailable"
                        and d.get("action") == "WAIT" and d.get("model_version") == "none"):
                    try:
                        rendered = datetime.fromisoformat(d.get("rendu_a", ""))
                        if rendered.tzinfo is None or rendered.timestamp() > maintenant:
                            raise ValueError("unverifiable outage timestamp")
                        count, last = outages.get(cle, (0, 0.0))
                        outages[cle] = (count + 1, max(last, rendered.timestamp()))
                    except (TypeError, ValueError):
                        faits.add(cle)
                else:
                    # A real cognitive WAIT/BLOCK/ALLOW is final for these facts.
                    faits.add(cle)
            except json.JSONDecodeError:
                continue
    for cle, (count, last) in outages.items():
        # One retry after a durable cooldown, never a loop of billable retries.
        # A restart cannot reset this budget: evidence stays in the append-only journal.
        # Source freshness is still checked by the worker before any API request.
        if (count >= HERMES_MAX_FAILURES_PER_DECISION
                or maintenant - last < HERMES_RETRY_AFTER_S):
            faits.add(cle)

    vues: dict[object, Demande] = {}
    perimees = 0
    for ligne in file_demandes.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            d = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        legacy = (d.get("symbol"), d.get("bar_time"))
        cle = d.get("decision_ref") or legacy
        if cle in faits or legacy in faits or None in legacy:
            continue

        # ── Une demande plus vieille que la péremption produirait un avis
        #    que `dernier_avis` jetterait aussitôt. Délibérer dessus coûte
        #    deux à sept minutes pour rien, pendant que les demandes
        #    FRAÎCHES s'empilent derrière. Constaté le 07/08/2026 :
        #    106 demandes périmées sur 120, un arriéré qui ne se résorbe
        #    jamais parce qu'il grossit plus vite qu'il ne se traite.
        try:
            age = maintenant - datetime.fromisoformat(
                d.get("demande_a", "")).timestamp()
            if age > peremption:
                perimees += 1
                continue
        except (ValueError, TypeError):
            pass
        vues[cle] = Demande(**{k: v for k, v in d.items()
                               if k in Demande.__dataclass_fields__})

    # Les plus récentes d'abord : si le travailleur prend du retard, mieux
    # vaut un avis sur la barre courante qu'un rattrapage d'historique.
    return list(vues.values())[-maxi:]


def purger(file_demandes: Path, *, peremption: int = PEREMPTION_S) -> int:
    """Retire du fichier les demandes périmées. Rend le nombre supprimé.

    Sans purge, le fichier grossit indéfiniment et chaque passage du
    travailleur relit un historique qu'il vient de décider d'ignorer.
    """
    import time as _t

    try:
        p = Path(file_demandes)
        if not p.exists():
            return 0
        maintenant = _t.time()
        gardees, jetees = [], 0
        for ligne in p.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                d = json.loads(ligne)
                age = maintenant - datetime.fromisoformat(
                    d.get("demande_a", "")).timestamp()
            except (ValueError, TypeError, json.JSONDecodeError):
                gardees.append(ligne)      # illisible : on ne jette pas
                continue
            if age > peremption:
                jetees += 1
            else:
                gardees.append(ligne)
        if jetees:
            tmp = p.with_suffix(".tmp")
            tmp.write_text("\n".join(gardees) + ("\n" if gardees else ""),
                           encoding="utf-8")
            tmp.replace(p)
        return jetees
    except Exception:  # noqa: BLE001
        return 0
