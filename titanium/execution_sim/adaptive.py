"""Catalogue des techniques d'execution adaptative (V14).

Ce module est le CATALOGUE, et rien d'autre : chaque technique y declare ce qui
la caracterise (nom, hypothese, axe, complexite, fidelite) et n'implemente que
``decide``. Le contexte d'arrivee vit dans ``adaptive_features``, la mecanique
commune (echec ferme, bornage au carnet, trace) dans ``adaptive_base``.

Un seul endroit a toucher par technique : le registre, les metriques et la
sonde d'axes derivent tous de ces declarations au lieu de maintenir des tables
paralleles a tenir synchronisees a la main.

Ce module vit dans ``titanium.execution_sim``, donc hors du chemin live : il
n'importe ni MetaTrader5, ni ``mt5_executor``, ni ``.env``. Il ne peut produire
que des ordres simules.

Pourquoi une couche separee de ``policies.py``
----------------------------------------------

``policies.py`` heberge l'arene historique a quinze politiques dont les resultats
sont deja documentes (``docs/RAPPORT_EXECUTION_MATRIX_V14.md``). Ce module y
ajoute une famille distincte pour deux raisons :

1. ne pas modifier ``POLICY_REGISTRY`` ni ``ALL_POLICIES``, afin qu'un run de la
   matrice historique reste comparable a lui-meme ;
2. porter une propriete que les quinze n'ont pas : chaque technique *observe le
   contexte d'arrivee* (spread, volatilite, profondeur, inventaire, urgence) et
   *choisit* sa forme d'ordre, au lieu d'appliquer une forme fixe.

Ces techniques sont des **hypotheses mesurables**, jamais des gains acquis. Rien
ici ne prouve un edge : le catalogue rend chaque adaptation explicite,
reproductible et comparable au temoin ``market`` (voir
``tools/execution_adaptative.py`` et ``docs/PLAN_EXECUTION_ADAPTATIVE_V14.md``).

**Echec ferme.** Si une technique ne peut pas calculer une decision sure (tick
nul, carnet inverse, prix non fini, quantite non positive, reference manquante),
elle ne rend AUCUN ordre. Voir ``adaptive_features.build_features``.
"""

from __future__ import annotations

import math
from typing import Any

from titanium.execution_sim.adaptive_base import AdaptiveTechnique
from titanium.execution_sim.adaptive_features import MAX_DECIMALES, AdaptiveFeatures
from titanium.execution_sim.models import Order, OrderType


class SpreadBudgetTechnique(AdaptiveTechnique):
    """Au marche si le spread tient dans un budget, passif s'il s'elargit."""

    name = "adapt_spread_budget"
    complexity = 2.0
    fidelity = 0.70
    hypothesis = (
        "Le cout total d'un ordre au marche augmente avec le spread, alors qu'un "
        "ordre passif encaisse ce spread : au-dela de spread_budget_bps, le passif "
        "doit battre le marche."
    )

    def decide(self, intent, context, features):
        budget = float(self.config.get("spread_budget_bps", 6.0))
        ttl = float(self.config.get("ttl_seconds", 30.0))
        if features.spread_bps <= budget:
            return self._marche(
                intent, context, features, "spread_dans_budget", spread_budget_bps=budget
            )
        return self._passif(
            intent,
            context,
            features,
            features.touch,
            "spread_au_dessus_budget",
            ttl_seconds=ttl,
            spread_budget_bps=budget,
        )


class VolatilityScaledPassiveTechnique(AdaptiveTechnique):
    """Ecart passif proportionnel a la volatilite : plus profond quand ca bouge."""

    name = "adapt_volatility_scale"
    complexity = 3.0
    fidelity = 0.60
    hypothesis = (
        "A volatilite elevee, un ordre pose au touch est plus souvent traverse par "
        "du bruit defavorable : reculer de k x volatilite ameliore le prix moyen "
        "quand la participation reste faible."
    )

    def decide(self, intent, context, features):
        k = float(self.config.get("offset_k", 0.35))
        cap = float(self.config.get("max_offset_bps", 8.0))
        offset_bps = min(cap, max(0.0, k * features.volatility_bps))
        side = int(intent.side)
        price = features.touch - side * features.mid * offset_bps / 10_000.0
        return self._passif(
            intent,
            context,
            features,
            price,
            "ecart_volatilite",
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            passive_offset_bps=round(offset_bps, 6),
        )


