"""Risque modulé par la confiance du setup, plutôt qu'un 1 % uniforme.

L'idée
------
Traiter un setup à 2 piliers sur 4 comme un setup à 4 sur 4 revient à jeter
l'information que la porte vient de produire. Si la confluence a un sens,
alors un setup qui satisfait tous les piliers mérite plus de capital qu'un
qui en satisfait le minimum.

Ce que ce module ne fait PAS
----------------------------
Il ne touche pas au plafond. `MAX_RISK_PCT = 2 %` reste un mur : moduler
c'est répartir le risque, pas l'augmenter globalement. Un module qui pourrait
dépasser le plafond ne serait pas un module de confiance, ce serait une
suppression de garde-fou déguisée.

⚠️ Ce qu'il faut savoir avant de s'en servir
---------------------------------------------
La modulation **amplifie ce qui existe déjà**, dans les deux sens. Si les
setups 4 piliers sont réellement meilleurs, elle augmente l'espérance ; s'ils
ne le sont pas, elle accélère les pertes. Or **cette hypothèse n'est pas
vérifiée** : le registre d'edge est vide, et rien dans les mesures actuelles
n'établit que le nombre de piliers prédit le résultat.

C'est donc un pari explicite, pas une optimisation démontrée. Le journal
enregistre le multiplicateur appliqué à chaque trade, ce qui permettra de le
vérifier a posteriori — et de le retirer s'il ne tient pas.
"""

from __future__ import annotations

from dataclasses import dataclass

from titanium.sizing import MAX_RISK_PCT, TARGET_RISK_PCT

#: Plancher. Un setup au quorum minimum garde une taille réduite mais non
#: nulle : il doit continuer à produire de la donnée pour la mesure d'edge.
RISQUE_MIN_PCT = 0.50

#: Ce que vaut un setup au quorum PROD (3 piliers) — la cible historique.
RISQUE_PIVOT_PCT = 1.00

#: Ce que vaut un setup parfait. Sous le plafond dur, délibérément : le
#: plafond doit rester inatteignable par la modulation seule, pour qu'il
#: continue à jouer son rôle de dernier recours.
RISQUE_MAX_PCT = 1.75

#: Poids de la conviction du délibérateur dans le mélange. Faible, parce
#: qu'un LLM n'a pas autorité sur l'exécution : il peut nuancer la taille,
#: jamais la décider. À zéro, la modulation est purement déterministe.
POIDS_CONVICTION = 0.25


@dataclass(frozen=True)
class Confiance:
    """Verdict de confiance, traçable."""

    pct: float                 # risque retenu, en % de l'équité
    multiplicateur: float      # rapport au risque cible (1.0 = inchangé)
    piliers: int
    conviction: float
    motif: str

    def to_dict(self) -> dict:
        return {"pct": round(self.pct, 4),
                "mult": round(self.multiplicateur, 3),
                "piliers": self.piliers,
                "conviction": round(self.conviction, 3),
                "motif": self.motif}


def evaluer(piliers: int, *, total_piliers: int = 4,
            conviction: float = 0.5,
            quorum: int = 2,
            plancher: float = RISQUE_MIN_PCT,
            pivot: float = RISQUE_PIVOT_PCT,
            plafond: float = RISQUE_MAX_PCT,
            poids_conviction: float = POIDS_CONVICTION) -> Confiance:
    """Convertit un décompte de piliers en pourcentage de risque.

    L'échelle est ancrée sur trois points connus plutôt qu'interpolée sur une
    courbe arbitraire : le quorum vaut le plancher, le quorum PROD vaut la
    cible historique, le sans-faute vaut le plafond de modulation. Entre les
    deux, on interpole linéairement.

    ``conviction`` (0..1) nuance le résultat de ±``poids_conviction`` au plus.
    Elle ne peut ni faire descendre sous le plancher ni monter au-dessus du
    plafond — un délibérateur ne décide pas de la taille.
    """
    piliers = max(0, int(piliers))
    total = max(1, int(total_piliers))
    quorum = max(1, min(int(quorum), total))

    if piliers < quorum:
        # Sous le quorum il n'y a pas de trade ; si on est appelé quand même,
        # on rend le plancher plutôt que zéro — zéro serait interprété comme
        # « pas de position » par un appelant qui n'a rien demandé de tel.
        return Confiance(plancher, plancher / pivot, piliers, conviction,
                         "sous le quorum")

    if piliers >= total:
        base = plafond
        motif = "tous les piliers"
    elif piliers <= quorum:
        base = plancher
        motif = f"quorum minimum ({quorum}/{total})"
    else:
        # Interpolation entre (quorum → plancher) et (total → plafond), en
        # passant par le pivot au quorum PROD quand il tombe dessus.
        etendue = total - quorum
        avance = (piliers - quorum) / etendue
        base = plancher + (plafond - plancher) * avance
        motif = f"{piliers}/{total} piliers"

    # ── Nuance du délibérateur, bornée. `conviction` est centrée sur 0.5 :
    #    en dessous elle réduit, au-dessus elle augmente.
    conviction = min(1.0, max(0.0, float(conviction)))
    facteur = 1.0 + poids_conviction * (conviction - 0.5) * 2.0
    pct = base * facteur

    # ── Bornes dures. Le plafond de modulation d'abord, le plafond ABSOLU
    #    ensuite : deux filets, parce que celui-ci ne doit jamais céder.
    pct = min(pct, plafond)
    pct = max(pct, plancher)
    pct = min(pct, MAX_RISK_PCT)

    return Confiance(pct, pct / pivot, piliers, conviction, motif)


def piliers_de(decision) -> int:
    """Compte les piliers de confluence franchis.

    ⚠️ **Pas** le nombre de portes qui passent. Une décision en porte six —
    `data_valid` (validité des données) et `trend_sr` (obligatoire, hors
    quorum) en plus des quatre piliers de micro-structure. Compter les six
    donnait 4 sur un setup qui n'en satisfait que 2, et lui accordait donc
    le risque **maximum** alors que c'est le setup le plus faible admis.
    Défaut constaté en observation le 07/08/2026, avant tout armement.

    On reprend ici la mesure exacte de la porte (`_SUPPORT_PILLARS`) plutôt
    que de redéfinir la liste : deux définitions finiraient par diverger, et
    celle qui décide du risque doit être celle qui décide de l'entrée.
    """
    from titanium.gates.confluence_gate import _SUPPORT_PILLARS

    gates = getattr(decision, "gates", None) or []
    return sum(1 for g in gates
               if getattr(g, "name", "") in _SUPPORT_PILLARS
               and getattr(g, "passed", False))


def total_piliers() -> int:
    """Nombre de piliers de confluence — lu à la source, jamais codé en dur."""
    from titanium.gates.confluence_gate import _SUPPORT_PILLARS

    return len(_SUPPORT_PILLARS)
