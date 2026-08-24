"""Prix d'entree passif de la boucle V14, sans aucune dependance courtier.

Pourquoi ce module existe
-------------------------
Le prix d'une limite d'entree etait calcule dans ``titanium/execution/
limit_orders.py``, c'est-a-dire dans le meme fichier que l'appel MetaTrader5.
Toute mesure hors ligne devait donc soit importer le courtier, soit
RECOPIER la formule. La copie a ete faite le 24/08/2026 dans la politique
``v14_live`` du simulateur, et la revue independante (Hermes H1, offset 578,
P0-3) a montre qu'elle divergeait deja de l'original sur trois points :
elle acceptait un stop nul, elle n'arrondissait pas au tick, et elle ne
verifiait pas la finitude des prix.

Un classement de politiques d'execution ou la politique « celle du bot » n'est
pas exactement celle du bot ne mesure rien. Le prix vit donc ici, seul, et les
deux appelants l'appellent au lieu de le reecrire.

Ce module ne connait ni MetaTrader5, ni compte, ni ordre : il transforme des
nombres en un nombre. Il ne peut donc rien envoyer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Confort maximal exige en plus du spread, en fraction de la distance de stop.
CONFORT_R = 0.05

#: Duree de validite selon le poids du spread dans le R, en secondes.
#: Lue de haut en bas : le premier seuil franchi gagne.
TTL_PAR_SPREAD_R = ((0.15, 120), (0.08, 300))
TTL_PLANCHER = 600


@dataclass(frozen=True)
class LimitPlan:
    price: float
    spread: float
    spread_r: float
    passive_extra: float
    saving_vs_market: float
    ttl_seconds: int


def arrondi_passif(valeur: float, *, tick: float, digits: int, side: int) -> float:
    """Arrondit TOUJOURS du cote qui ne traverse pas le carnet.

    Un achat descend au tick inferieur, une vente monte au tick superieur :
    arrondir a l'oppose transformerait une limite passive en ordre marketable
    des la premiere decimale, et l'economie mesuree serait imaginaire.
    """
    tick = float(tick)
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("TICK_INVALIDE")
    units = valeur / tick
    quantized = math.floor(units + 1e-10) if side > 0 else math.ceil(units - 1e-10)
    return round(quantized * tick, digits)


def ttl_du_spread(spread_r: float) -> int:
    """Plus le spread pese lourd dans le R, plus l'ordre attend peu."""
    for seuil, secondes in TTL_PAR_SPREAD_R:
        if spread_r > seuil:
            return secondes
    return TTL_PLANCHER


def plan_limite_entree(*, bid: float, ask: float, side: int, stop_distance: float,
                       tick: float, digits: int) -> LimitPlan:
    """Prix passif equilibrant l'economie de spread et la probabilite de fill.

    - spread <= 5 % de R : achat au bid / vente a l'ask ;
    - spread plus couteux : exige jusqu'a 5 % de R d'amelioration en plus ;
    - plus le spread pese dans R, plus la duree de validite est courte.

    Echoue FERME : un sens inconnu, un prix non fini, un prix negatif, un stop
    nul ou un carnet inverse (``ask < bid``) levent ``ValueError``. Aucune
    valeur par defaut n'est inventee : sans prix valide, il n'y a pas d'ordre.
    """
    if side not in (-1, 1):
        raise ValueError("SIDE_INVALIDE")
    valeurs = (float(bid), float(ask), float(stop_distance))
    if not all(math.isfinite(v) and v > 0 for v in valeurs) or float(ask) < float(bid):
        raise ValueError("PRIX_INVALIDE")

    bid, ask, stop_distance = valeurs
    spread = ask - bid
    spread_r = spread / stop_distance
    seuil_confort = CONFORT_R * stop_distance
    extra = min(seuil_confort, max(0.0, spread - seuil_confort))
    brut = bid - extra if side > 0 else ask + extra
    price = arrondi_passif(brut, tick=tick, digits=digits, side=side)
    # Une limite passive ne doit jamais traverser le carnet au moment du plan.
    price = min(price, bid) if side > 0 else max(price, ask)

    reference_marche = ask if side > 0 else bid
    saving = (reference_marche - price) * side
    return LimitPlan(
        price=round(price, digits),
        spread=spread,
        spread_r=spread_r,
        passive_extra=extra,
        saving_vs_market=max(0.0, saving),
        ttl_seconds=ttl_du_spread(spread_r),
    )
