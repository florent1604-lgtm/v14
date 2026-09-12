"""Proprietaire unique de l'execution SEQUENTIELLE d'une politique.

Une politique sequentielle envoie ses ordres L'UN APRES L'AUTRE : l'ordre
suivant nait apres le sort du precedent. Trois familles passent par ici -- l'arene
``adaptive`` historique, la famille adaptative, et le moteur generique -- et elles
ne different que par un ``ProfilSequentiel``.

Pourquoi ce module existe
-------------------------

L'ordonnancement etait ecrit deux fois : une machine a etats dans le runner et
une boucle dans ``BacktestExecutionEngine``. Elles divergeaient sur un point qui
compte : la boucle du moteur remplissait chaque ordre des le PREMIER evenement,
donc une tranche programmee a t + 6 s s'executait a t0 et le meme plan rendait
deux resultats selon l'entree utilisee. Un seul proprietaire supprime la classe
entiere de ce defaut : les deux entrees prennent desormais les memes decisions
d'ordonnancement et de remplissage, et la seule difference admise est nommee
dans ``ProfilSequentiel``.

Trois invariants vivent ici, et nulle part ailleurs :

* **jamais plus que la quantite voulue** -- ``fills.FillBudget``, alimente par
  les remplissages REELS et non par le plan ;
* **discretisation decalage -> indice** -- ``index_activation``, seule fonction
  qui traduit des millisecondes en evenements consommables ;
* **un evenement anterieur a l'horaire n'est jamais consomme** -- une tranche
  differee ne peut pas se remplir avant son activation.

Aucune dependance a MT5, au reseau ou a ``.env`` : ce module ne fait que du
simule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from titanium.execution_sim.fills import FillBudget, Mode
from titanium.execution_sim.matching import MatchingSimulator
from titanium.execution_sim.models import ExecutionIntent, MarketSnapshot, Order
from titanium.execution_sim.oms import OrderManager
from titanium.execution_sim.portfolio import Portfolio
from titanium.execution_sim.risk import RiskEngine, RiskRejected


def index_activation(
    offset_ms: int,
    latency_ms: int,
    seconds_per_snapshot: float,
    count: int,
    *,
    plafond_si_differe: bool = False,
) -> int:
    """Index du premier evenement qu'un ordre differe peut consommer.

    Proprietaire unique de cette discretisation. Les familles ont des exigences
    differentes, et c'est dit ici plutot que duplique :

    * arrondi vers le BAS -- un ordre au marche s'execute sur l'evenement
      d'arrivee, comme le temoin ``market`` ;
    * arrondi vers le HAUT des que le decalage est strictement positif -- une
      tranche programmee a 800 ms ne doit pas se remplir sur un evenement
      anterieur a son horaire.

    Un decalage nul garde la discretisation basse dans les deux cas.
    """
    span = max(seconds_per_snapshot, 1e-9)
    if plafond_si_differe and offset_ms > 0:
        index = math.ceil((offset_ms + latency_ms) / 1000.0 / span)
    else:
        index = int((offset_ms + latency_ms) / 1000.0 / span)
    return min(count - 1, max(0, index))


@dataclass(frozen=True)
class ProfilSequentiel:
    """Les choix qui distinguent les familles sequentielles.

    Tout le reste du chemin est commun. Ces differences ont ete payees deux fois
    a l'origine : deux machines a etats paralleles, dont une seule bornait le
    remplissage. Elles sont nommees ici, en un seul endroit.

    ``quantite``
        ``reliquat`` -- la quantite du plan est remplacee par le reliquat (un
        escalier d'agressivite vise la totalite de ce qui reste).
        ``plan_borne`` -- la quantite du plan est bornee au reliquat (une
        echelle de tranches porte sa propre taille).
        ``libre`` -- aucune borne : le plan fait foi. Reserve aux politiques a
        jambes multiples, dont chaque jambe porte la taille pleine par
        conception ; les borner casserait leur semantique.
    ``annulation``
        ``ordre_ouvert`` -- annule tout ordre precedent encore ouvert.
        ``drapeau`` -- n'annule que si le plan le demande (``cancel_previous``).
    ``soumission``
        ``arrivee`` -- horodatage de soumission = instant d'arrivee.
        ``activation`` -- horodatage = instant d'activation de la tranche.
    ``discretisation``
        ``plancher`` / ``plafond`` -- voir ``index_activation``.
    """

    quantite: str
    annulation: str
    soumission: str
    discretisation: str


#: Arene ``adaptive`` historique : son comportement est fige, la matrice des
#: quinze politiques depend de chacun de ces choix.
PROFIL_ADAPTIVE_HISTORIQUE = ProfilSequentiel("reliquat", "ordre_ouvert", "arrivee", "plancher")

#: Famille adaptative : tranches bornees, annulation demandee par le plan,
#: horodatage a l'activation, aucun evenement anterieur consomme.
PROFIL_ADAPTATIF = ProfilSequentiel("plan_borne", "drapeau", "activation", "plafond")

#: Politiques a jambes multiples : chaque jambe porte la taille pleine, donc
#: aucune borne. Le reste du chemin est celui des autres familles.
PROFIL_JAMBES = ProfilSequentiel("libre", "drapeau", "activation", "plafond")


def post_new_fills(portfolio: Portfolio, order: Order, posted: set[str]) -> None:
    """Passe au portefeuille les remplissages pas encore comptes, une seule fois."""
    for fill in order.fills:
        if fill.fill_id not in posted:
            portfolio.apply_fill(order, quantity=fill.quantity, price=fill.price, fee=fill.fee)
            posted.add(fill.fill_id)


def executer_sequentiel(
    orders: list[Order],
    snapshots: list[MarketSnapshot],
    intent: ExecutionIntent,
    *,
    latency_ms: int,
    oms: OrderManager,
    portfolio: Portfolio,
    risk: RiskEngine,
    matcher: MatchingSimulator,
    seconds_per_snapshot: float,
    profil: ProfilSequentiel = PROFIL_ADAPTATIF,
) -> list[Order]:
    """Execute une politique qui envoie ses ordres L'UN APRES L'AUTRE.

    Toutes les familles passent ici et ne different que par ``ProfilSequentiel``.
    Le remplissage est borne par ``FillBudget``, proprietaire unique de la regle
    « jamais plus que la quantite voulue », et chaque ordre ne consomme qu'a
    partir de son propre instant d'activation.
    """
    posted: set[str] = set()
    executed: list[Order] = []
    borne = profil.quantite != "libre"
    budget = FillBudget(intent.quantity) if borne else None
    mode = Mode.RELIQUAT if profil.quantite == "reliquat" else Mode.PLAN
    precedent: Order | None = None
    for planned in orders:
        if budget is not None:
            if budget.epuise:
                break
            planned.quantity = budget.autoriser(planned.quantity, mode=mode)
            if planned.quantity <= 1e-12:
                continue
        annuler = (
            precedent is not None
            and not precedent.is_terminal
            and (
                profil.annulation == "ordre_ouvert"
                or bool(planned.metadata.get("cancel_previous"))
            )
        )
        if annuler:
            oms.request_cancel(precedent.client_order_id, snapshots[0].timestamp)
            oms.ack_cancel(precedent.client_order_id, True, snapshots[0].timestamp)
            planned.replaces = precedent.client_order_id
            planned.queue_ahead = 0.0
        try:
            checked = risk.validate(planned, portfolio, oms)
        except RiskRejected:
            continue
        start_index = index_activation(
            checked.scheduled_offset_ms,
            latency_ms,
            seconds_per_snapshot,
            len(snapshots),
            plafond_si_differe=profil.discretisation == "plafond",
        )
        if profil.soumission == "activation":
            oms.submit(checked, snapshots[start_index].timestamp)
        else:
            oms.submit(checked, checked.created_at)
        executed.append(checked)
        checked.metadata["simulated_latency_ms"] = latency_ms
        for snapshot in snapshots[start_index:]:
            matcher.match(checked, snapshot, oms)
            post_new_fills(portfolio, checked, posted)
            if checked.is_terminal:
                break
        if budget is not None:
            budget.enregistrer(checked.filled_quantity)
        precedent = checked
    return executed