class DepthGuardTechnique(AdaptiveTechnique):
    """Prendre quand la profondeur prenable couvre la quantite, sinon patienter."""

    name = "adapt_depth_guard"
    complexity = 3.0
    fidelity = 0.65
    hypothesis = (
        "Un ordre au marche n'est peu couteux que si le carnet prenable couvre la "
        "quantite. Sous un ratio de profondeur minimal, il faut preferer le passif."
    )

    def decide(self, intent, context, features):
        seuil = float(self.config.get("min_depth_ratio", 1.5))
        if features.depth_ratio >= seuil:
            return self._prendre(intent, context, features, "profondeur_suffisante", seuil)
        return self._passif(
            intent,
            context,
            features,
            features.touch,
            "profondeur_insuffisante",
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            min_depth_ratio=seuil,
        )

    def _prendre(self, intent, context, features, decision, seuil):
        return self._ioc(intent, context, features, decision, min_depth_ratio=seuil)


class UrgencyLadderTechnique(AdaptiveTechnique):
    """Trois paliers d'agressivite pilotes par l'urgence declaree."""

    name = "adapt_urgency_ladder"
    axis = "urgence"
    complexity = 3.0
    fidelity = 0.70
    hypothesis = (
        "L'agressivite optimale depend de l'horizon : une intention urgente doit "
        "payer pour etre remplie, une intention patiente doit poster."
    )

    def decide(self, intent, context, features):
        haut = float(self.config.get("high_urgency", 0.66))
        milieu = float(self.config.get("medium_urgency", 0.33))
        if features.urgency >= haut:
            return self._marche(
                intent, context, features, "urgence_haute", high_urgency=haut
            )
        if features.urgency >= milieu:
            return self._ioc(
                intent, context, features, "urgence_moyenne", medium_urgency=milieu
            )
        return self._passif(
            intent,
            context,
            features,
            features.touch,
            "urgence_basse",
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            medium_urgency=milieu,
        )


