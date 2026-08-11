"""Le délibérateur — convertit l'avis du graphe multi-agents en conviction.

L'orchestrateur attend une fonction ``(symbol, side, features) -> [0,1]``. Ce
module la fabrique à partir du graphe de délibération, dont la sortie utile est
une notation à 5 niveaux produite par le Portfolio Manager :

    Buy · Overweight · Hold · Underweight · Sell

CE QUE LA CONVERSION DOIT GARANTIR
-----------------------------------
Le sens de l'entrée est déjà décidé par les portes déterministes. Le graphe
n'est consulté que **sur un setup déjà validé**, et sa seule influence est la
TAILLE. La conversion doit donc mesurer une **concordance**, pas une direction :

* le graphe dit la même chose que les portes  → grosse taille ;
* le graphe est neutre                        → taille de référence ;
* le graphe dit l'inverse                     → petite taille.

POURQUOI UN PLANCHER STRICTEMENT POSITIF
-----------------------------------------
Une conviction de 0 produit `TAILLE_NULLE` côté RiskGate, c'est-à-dire un refus.
Laisser le graphe descendre à 0, ce serait lui rendre par la fenêtre le **droit
de véto** qu'on lui a refusé par la porte. Le véto appartient aux portes
déterministes et au RiskGate — pas au LLM.

`CONVICTION_FLOOR` est donc strictement positif et **configurable** : si Florent
décide un jour que la contradiction du graphe doit bloquer, il met le plancher à
0 en connaissance de cause, au lieu de le subir.

COÛT
----
Un passage du graphe coûte plusieurs dizaines d'appels LLM (~96 s mesuré sur
EURUSD). Le résultat est donc mis en cache par ``(symbole, date)`` : plusieurs
setups sur le même instrument le même jour ne relancent pas la délibération.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tradingagents.agents.utils.rating import RATINGS_5_TIER, parse_rating

#: Conviction rendue quand le graphe est neutre (Hold) ou indisponible.
NEUTRAL_CONVICTION = 0.5

#: Plancher : le graphe peut réduire fortement, jamais annuler. Voir docstring.
CONVICTION_FLOOR = 0.15

#: Biais directionnel de chaque note, de franchement haussier à franchement baissier.
RATING_BIAS: dict[str, float] = {
    "Buy": 1.0,
    "Overweight": 0.5,
    "Hold": 0.0,
    "Underweight": -0.5,
    "Sell": -1.0,
}


def rating_to_bias(rating: str) -> float:
    """Note à 5 niveaux → biais directionnel ∈ [−1, +1]. Inconnu ⇒ 0 (neutre)."""
    return RATING_BIAS.get(str(rating).strip().capitalize(), 0.0)


def conviction_from_rating(rating: str, side: int, *,
                           floor: float = CONVICTION_FLOOR) -> float:
    """Concordance entre la note du graphe et le sens des portes → conviction.

    L'accord vaut ``biais × side`` ∈ [−1, +1] : positif quand le graphe va dans
    le sens du setup, négatif quand il s'y oppose. La conviction interpole
    linéairement entre le plancher et 1.0, en passant par le neutre à 0.

    Args:
        rating: une des notes de ``RATINGS_5_TIER``.
        side: sens décidé par les portes (+1 / −1).
        floor: conviction minimale. Strictement positif par défaut — voir le
            module : laisser 0 rendrait au graphe un droit de véto.

    Returns:
        conviction ∈ [floor, 1.0].
    """
    if side not in (-1, 1):
        return NEUTRAL_CONVICTION

    accord = rating_to_bias(rating) * side          # ∈ [−1, +1]
    if accord >= 0:
        # neutre → pleine taille
        return NEUTRAL_CONVICTION + accord * (1.0 - NEUTRAL_CONVICTION)
    # neutre → plancher
    return NEUTRAL_CONVICTION + accord * (NEUTRAL_CONVICTION - floor)


@dataclass
class GraphDeliberator:
    """Délibérateur adossé au graphe multi-agents.

    Utilisable directement comme ``deliberate_fn`` de l'orchestrateur :

        delib = GraphDeliberator(trade_date="2026-08-05")
        out = run_once("EURUSD", feats, ctx, deliberate_fn=delib)

    Le graphe n'est construit qu'au premier appel — instancier ce délibérateur
    ne coûte rien et ne contacte aucun fournisseur.
    """
    trade_date: str
    config: dict | None = None
    floor: float = CONVICTION_FLOOR
    asset_type: str = "stock"
    debug: bool = False
    #: Analystes à convoquer. Les quatre par défaut, comme le socle V13.
    #:
    #: ⚠️ Convoquer les quatre sur un setup M15 de devises coûte ~116 s pour
    #: trois avis hors sujet : `fundamentals` cherche des résultats
    #: trimestriels sur une paire de devises, `social` scrute Reddit sur
    #: EURSGD. Le choix appartient à l'appelant, qui seul connaît la classe
    #: d'actif et l'horizon.
    analystes: tuple = ("market", "social", "news", "fundamentals")

    #: (symbole, date) → note rendue par le graphe. Évite de repayer un run.
    cache: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    #: Trace du dernier appel, pour le journal et le dashboard.
    last: dict = field(default_factory=dict, repr=False)

    _graph = None

    def _get_graph(self):
        if self._graph is None:
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            cfg = (self.config or DEFAULT_CONFIG).copy()
            self._graph = TradingAgentsGraph(
                selected_analysts=tuple(self.analystes),
                debug=self.debug, config=cfg)
        return self._graph

    def rating_for(self, symbol: str, contexte: str = "") -> str:
        """Note du graphe pour ce symbole à la date fixée. Mise en cache.

        ``contexte`` transmet la lecture déterministe de Titanium aux
        analystes, pour qu'ils la confrontent à leur propre recherche plutôt
        que de travailler en aveugle. Il entre dans la clé de cache : deux
        lectures différentes du même symbole sont deux questions différentes,
        et les confondre rendrait un avis périmé pour la seconde.
        """
        cle = (symbol, self.trade_date, contexte[:200])
        if cle not in self.cache:
            # `extra_context` n'est transmis que s'il existe : la signature
            # historique reste ainsi valable pour tout graphe qui ne connaît
            # pas ce paramètre — y compris les doublures de test.
            extra = {"extra_context": contexte} if contexte else {}
            _, signal = self._get_graph().propagate(symbol, self.trade_date,
                                                    asset_type=self.asset_type,
                                                    **extra)
            # `propagate` rend déjà la note ; on repasse par le parseur au cas
            # où un appelant fournirait la prose complète.
            note = signal if signal in RATINGS_5_TIER else parse_rating(str(signal))
            self.cache[cle] = note
        return self.cache[cle]

    def __call__(self, symbol: str, side: int, features: dict) -> float:
        """Interface attendue par l'orchestrateur.

        Ne rattrape volontairement aucune exception : l'orchestrateur est
        fail-closed et convertit toute erreur en conviction neutre, avec le type
        d'erreur dans la trace. Doubler ce filet ici masquerait la cause.
        """
        note = self.rating_for(symbol)
        conviction = conviction_from_rating(note, side, floor=self.floor)
        self.last = {"symbol": symbol, "side": side, "rating": note,
                     "conviction": round(conviction, 4)}
        return conviction


def static_deliberator(rating: str, *, floor: float = CONVICTION_FLOOR):
    """Délibérateur à note figée — rejeu, tests, mode dégradé.

    Permet de faire tourner la chaîne complète sans dépenser un seul appel LLM,
    par exemple pour rejouer une journée avec la note déjà connue.
    """
    def _delib(symbol: str, side: int, features: dict) -> float:
        return conviction_from_rating(rating, side, floor=floor)

    return _delib
