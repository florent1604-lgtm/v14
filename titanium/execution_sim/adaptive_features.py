"""Contexte d'arrivee des techniques d'execution adaptative.

Un seul proprietaire pour la question « que voit une technique au moment de
decider ? ». Les techniques ne lisent ni le carnet ni l'intention directement :
elles lisent cet objet, ce qui garantit que deux techniques comparees ont vu
exactement la meme chose.

**Echec ferme.** ``build_features`` rend ``None`` des qu'une entree n'est pas
fiable (tick nul, carnet inverse, prix non fini, quantite non positive). Les
appelants refusent alors de produire un ordre ; aucun repli n'est fabrique.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from titanium.execution_sim.models import ExecutionIntent
from titanium.execution_sim.policies import PolicyContext

# La regle de completude du bloc macro a UN seul proprietaire : la porte de
# confluence et le contexte d'arrivee doivent refuser sur le meme critere, sinon
# un bloc accepte par l'une serait refuse par l'autre sans que rien ne le dise.
from titanium.macro.gate import MACRO_BLOCK_KEYS, MACRO_POSTURE_KEY

MAX_DECIMALES = 10


def fini(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(
        float(value)
    )


def arrondi_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, MAX_DECIMALES)


def borne(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def profondeur(levels: tuple[Any, ...]) -> float:
    total = 0.0
    for level in levels:
        price = float(getattr(level, "price", 0.0) or 0.0)
        quantity = float(getattr(level, "quantity", 0.0) or 0.0)
        if fini(price) and fini(quantity) and quantity > 0:
            total += quantity
    return total


@dataclass(frozen=True)
class AdaptiveFeatures:
    """Contexte d'arrivee, calcule une fois, sans information posterieure.

    Toutes les techniques lisent ce meme objet : deux techniques comparees ont
    donc vu exactement la meme chose, comme un scenario identique donne a deux
    politiques de la matrice.
    """

    spread: float
    spread_bps: float
    volatility_bps: float
    tick_size: float
    quantity: float
    touch: float
    opposite: float
    mid: float
    microprice: float
    imbalance: float
    take_depth: float
    queue_depth: float
    depth_ratio: float
    inventory_ratio: float
    urgency: float
    baseline_spread_bps: float | None = None
    horizon_ms: int = 0
    # ── Pas de champ macro recopie ici, et c'est deliberé : la posture macro
    #    n'est pas COPIEe, elle est APPLIQUEE a l'axe d'agressivite que les
    #    techniques lisent deja (`urgency`), et la valeur appliquee est celle que
    #    la trace de chaque technique porte deja. Recopier l'etat ou le score dans
    #    des champs que personne ne lirait aurait ajoute de l'etat sans lecteur —
    #    le defaut que ce module refuse par ailleurs.


def build_features(
    intent: ExecutionIntent,
    context: PolicyContext,
    *,
    baseline_spread_bps: float | None = None,
    max_inventory: float = 10.0,
    urgency_default: float = 0.5,
    horizon_reference_ms: float = 60_000.0,
) -> AdaptiveFeatures | None:
    """Derive le contexte d'arrivee, ou ``None`` si la decision n'est pas sure."""
    snapshot = context.snapshot
    tick = float(context.tick_size or 0.0)
    bid = float(snapshot.bid or 0.0)
    ask = float(snapshot.ask or 0.0)
    quantity = float(intent.quantity)
    if not fini(tick) or tick <= 0:
        return None
    if not (fini(bid) and fini(ask)) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    if not fini(quantity) or quantity <= 0:
        return None
    mid = (bid + ask) / 2.0
    if not fini(mid) or mid <= 0:
        return None

    side = int(intent.side)
    touch = bid if side > 0 else ask
    opposite = ask if side > 0 else bid
    spread = ask - bid
    spread_bps = spread / mid * 10_000.0 if mid else 0.0

    bid_depth = profondeur(snapshot.bid_levels)
    ask_depth = profondeur(snapshot.ask_levels)
    # Profondeur prenable = cote oppose (nos ordres au marche consomment ce
    # cote). Absente, on retombe sur le volume observe, borne plancher a la
    # quantite voulue pour ne pas fabriquer un ratio faussement favorable.
    take_depth = ask_depth if side > 0 else bid_depth
    if take_depth <= 0:
        take_depth = max(float(snapshot.volume or 0.0), quantity)
    queue_depth = bid_depth if side > 0 else ask_depth
    total_depth = bid_depth + ask_depth
    microprice = (
        (ask * bid_depth + bid * ask_depth) / total_depth if total_depth > 0 else mid
    )
    imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0
    depth_ratio = take_depth / quantity if quantity > 0 else 0.0

    inventory = 0.0 if not fini(context.inventory) else float(context.inventory)
    inventory_ratio = inventory / max_inventory if max_inventory > 0 else 0.0

    # ── Macro : refuse de planifier quand le calendrier interdit le risque neuf.
    #    UNE SEULE REGLE, exactement celle de la porte : les cles de
    #    `MACRO_BLOCK_KEYS`, puis `allows_new_risk`. Un bloc PRESENT mais
    #    incomplet est traite comme un refus, jamais comme un laissez-passer.
    #    Cette fonction ne validait aussi le type de `score` : c'etait un critere
    #    que la porte n'applique pas, donc un bloc accepte par l'une et refuse
    #    par l'autre — et il ne servait qu'a remplir un champ que personne ne
    #    lisait. Les champs de trace restent ceux de la porte, qui les cite.
    macro = getattr(context, "macro", None)
    posture = 0.0
    if macro is not None:
        if not isinstance(macro, dict) or not MACRO_BLOCK_KEYS.issubset(macro):
            return None
        if macro.get("allows_new_risk") is not True:
            return None
        # Posture d'execution. ABSENTE vaut neutre — un producteur qui ne la
        # publie pas doit rendre exactement le comportement d'avant, au bit.
        # PRESENTE mais illisible (non finie, hors [0, 1]) refuse de planifier :
        # une posture qu'on ne sait pas lire n'est pas une posture neutre.
        declaree = macro.get(MACRO_POSTURE_KEY, 0.0)
        if not fini(declaree) or not 0.0 <= float(declaree) <= 1.0:
            return None
        posture = float(declaree)

    metadata = dict(intent.metadata or {})
    # Precedence : declaree > derivee de l'horizon > defaut. Aucune etiquette ne
    # nomme la branche gagnante : c'est la VALEUR qui la nomme, et un test la
    # fixe avec trois valeurs distinctes.
    urgency_declaree = metadata.get("urgency")
    if fini(urgency_declaree):
        urgency = borne(float(urgency_declaree), 0.0, 1.0)
    elif fini(metadata.get("horizon_ms")):
        horizon = max(1.0, float(metadata["horizon_ms"]))
        urgency = borne(1.0 - horizon / max(1.0, horizon_reference_ms), 0.0, 1.0)
    else:
        urgency = borne(float(urgency_default), 0.0, 1.0)

    # ── Effet de la posture : une tension T ramene l'agressivite a (1 - T) de sa
    #    valeur. T = 0 rend le vecteur EXACTEMENT celui d'avant (aucune valeur
    #    modifiee), donc la posture neutre est prouvable, pas promise. T = 1
    #    ramene a l'urgence nulle, qui est le palier le plus patient de la
    #    famille : la posture ne peut jamais rendre une technique plus agressive
    #    que ce que l'intention demandait.
    if posture > 0.0:
        urgency = borne(urgency * (1.0 - posture), 0.0, 1.0)

    horizon_ms = int(metadata.get("horizon_ms") or 0)
    return AdaptiveFeatures(
        spread=spread,
        spread_bps=spread_bps,
        volatility_bps=float(snapshot.volatility_bps or 0.0),
        tick_size=tick,
        quantity=quantity,
        touch=touch,
        opposite=opposite,
        mid=mid,
        microprice=microprice,
        imbalance=imbalance,
        take_depth=take_depth,
        queue_depth=queue_depth,
        depth_ratio=depth_ratio,
        inventory_ratio=inventory_ratio,
        urgency=urgency,
        baseline_spread_bps=baseline_spread_bps,
        horizon_ms=horizon_ms,
    )