class DeadlineLadderTechnique(AdaptiveTechnique):
    """Echelle de tranches passives, puis cloture du reliquat a l'echeance.

    La tranche finale porte la quantite TOTALE et un marqueur ``catchup`` : le
    runner la borne elle-meme au reliquat reel (quantite voulue moins quantites
    deja remplies). C'est le seul mecanisme qui garantit la quantite sans que la
    technique ait besoin de connaitre les remplissages, et il reste deterministe.
    """

    name = "adapt_deadline_ladder"
    axis = "horizon"
    complexity = 5.0
    fidelity = 0.65
    sequential = True
    hypothesis = (
        "Decouper en tranches passives capture du spread sans exposer toute la "
        "taille, a condition qu'une tranche finale agressive garantisse la "
        "quantite avant l'echeance."
    )

    def decide(self, intent, context, features):
        tranches = max(2, int(self.config.get("slices", 3)))
        # L'horizon vient de l'INTENTION quand elle en declare un : c'est l'axe
        # d'adaptation annonce. La config n'est qu'un repli. Sans cela la
        # technique ne s'adaptait jamais a l'echeance, seulement a un reglage.
        horizon = features.horizon_ms or int(self.config.get("horizon_ms", 20_000) or 20_000)
        pas = max(1, horizon // tranches)
        part = features.quantity / tranches
        orders: list[Order] = []
        for index in range(tranches - 1):
            passif = self._passif(
                intent,
                context,
                features,
                features.touch,
                f"tranche_passive_{index + 1}/{tranches}",
                quantity=part,
                offset_ms=index * pas,
                # Chaque tranche passive expire a l'ouverture de la suivante :
                # une echelle qui laisse des enfants ouverts indefiniment
                # melangerait ses fills avec ceux de la tranche de cloture.
                ttl_seconds=pas / 1000.0,
                slice_index=index + 1,
                cancel_previous=False,
            )[0]
            orders.append(passif)
        orders.append(
            self._order(
                intent,
                context,
                # Ordre au MARCHE, volontairement : une cloture d'echeance doit
                # se remplir au prix qui prevaut alors, pas au prix d'arrivee.
                # Un IOC plafonne a l'ask d'arrivee ne se remplit plus des que
                # le marche a bouge -- mesure sur le scenario de reference.
                OrderType.MARKET,
                quantity=features.quantity,
                offset_ms=(tranches - 1) * pas,
                metadata=self._preuve(
                    features,
                    "cloture_au_marche_reliquat",
                    slice_index=tranches,
                    catchup=True,
                    cancel_previous=True,
                ),
            )
        )
        return orders


class MicropriceAnchorTechnique(AdaptiveTechnique):
    """Poste au microprice (moyenne ponderee par la profondeur)."""

    name = "adapt_microprice_anchor"
    complexity = 4.0
    fidelity = 0.50
    hypothesis = (
        "Le microprice pondere par la profondeur anticipe mieux le prochain trade "
        "que le milieu : l'ancrer ameliore le prix d'un ordre passif sans "
        "traverser le carnet."
    )

    def decide(self, intent, context, features):
        return self._passif(
            intent,
            context,
            features,
            features.microprice,
            "ancre_microprice",
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            microprice=round(features.microprice, MAX_DECIMALES),
            imbalance=round(features.imbalance, 6),
        )


class InventorySkewTechnique(AdaptiveTechnique):
    """Ecart passif incline par l'inventaire existant."""

    name = "adapt_inventory_skew"
    axis = "inventaire"
    complexity = 4.0
    fidelity = 0.55
    hypothesis = (
        "Un inventaire deja engage doit rendre les ajouts plus selectifs : incliner "
        "le prix contre son sens reduit l'exposition sans annuler l'execution."
    )

    def decide(self, intent, context, features):
        skew_bps = float(self.config.get("skew_bps", 6.0)) * features.inventory_ratio
        side = int(intent.side)
        price = features.touch - side * features.mid * skew_bps / 10_000.0
        return self._passif(
            intent,
            context,
            features,
            price,
            "inclinaison_inventaire",
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            skew_bps=round(skew_bps, 6),
        )


class CostBenefitTechnique(AdaptiveTechnique):
    """Compare l'economie attendue du passif au cout d'anti-selection."""

    name = "adapt_cost_benefit"
    complexity = 5.0
    fidelity = 0.55
    hypothesis = (
        "Le passif n'est preferable que si la demi-largeur de spread gagnee depasse "
        "le cout d'anti-selection attendu, proportionnel a la volatilite et a "
        "l'horizon."
    )

    def decide(self, intent, context, features):
        horizon_ms = float(self.config.get("horizon_ms", 5_000))
        maker_bps = float(self.config.get("maker_bps", 1.0))
        adverse_factor = float(self.config.get("adverse_vol_factor", 0.5))
        economie_bps = max(0.0, features.spread_bps / 2.0 - maker_bps)
        adverse_bps = adverse_factor * features.volatility_bps * math.sqrt(
            max(horizon_ms, 1.0) / 60_000.0
        )
        if economie_bps > adverse_bps:
            return self._passif(
                intent,
                context,
                features,
                features.touch,
                "economie_superieure",
                ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
                expected_saving_bps=round(economie_bps, 6),
                expected_adverse_bps=round(adverse_bps, 6),
            )
        return self._marche(
            intent,
            context,
            features,
            "anti_selection_dominante",
            expected_saving_bps=round(economie_bps, 6),
            expected_adverse_bps=round(adverse_bps, 6),
        )


class VolatilityAbortTechnique(AdaptiveTechnique):
    """Coupe l'execution au-dela d'un seuil de volatilite."""

    name = "adapt_volatility_abort"
    complexity = 2.0
    fidelity = 0.90
    hypothesis = (
        "Au-dela d'un regime de volatilite extreme, aucun mode d'ordre ne compense "
        "l'anti-selection : ne pas executer est la decision la moins couteuse."
    )

    def decide(self, intent, context, features):
        dur = float(self.config.get("hard_bps", 25.0))
        alerte = float(self.config.get("warn_bps", 8.0))
        if features.volatility_bps > dur:
            # Echec ferme volontaire : aucun ordre, et pas de repli.
            return []
        if features.volatility_bps > alerte:
            return self._passif(
                intent,
                context,
                features,
                features.touch,
                "volatilite_alerte",
                ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
                hard_bps=dur,
                warn_bps=alerte,
            )
        return self._marche(
            intent, context, features, "volatilite_normale", hard_bps=dur, warn_bps=alerte
        )


class DepthSliceTechnique(AdaptiveTechnique):
    """Tranche la taille en enfants dimensionnes a la profondeur visible."""

    name = "adapt_depth_slice"
    complexity = 5.0
    fidelity = 0.60
    sequential = True
    hypothesis = (
        "Limiter chaque enfant a une fraction de la profondeur prenable reduit "
        "l'impact et evite de demander au carnet plus qu'il n'affiche."
    )

    def decide(self, intent, context, features):
        fraction = float(self.config.get("depth_fraction", 0.25))
        intervalle = int(self.config.get("interval_ms", 800))
        max_tranches = max(1, int(self.config.get("max_slices", 6)))
        mini = float(self.config.get("min_slice", 0.01))
        taille = max(mini, features.take_depth * fraction)
        tranches = min(max_tranches, max(1, math.ceil(features.quantity / taille)))
        part = features.quantity / tranches
        orders = []
        for index in range(tranches):
            orders.append(
                self._passif(
                    intent,
                    context,
                    features,
                    features.touch,
                    f"tranche_profondeur_{index + 1}/{tranches}",
                    quantity=part,
                    offset_ms=index * intervalle,
                    ttl_seconds=max(0.001, intervalle / 1000.0),
                    slice_index=index + 1,
                    slice_count=tranches,
                    cancel_previous=False,
                )[0]
            )
        return orders


class ImproveTouchTechnique(AdaptiveTechnique):
    """Ameliore le touch d'un tick quand le spread le permet, sinon le rejoint."""

    name = "adapt_improve_touch"
    complexity = 3.0
    fidelity = 0.60
    hypothesis = (
        "Quand le spread depasse plusieurs ticks, ameliorer d'un tick gagne la "
        "priorite de file sans croiser ; en spread serre, ameliorer revient a "
        "payer pour rien."
    )

    def decide(self, intent, context, features):
        ticks = int(self.config.get("improve_ticks", 1))
        spread_ticks = features.spread / features.tick_size if features.tick_size else 0.0
        needs = 2 * ticks + 1
        if spread_ticks >= needs:
            price = features.touch + int(intent.side) * ticks * features.tick_size
            decision = "amelioration_tick"
        else:
            price = features.touch
            decision = "jonction_touch"
        return self._passif(
            intent,
            context,
            features,
            price,
            decision,
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            spread_ticks=round(spread_ticks, 6),
        )


class JoinTouchTechnique(AdaptiveTechnique):
    """Rejoint le touch en maker, sans jamais croiser, avec echeance."""

    name = "adapt_join_touch"
    complexity = 2.0
    fidelity = 0.65
    hypothesis = (
        "Joindre le touch est la forme passive la moins informative : elle ne "
        "revele pas de conviction et sert de temoin maker pour mesurer le cout "
        "d'un ordre au marche."
    )

    def decide(self, intent, context, features):
        return self._passif(
            intent,
            context,
            features,
            features.touch,
            "jonction_touch",
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            queue_depth=round(features.queue_depth, 6),
        )


class SpreadParticipationTechnique(AdaptiveTechnique):
    """En expansion de spread, repartit la taille en tranches proportionnelles.

    Remplace ``adapt_spread_expansion``, retiree le 12/09/2026. L'ancienne regle
    comparait le spread courant a ``reference x facteur`` : avec les valeurs par
    defaut (3,0 x 2,0) cela fait **6,0 bps**, soit EXACTEMENT le seuil absolu de
    ``adapt_spread_budget``. Les deux rendaient un resultat identique au bit
    pres sur 864/864 scenarios -- deux noms pour un comportement, exactement le
    defaut qu'un classement doit exclure.

    La difference est desormais portee par la REGLE et non par un seuil :
    ``adapt_spread_budget`` rend un ordre unique, celle-ci repartit la taille en
    tranches dont le NOMBRE suit le rapport d'expansion. Aucun reglage de seuil
    ne suffirait a les distinguer, car le harnais ne compte que deux niveaux de
    spread : deux regles a seuil y sont identiques des que leurs seuils tombent
    dans le meme intervalle.
    """

    name = "adapt_spread_participation"
    complexity = 5.0
    fidelity = 0.60
    sequential = True
    hypothesis = (
        "Plus le spread depasse sa reference, plus il paie de repartir la taille "
        "en tranches passives plutot que de tout poster d'un bloc."
    )

    def decide(self, intent, context, features):
        reference = features.baseline_spread_bps
        if reference is None or reference <= 0:
            # Reference absente : sans elle le rapport d'expansion serait invente.
            return []
        facteur = max(1.0, float(self.config.get("expansion_factor", 2.0)))
        seuil = reference * facteur
        if features.spread_bps < seuil:
            return self._marche(
                intent,
                context,
                features,
                "spread_normal",
                baseline_spread_bps=reference,
                expansion_threshold_bps=round(seuil, 6),
            )
        ratio = features.spread_bps / reference
        maximum = max(2, int(self.config.get("max_slices", 5)))
        tranches = max(2, min(maximum, int(round(ratio))))
        intervalle = int(self.config.get("interval_ms", 700))
        part = features.quantity / tranches
        return [
            self._passif(
                intent,
                context,
                features,
                features.touch,
                f"tranche_expansion_{index + 1}/{tranches}",
                quantity=part,
                offset_ms=index * intervalle,
                ttl_seconds=max(0.001, intervalle / 1000.0),
                slice_index=index + 1,
                slice_count=tranches,
                expansion_ratio=round(ratio, 6),
                cancel_previous=False,
            )[0]
            for index in range(tranches)
        ]


class SizePatienceTechnique(AdaptiveTechnique):
    """Petite taille au marche, grosse taille tranchee en passif."""

    name = "adapt_size_patience"
    complexity = 5.0
    fidelity = 0.60
    sequential = True
    hypothesis = (
        "Le cout d'impact croit avec la taille : une petite intention doit etre "
        "executee vite, une grosse intention doit etre fractionnee pour ne pas "
        "payer sa propre demande."
    )

    def decide(self, intent, context, features):
        petite = float(self.config.get("small_size", 2.0))
        if features.quantity <= petite:
            return self._ioc(
                intent, context, features, "petite_taille_rapide", small_size=petite
            )
        fraction = float(self.config.get("depth_fraction", 0.25))
        intervalle = int(self.config.get("interval_ms", 800))
        max_tranches = max(1, int(self.config.get("max_slices", 6)))
        taille = max(float(self.config.get("min_slice", 0.01)), features.take_depth * fraction)
        tranches = min(max_tranches, max(1, math.ceil(features.quantity / taille)))
        part = features.quantity / tranches
        return [
            self._passif(
                intent,
                context,
                features,
                features.touch,
                f"grosse_taille_tranchee_{index + 1}/{tranches}",
                quantity=part,
                offset_ms=index * intervalle,
                ttl_seconds=max(0.001, intervalle / 1000.0),
                slice_index=index + 1,
                slice_count=tranches,
                cancel_previous=False,
                small_size=petite,
            )[0]
            for index in range(tranches)
        ]


class MidpointAggressiveTechnique(AdaptiveTechnique):
    """Au milieu en spread large, au touch en spread normal."""

    name = "adapt_midpoint_aggressive"
    complexity = 3.0
    fidelity = 0.60
    hypothesis = (
        "Poster au milieu n'est rentable que si la demi-largeur gagnee depasse le "
        "risque de non-execution ; en spread normal, le touch suffit."
    )

    def decide(self, intent, context, features):
        seuil_ticks = float(self.config.get("wide_ticks", 4.0))
        spread_ticks = features.spread / features.tick_size if features.tick_size else 0.0
        if spread_ticks >= seuil_ticks:
            return self._passif(
                intent,
                context,
                features,
                features.mid,
                "milieu_spread_large",
                ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
                spread_ticks=round(spread_ticks, 6),
            )
        return self._passif(
            intent,
            context,
            features,
            features.touch,
            "touch_spread_normal",
            ttl_seconds=float(self.config.get("ttl_seconds", 30.0)),
            spread_ticks=round(spread_ticks, 6),
        )


class LadderMakerTakerTechnique(AdaptiveTechnique):
    """Tentative maker bornee, puis cloture au taker du reliquat."""

    name = "adapt_ladder_maker_taker"
    axis = "horizon"
    complexity = 6.0
    fidelity = 0.50
    sequential = True
    hypothesis = (
        "Une tentative maker limitee dans le temps capture le spread quand elle "
        "aboutit, et ne coute que le taker sur le reliquat quand elle echoue."
    )

    def decide(self, intent, context, features):
        tranches = max(1, int(self.config.get("slices", 2)))
        # Meme regle que la technique d'echeance : l'horizon declare prime.
        horizon = features.horizon_ms or int(self.config.get("horizon_ms", 10_000) or 10_000)
        pas = max(1, horizon // (tranches + 1))
        part = features.quantity / (tranches + 1)
        orders: list[Order] = []
        for index in range(tranches):
            orders.append(
                self._passif(
                    intent,
                    context,
                    features,
                    features.touch,
                    f"maker_{index + 1}/{tranches}",
                    kind=OrderType.POST_ONLY,
                    quantity=part,
                    offset_ms=index * pas,
                    ttl_seconds=pas / 1000.0,
                    slice_index=index + 1,
                    cancel_previous=False,
                )[0]
            )
        orders.append(
            self._order(
                intent,
                context,
                # Ordre au marche du reliquat, meme raison que la technique
                # d'echeance : au prix qui prevaut au moment du declenchement.
                OrderType.MARKET,
                # Meme mecanisme de rattrapage que la technique d'echeance : la
                # quantite totale est bornee par le runner au reliquat reel.
                quantity=features.quantity,
                offset_ms=tranches * pas,
                metadata=self._preuve(
                    features,
                    "cloture_au_marche_reliquat",
                    slice_index=tranches + 1,
                    catchup=True,
                    cancel_previous=True,
                ),
            )
        )
        return orders


class AdaptiveSelectorTechnique(AdaptiveTechnique):
    """Meta-selection : choisit une technique selon le contexte d'arrivee.

    C'est la brique qui rend le systeme *adaptatif* au sens fort : la forme
    d'ordre n'est pas choisie a l'avance, elle est arbitree a l'arrivee. La
    selection reste deterministe et explicite pour etre auditable.
    """

    name = "adapt_selector"
    complexity = 7.0
    fidelity = 0.60
    hypothesis = (
        "Aucune forme d'ordre ne domine tous les regimes : un arbitrage explicite "
        "spread/profondeur/urgence doit battre chacune des techniques prises "
        "seule, si l'arbitrage lui-meme n'introduit pas de biais."
    )

    def decide(self, intent, context, features):
        cible = self._choisir(features)
        # La technique deleguee herite de la config partagee (reference de
        # spread, inventaire, frais) : sans cela, une delegation vers une
        # technique qui exige une reference echouerait ferme et le selecteur ne
        # traderait jamais.
        delegue = {**self.config, **dict(self.config.get("delegate", {}))}
        technique = ADAPTIVE_POLICY_REGISTRY[cible](delegue)
        orders = technique.plan(intent, context)
        for order in orders:
            order.metadata["selected_technique"] = cible
        return orders

    def _choisir(self, features: AdaptiveFeatures) -> str:
        """Regle d'arbitrage PREENREGISTREE, pas ajustee sur la mesure.

        Elle suit un ordre de priorite de surete puis de qualite : proteger
        d'abord (volatilite extreme), honorer ensuite l'echeance (urgence),
        puis preferer la technique dont la mesure conditionnelle est la
        meilleure dans le regime courant.

        La version initiale retombait sur ``adapt_join_touch`` en regime
        normal. Mesure : le selecteur rendait EXACTEMENT le meme delta que ce
        repli, qui est domine par ``adapt_midpoint_aggressive``. Un arbitrage
        dont le repli est domine n'arbitre rien ; c'est ce que la mesure a
        montre, et non une intuition.
        """
        profondeur_min = float(self.config.get("min_depth_ratio", 1.5))
        urgence_haute = float(self.config.get("high_urgency", 0.6))
        if features.volatility_bps > float(self.config.get("abort_volatility_bps", 25.0)):
            return "adapt_volatility_abort"
        if features.urgency >= urgence_haute:
            return "adapt_urgency_ladder"
        if features.depth_ratio < profondeur_min:
            return "adapt_depth_guard"
        # En regime calme, cette technique poste au touch ; en spread large,
        # elle poste au milieu. Elle subsume donc la jonction simple.
        return "adapt_midpoint_aggressive"


TECHNIQUES: tuple[type[AdaptiveTechnique], ...] = (
    SpreadBudgetTechnique,
    VolatilityScaledPassiveTechnique,
    DepthGuardTechnique,
    UrgencyLadderTechnique,
    DeadlineLadderTechnique,
    MicropriceAnchorTechnique,
    InventorySkewTechnique,
    CostBenefitTechnique,
    VolatilityAbortTechnique,
    DepthSliceTechnique,
    ImproveTouchTechnique,
    JoinTouchTechnique,
    SpreadParticipationTechnique,
    SizePatienceTechnique,
    MidpointAggressiveTechnique,
    LadderMakerTakerTechnique,
    AdaptiveSelectorTechnique,
)

ADAPTIVE_POLICY_REGISTRY: dict[str, type[AdaptiveTechnique]] = {
    cls.name: cls for cls in TECHNIQUES
}

ADAPTIVE_POLICIES: tuple[str, ...] = tuple(ADAPTIVE_POLICY_REGISTRY)

SEQUENTIAL_ADAPTIVE_POLICIES: frozenset[str] = frozenset(
    name for name, cls in ADAPTIVE_POLICY_REGISTRY.items() if cls.sequential
)

ADAPTIVE_CATALOG: tuple[dict[str, str], ...] = tuple(
    {"name": cls.name, "hypothesis": cls.hypothesis} for cls in TECHNIQUES
)

#: Axes d'adaptation DECLARES par les techniques, derives de leurs attributs.
#: La sonde d'axes de ``tools/execution_adaptative.py`` lit cette table : il n'y
#: a plus de dictionnaire parallele a tenir synchronise a la main.
AXE_DECLARE: dict[str, str] = {
    cls.name: cls.axis for cls in TECHNIQUES if cls.axis
}


def get_adaptive_technique(name: str, config: dict[str, Any] | None = None) -> AdaptiveTechnique:
    try:
        return ADAPTIVE_POLICY_REGISTRY[name](config)
    except KeyError as exc:
        raise ValueError(f"unknown adaptive technique: {name}") from exc
