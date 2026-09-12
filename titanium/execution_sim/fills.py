"""Proprietaire unique du contrat de remplissage d'une intention.

Regle, et elle n'existe qu'ici : **une intention ne peut pas etre remplie
au-dela de la quantite demandee.** Elle etait auparavant reimplementee dans
chaque executeur, et une divergence entre les deux a produit un sur-remplissage
de 66 % sur le moteur generique alors que le runner bornait correctement.

Le budget est incremental parce que le reliquat depend des remplissages REELS,
connus seulement apres soumission de chaque ordre : on ne peut pas le calculer
une fois pour toutes sur le plan.
"""

from __future__ import annotations

from enum import Enum


class Mode(Enum):
    """Comment la quantite proposee par un plan est traitee."""

    #: Le plan propose une quantite ; on la borne au reliquat. Convient aux
    #: echelles de tranches, ou chaque ordre porte sa propre taille.
    PLAN = "plan_borne"
    #: Le plan propose un ordre ; on lui substitue le reliquat entier. Convient
    #: aux escaliers d'agressivite, ou chaque etage vise la totalite restante.
    RELIQUAT = "reliquat"


class FillBudget:
    """Compteur de remplissage pour UNE intention, partage par les executeurs."""

    def __init__(self, voulue: float) -> None:
        self.voulue = float(voulue)
        self.rempli = 0.0

    @property
    def restant(self) -> float:
        return max(0.0, self.voulue - self.rempli)

    @property
    def epuise(self) -> bool:
        return self.restant <= 1e-12

    def autoriser(self, proposee: float, *, mode: Mode) -> float:
        """Quantite a envoyer pour un ordre, jamais superieure au reliquat."""
        if mode is Mode.RELIQUAT:
            return self.restant
        return min(float(proposee), self.restant)

    def enregistrer(self, remplie: float) -> None:
        self.rempli += max(0.0, float(remplie))
