"""Profils comportementaux ICT par classe d'actif — l'intelligence derrière les prix.

CE QUE CE MODULE COMPREND
--------------------------
Derrière chaque bougie, il y a des ÊTRES HUMAINS — des traders retail sous
l'émotion, des algorithmes institutionnels qui exécutent des mandats, des market
makers qui fournissent la liquidité, et des banques centrales qui gouvernent les
cycles. Ce module encode cette réalité.

Un prix n'est pas un nombre : c'est le RÉSULTAT d'une négociation entre des
participants qui ont des motivations, des contraintes, et des horizons différents.
La même bougie sur EURUSD et sur BTCUSD ne raconte pas la même histoire, parce
que les participants ne sont pas les mêmes.

PHILOSOPHIE ICT
---------------
Inner Circle Trader enseigne que le marché n'est PAS aléatoire. Les grandes
institutions (les « Smart Money ») laissent des empreintes lisibles :

1. **Manipulation** — elles créent de faux mouvements pour déclencher les stops
   du retail et accumuler à meilleur prix. C'est le « Judas Swing ».

2. **Displacement** — quand elles ont accumulé, elles déplacent le prix avec
   violence. Ce mouvement crée des FVG (Fair Value Gaps) qui sont les traces
   de leur passage.

3. **Distribution** — elles livrent leurs positions à ceux qui achètent
   l'euphorie ou vendent la panique.

Le bot doit reconnaître ces phases et s'ADAPTER à chaque actif, parce que
chaque marché a ses propres institutions, ses propres horaires, et ses propres
schémas de manipulation.

ARCHITECTURE
------------
Ce module fournit :
- ``AssetProfile`` : le profil comportemental d'un actif
- ``classify_asset()`` : classification fine (pas juste forex/crypto/indice)
- ``session_context()`` : quelle session domine, quelle liquidité attendre
- ``manipulation_profile()`` : comment les institutions manipulent CET actif
- ``optimal_conditions()`` : conditions idéales pour trader CET actif avec ICT
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# PARTICIPANTS — qui est derrière les prix ?
# ═══════════════════════════════════════════════════════════════════════════════

class Participant(Enum):
    """Les catégories de participants sur un marché."""
    CENTRAL_BANK = "banque_centrale"        # politique monétaire, interventions
    COMMERCIAL_BANK = "banque_commerciale"   # flux clients, hedging corporate
    HEDGE_FUND = "hedge_fund"               # spéculatif, algorithmes, momentum
    MARKET_MAKER = "market_maker"           # fournit la liquidité, cherche le spread
    ASSET_MANAGER = "asset_manager"         # flux longs, rebalancing
    RETAIL = "retail"                       # émotionnel, stops prévisibles
    ALGORITHMIC = "algorithmique"           # HFT, arbitrage, stat-arb
    CORPORATE = "corporate"                # hedging commercial, flux réels
    SOVEREIGN = "souverain"                # fonds souverains, réserves de change


class MarketRegime(Enum):
    """Régime de marché ICT."""
    ACCUMULATION = "accumulation"       # Smart Money construit sa position
    MANIPULATION = "manipulation"       # faux mouvement pour piéger le retail
    DISTRIBUTION = "distribution"       # Smart Money livre sa position
    EXPANSION = "expansion"             # mouvement directionnel franc
    RETRACEMENT = "retracement"         # retour vers la valeur (OTE)
    CONSOLIDATION = "consolidation"     # range, pas de direction


class SessionType(Enum):
    """Sessions de trading ICT."""
    ASIAN = "asian"           # 00:00-08:00 UTC — range, accumulation
    LONDON = "london"         # 07:00-16:00 UTC — manipulation + expansion
    NEW_YORK = "new_york"     # 12:00-21:00 UTC — continuation ou reversal
    LONDON_CLOSE = "london_close"  # 15:00-17:00 UTC — fin de journée
    OVERLAP = "overlap"       # 12:00-16:00 UTC — max liquidité


# ═══════════════════════════════════════════════════════════════════════════════
# KILL ZONES — quand trader, pas seulement quoi
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class KillZone:
    """Fenêtre horaire optimale pour un type de setup ICT."""
    name: str
    start_utc: int          # heure UTC de début
    end_utc: int            # heure UTC de fin
    setup_types: tuple      # types de setup favorisés
    manipulation_prob: float  # probabilité de Judas Swing dans cette fenêtre
    description: str


# Kill Zones canoniques ICT
ASIAN_KZ = KillZone(
    "Asian Kill Zone", 0, 8,
    ("range_definition", "liquidity_pool_formation"),
    0.3,
    "La session asiatique DEFINIT le range que Londres va manipuler. "
    "Les hauts et bas asiatiques deviennent les cibles de liquidité du matin."
)

LONDON_OPEN_KZ = KillZone(
    "London Open Kill Zone", 7, 10,
    ("judas_swing", "manipulation", "expansion"),
    0.7,
    "L'ouverture de Londres est la fenêtre de MANIPULATION par excellence. "
    "Les institutions balaient la liquidité asiatique (stops au-dessus des hauts "
    "ou en dessous des bas asiatiques) avant de lancer le vrai mouvement."
)

NY_OPEN_KZ = KillZone(
    "New York Open Kill Zone", 12, 15,
    ("continuation", "reversal", "expansion"),
    0.5,
    "New York soit CONTINUE le mouvement de Londres (c'est le cas le plus rentable), "
    "soit le REVERSE si Londres a sur-étendu. La liquidité maximale est dans "
    "l'overlap London-NY (12-16 UTC)."
)

LONDON_CLOSE_KZ = KillZone(
    "London Close Kill Zone", 15, 17,
    ("reversal", "profit_taking"),
    0.4,
    "La clôture de Londres voit les institutions prendre leurs profits. "
    "Les mouvements intraday s'inversent souvent ici. Pas de nouvelles positions — "
    "c'est une fenêtre de SORTIE, pas d'entrée."
)

ALL_KILL_ZONES = [ASIAN_KZ, LONDON_OPEN_KZ, NY_OPEN_KZ, LONDON_CLOSE_KZ]


def current_session(now: datetime | None = None) -> SessionType:
    """Session active à l'instant donné."""
    if now is None:
        now = datetime.now(timezone.utc)
    h = now.hour
    if 0 <= h < 7:
        return SessionType.ASIAN
    if 7 <= h < 12:
        return SessionType.LONDON
    if 12 <= h < 16:
        return SessionType.OVERLAP
    if 16 <= h < 17:
        return SessionType.LONDON_CLOSE
    if 17 <= h < 21:
        return SessionType.NEW_YORK
    return SessionType.ASIAN  # nuit = prochain cycle asiatique


def active_kill_zones(now: datetime | None = None) -> list[KillZone]:
    """Kill zones actives à l'instant donné."""
    if now is None:
        now = datetime.now(timezone.utc)
    h = now.hour
    return [kz for kz in ALL_KILL_ZONES if kz.start_utc <= h < kz.end_utc]


# ═══════════════════════════════════════════════════════════════════════════════
# PROFILS D'ACTIFS — chaque marché a sa personnalité
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AssetProfile:
    """Profil comportemental ICT d'un actif.

    Ce n'est PAS une fiche technique — c'est une description de COMMENT
    les participants interagissent sur cet actif, et ce que ça implique
    pour la détection ICT.
    """
    symbol: str
    asset_class: str            # forex_major, forex_minor, forex_exotic, etc.
    display_name: str

    # Qui domine ce marché ?
    dominant_participants: list[str] = field(default_factory=list)

    # Comportement de manipulation
    manipulation_intensity: float = 0.5    # 0=faible, 1=intense
    judas_swing_frequency: str = "moderate"  # rare/moderate/frequent
    stop_hunt_depth_atr: float = 0.5       # profondeur typique des chasses aux stops

    # Sessions optimales
    primary_session: str = "london"        # session principale de liquidité
    secondary_session: str = "new_york"
    avoid_session: str = "asian"           # session à éviter pour ce type d'actif

    # Caractéristiques ICT
    fvg_fill_rate: float = 0.7             # taux de comblement des FVG (0-1)
    ob_respect_rate: float = 0.6           # taux de réaction aux Order Blocks
    displacement_frequency: str = "moderate"  # fréquence des displacements

    # Profil de volatilité
    atr_stability: float = 0.5            # stabilité de l'ATR (0=erratique, 1=stable)
    spread_sensitivity: str = "low"        # sensibilité au spread

    @property
    def spread_sensitivity_score(self) -> float:
        """0-1 : plus c'est haut, plus le spread pénalise."""
        return {"low": 0.1, "medium": 0.3, "high": 0.6}.get(self.spread_sensitivity, 0.3)

    # Intelligence contextuelle
    drivers: list[str] = field(default_factory=list)  # ce qui fait bouger cet actif
    correlation_cluster: str = ""          # famille de corrélation naturelle
    institutional_flow_readable: bool = True  # les flux institutionnels sont-ils lisibles ?

    # Recommandations ICT
    best_setup_types: list[str] = field(default_factory=list)
    rr_optimal: float = 2.0               # R:R optimal pour cet actif
    timeframe_entry: str = "M15"           # TF d'entrée recommandé
    timeframe_structure: str = "H4"        # TF de structure recommandé

    # Précautions
    warnings: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# BASE DE CONNAISSANCES — profils par famille d'actifs
# ═══════════════════════════════════════════════════════════════════════════════

def _forex_major_profile(symbol: str, name: str, **kw) -> AssetProfile:
    """Paires majeures : les plus liquides, les plus lisibles en ICT."""
    defaults = dict(
        asset_class="forex_major",
        dominant_participants=["banque_centrale", "banque_commerciale", "hedge_fund", "market_maker"],
        manipulation_intensity=0.6,
        judas_swing_frequency="frequent",
        stop_hunt_depth_atr=0.4,
        primary_session="london",
        secondary_session="new_york",
        avoid_session="late_ny",
        fvg_fill_rate=0.75,
        ob_respect_rate=0.65,
        displacement_frequency="moderate",
        atr_stability=0.7,
        spread_sensitivity="low",
        institutional_flow_readable=True,
        best_setup_types=["continuation", "reversal_on_sweep"],
        rr_optimal=2.0,
        timeframe_entry="M15",
        timeframe_structure="H4",
    )
    defaults.update(kw)
    return AssetProfile(symbol=symbol, display_name=name, **defaults)


def _forex_cross_profile(symbol: str, name: str, **kw) -> AssetProfile:
    """Crosses : moins liquides, mais schémas ICT respectés."""
    defaults = dict(
        asset_class="forex_cross",
        dominant_participants=["banque_commerciale", "hedge_fund", "corporate"],
        manipulation_intensity=0.5,
        judas_swing_frequency="moderate",
        stop_hunt_depth_atr=0.5,
        primary_session="london",
        secondary_session="new_york",
        avoid_session="asian",
        fvg_fill_rate=0.70,
        ob_respect_rate=0.60,
        displacement_frequency="moderate",
        atr_stability=0.6,
        spread_sensitivity="medium",
        institutional_flow_readable=True,
        best_setup_types=["continuation"],
        rr_optimal=2.0,
        timeframe_entry="M15",
        timeframe_structure="H4",
    )
    defaults.update(kw)
    return AssetProfile(symbol=symbol, display_name=name, **defaults)


def _forex_exotic_profile(symbol: str, name: str, **kw) -> AssetProfile:
    """Exotiques : liquidité réduite, spreads larges, manipulation différente."""
    defaults = dict(
        asset_class="forex_exotic",
        dominant_participants=["banque_centrale", "souverain", "corporate"],
        manipulation_intensity=0.3,
        judas_swing_frequency="rare",
        stop_hunt_depth_atr=0.8,
        primary_session="london",
        secondary_session="new_york",
        avoid_session="asian",
        fvg_fill_rate=0.55,
        ob_respect_rate=0.45,
        displacement_frequency="rare",
        atr_stability=0.4,
        spread_sensitivity="high",
        institutional_flow_readable=False,
        best_setup_types=["continuation"],
        rr_optimal=2.5,
        timeframe_entry="H1",
        timeframe_structure="D1",
        warnings=["spread élevé — vérifier le coût relatif avant toute entrée",
                   "liquidité faible hors Londres — slippage probable",
                   "interventions de banque centrale imprévisibles"],
    )
    defaults.update(kw)
    return AssetProfile(symbol=symbol, display_name=name, **defaults)


def _index_profile(symbol: str, name: str, **kw) -> AssetProfile:
    """Indices boursiers : pilotés par les flux institutionnels et la macro."""
    defaults = dict(
        asset_class="index",
        dominant_participants=["asset_manager", "hedge_fund", "algorithmique", "market_maker"],
        manipulation_intensity=0.7,
        judas_swing_frequency="frequent",
        stop_hunt_depth_atr=0.3,
        primary_session="new_york" if "US" in symbol or "NAS" in symbol or "S&P" in symbol else "london",
        secondary_session="london" if "US" in symbol or "NAS" in symbol else "new_york",
        avoid_session="asian",
        fvg_fill_rate=0.80,
        ob_respect_rate=0.70,
        displacement_frequency="frequent",
        atr_stability=0.6,
        spread_sensitivity="medium",
        institutional_flow_readable=True,
        best_setup_types=["continuation", "reversal_on_sweep", "gap_fill"],
        rr_optimal=2.0,
        timeframe_entry="M15",
        timeframe_structure="H4",
        drivers=["earnings", "fed", "risk_sentiment", "sector_rotation"],
    )
    defaults.update(kw)
    return AssetProfile(symbol=symbol, display_name=name, **defaults)


def _crypto_profile(symbol: str, name: str, **kw) -> AssetProfile:
    """Crypto : 24/7, retail dominant, manipulation intense par les whales."""
    defaults = dict(
        asset_class="crypto",
        dominant_participants=["retail", "hedge_fund", "algorithmique"],
        manipulation_intensity=0.9,
        judas_swing_frequency="frequent",
        stop_hunt_depth_atr=0.6,
        primary_session="new_york",
        secondary_session="asian",
        avoid_session="",
        fvg_fill_rate=0.65,
        ob_respect_rate=0.55,
        displacement_frequency="frequent",
        atr_stability=0.3,
        spread_sensitivity="medium",
        institutional_flow_readable=False,
        best_setup_types=["sweep_reversal", "displacement_continuation"],
        rr_optimal=2.0,
        timeframe_entry="M15",
        timeframe_structure="H4",
        drivers=["sentiment", "regulatory_news", "btc_dominance", "whale_activity"],
        warnings=["volatilité extrême — ATR instable, adapter le sizing",
                   "manipulation whale : les mèches sont plus profondes",
                   "24/7 : pas de session dominante claire, mais NY reste le pic"],
    )
    defaults.update(kw)
    return AssetProfile(symbol=symbol, display_name=name, **defaults)


def _commodity_profile(symbol: str, name: str, **kw) -> AssetProfile:
    """Commodities : flux réels + spéculatif, saisonnalité."""
    defaults = dict(
        asset_class="commodity",
        dominant_participants=["corporate", "hedge_fund", "souverain", "algorithmique"],
        manipulation_intensity=0.5,
        judas_swing_frequency="moderate",
        stop_hunt_depth_atr=0.5,
        primary_session="london",
        secondary_session="new_york",
        avoid_session="asian",
        fvg_fill_rate=0.70,
        ob_respect_rate=0.60,
        displacement_frequency="moderate",
        atr_stability=0.5,
        spread_sensitivity="medium",
        institutional_flow_readable=True,
        best_setup_types=["continuation", "range_breakout"],
        rr_optimal=2.0,
        timeframe_entry="M15",
        timeframe_structure="H4",
        drivers=["supply_demand", "geopolitics", "usd_strength", "seasonality"],
    )
    defaults.update(kw)
    return AssetProfile(symbol=symbol, display_name=name, **defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRE DES ACTIFS CONNUS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_registry() -> dict[str, AssetProfile]:
    """Construit le registre des profils connus."""
    reg: dict[str, AssetProfile] = {}

    # ── FOREX MAJEURS ──────────────────────────────────────────────────────

    reg["EURUSD"] = _forex_major_profile("EURUSD", "Euro / Dollar US",
        drivers=["BCE", "Fed", "différentiel_taux", "PMI", "NFP"],
        correlation_cluster="anti_dollar",
        manipulation_intensity=0.65,
        fvg_fill_rate=0.78,
        ob_respect_rate=0.68,
        warnings=["paire la plus liquide — manipulation subtile mais constante",
                   "BCE et Fed sont les deux moteurs ; ignorer les fondamentaux = voler à l'aveugle"])

    reg["GBPUSD"] = _forex_major_profile("GBPUSD", "Livre Sterling / Dollar US",
        drivers=["BoE", "Fed", "données_UK", "Brexit_legacy"],
        correlation_cluster="anti_dollar",
        manipulation_intensity=0.75,
        judas_swing_frequency="frequent",
        stop_hunt_depth_atr=0.5,
        rr_optimal=2.0,
        warnings=["câble = volatil — les Judas Swings sont plus profonds que sur EUR",
                   "session de Londres DOMINANTE — ne pas trader en asiatique"])

    reg["USDJPY"] = _forex_major_profile("USDJPY", "Dollar US / Yen Japonais",
        drivers=["BoJ", "Fed", "différentiel_taux", "risk_sentiment", "intervention_BoJ"],
        correlation_cluster="yen_carry",
        manipulation_intensity=0.5,
        primary_session="asian",
        secondary_session="new_york",
        avoid_session="london_close",
        warnings=["la BoJ peut intervenir SANS prévenir — niveaux psycho 150/155/160 critiques",
                   "carry trade : les mouvements de risk-off créent des cascades brutales"])

    reg["USDCHF"] = _forex_major_profile("USDCHF", "Dollar US / Franc Suisse",
        drivers=["BNS", "Fed", "risk_sentiment", "flux_refuge"],
        correlation_cluster="dollar_index",
        manipulation_intensity=0.4,
        displacement_frequency="rare",
        warnings=["valeur refuge — les mouvements sont souvent CONTRA les marchés actions",
                   "BNS : intervient discrètement pour plafonner le franc"])

    reg["AUDUSD"] = _forex_major_profile("AUDUSD", "Dollar Australien / Dollar US",
        drivers=["RBA", "commodities", "Chine", "risk_sentiment"],
        correlation_cluster="commodity_currencies",
        primary_session="asian",
        secondary_session="london",
        manipulation_intensity=0.5,
        warnings=["proxy de la Chine — données chinoises la font bouger la nuit",
                   "session asiatique ACTIVE pour cette paire"])

    reg["USDCAD"] = _forex_major_profile("USDCAD", "Dollar US / Dollar Canadien",
        drivers=["BoC", "pétrole", "Fed", "données_emploi_CA"],
        correlation_cluster="oil_sensitive",
        manipulation_intensity=0.5,
        warnings=["corrélé au pétrole — un spike WTI fait bouger USDCAD",
                   "session NY dominante car les flux pétroliers sont en USD"])

    reg["NZDUSD"] = _forex_major_profile("NZDUSD", "Dollar Néo-Zélandais / Dollar US",
        drivers=["RBNZ", "dairy_prices", "risk_sentiment", "Chine"],
        correlation_cluster="commodity_currencies",
        primary_session="asian",
        manipulation_intensity=0.45,
        spread_sensitivity="medium",
        warnings=["liquidité plus faible que AUD — spreads élargis hors session",
                   "paire de carry : sensible au risk-on/risk-off"])

    # ── FOREX CROSSES ──────────────────────────────────────────────────────

    reg["EURGBP"] = _forex_cross_profile("EURGBP", "Euro / Livre Sterling",
        drivers=["BCE_vs_BoE", "données_UK_vs_zone_euro"],
        correlation_cluster="europe",
        spread_sensitivity="high",
        warnings=["spread ÉLEVÉ relatif à l'ATR — LEÇON E2 : 21% du risque mangé",
                   "range étroit — ne convient qu'aux micro-mouvements"])

    reg["EURJPY"] = _forex_cross_profile("EURJPY", "Euro / Yen Japonais",
        drivers=["BCE", "BoJ", "risk_sentiment"],
        correlation_cluster="yen_carry",
        manipulation_intensity=0.6,
        rr_optimal=2.0,
        warnings=["volatil — combine la direction EUR et le sentiment risk-on/off"])

    reg["GBPJPY"] = _forex_cross_profile("GBPJPY", "Livre / Yen Japonais",
        drivers=["BoE", "BoJ", "risk_sentiment"],
        correlation_cluster="yen_carry",
        manipulation_intensity=0.7,
        judas_swing_frequency="frequent",
        stop_hunt_depth_atr=0.6,
        rr_optimal=2.5,
        warnings=["surnommé « the beast » — mèches TRÈS profondes",
                   "les sweeps sont violents mais les expansions aussi — favorise un R:R plus haut"])

    reg["AUDJPY"] = _forex_cross_profile("AUDJPY", "Dollar Australien / Yen",
        drivers=["risk_sentiment", "RBA", "BoJ", "Chine"],
        correlation_cluster="yen_carry",
        primary_session="asian",
        manipulation_intensity=0.5)

    reg["EURAUD"] = _forex_cross_profile("EURAUD", "Euro / Dollar Australien",
        drivers=["BCE", "RBA", "Chine", "risk_sentiment"],
        manipulation_intensity=0.5,
        spread_sensitivity="medium")

    reg["EURNZD"] = _forex_cross_profile("EURNZD", "Euro / Dollar NZ",
        drivers=["BCE", "RBNZ", "dairy"],
        spread_sensitivity="high",
        warnings=["spread élevé — vérifier le coût avant entrée"])

    reg["EURCAD"] = _forex_cross_profile("EURCAD", "Euro / Dollar Canadien",
        drivers=["BCE", "BoC", "pétrole"],
        correlation_cluster="oil_sensitive",
        fvg_fill_rate=0.72,
        ob_respect_rate=0.62)

    reg["AUDCAD"] = _forex_cross_profile("AUDCAD", "Dollar AU / Dollar CA",
        drivers=["commodities", "RBA", "BoC"],
        correlation_cluster="commodity_currencies",
        primary_session="asian")

    reg["NZDCHF"] = _forex_cross_profile("NZDCHF", "Dollar NZ / Franc Suisse",
        drivers=["RBNZ", "BNS", "risk_sentiment"],
        spread_sensitivity="high")

    reg["CADCHF"] = _forex_cross_profile("CADCHF", "Dollar CA / Franc Suisse",
        drivers=["BoC", "BNS", "pétrole"],
        spread_sensitivity="medium")

    reg["CHFSEK"] = _forex_exotic_profile("CHFSEK", "Franc Suisse / Couronne Suédoise",
        drivers=["BNS", "Riksbank", "zone_euro"],
        spread_sensitivity="high",
        warnings=["exotique nordique — liquidité limitée, spreads imprévisibles"])

    reg["SGDJPY"] = _forex_exotic_profile("SGDJPY", "Dollar Singapour / Yen",
        drivers=["MAS", "BoJ", "risk_sentiment_asie"],
        primary_session="asian",
        warnings=["cross asiatique — actif principalement pendant Tokyo"])

    reg["AUDSGD"] = _forex_exotic_profile("AUDSGD", "Dollar AU / Dollar Singapour",
        drivers=["RBA", "MAS", "Chine"],
        primary_session="asian")

    reg["USDSGD"] = _forex_exotic_profile("USDSGD", "Dollar US / Dollar Singapour",
        drivers=["Fed", "MAS", "commerce_asiatique"],
        primary_session="asian")

    reg["EURSGD"] = _forex_exotic_profile("EURSGD", "Euro / Dollar Singapour",
        drivers=["BCE", "MAS"],
        primary_session="london")

    reg["EURNOK"] = _forex_exotic_profile("EURNOK", "Euro / Couronne Norvégienne",
        drivers=["BCE", "Norges_Bank", "pétrole_Brent"],
        correlation_cluster="oil_sensitive",
        primary_session="london",
        warnings=["proxy pétrolier nordique — corrélé au Brent"])

    reg["GBPNOK"] = _forex_exotic_profile("GBPNOK", "Livre / Couronne Norvégienne",
        drivers=["BoE", "Norges_Bank", "pétrole"],
        spread_sensitivity="high")

    reg["USDNOK"] = _forex_exotic_profile("USDNOK", "Dollar US / Couronne Norvégienne",
        drivers=["Fed", "Norges_Bank", "pétrole"],
        correlation_cluster="oil_sensitive")

    reg["EURHUF"] = _forex_exotic_profile("EURHUF", "Euro / Forint Hongrois",
        drivers=["BCE", "MNB", "risque_CEE"],
        warnings=["politique monétaire hongroise volatile — taux directeurs imprévisibles"])

    reg["USDINR"] = _forex_exotic_profile("USDINR", "Dollar US / Roupie Indienne",
        drivers=["Fed", "RBI", "flux_capitaux_EM"],
        primary_session="asian",
        warnings=["RBI intervient activement — bande implicite autour de niveaux ronds"])

    reg["USDKRW"] = _forex_exotic_profile("USDKRW", "Dollar US / Won Coréen",
        drivers=["Fed", "BoK", "tech_exports"],
        primary_session="asian",
        spread_sensitivity="high",
        warnings=["LEÇON E2 : spread = 26% du risque — généralement non tradable"])

    reg["EURZAR"] = _forex_exotic_profile("EURZAR", "Euro / Rand Sud-Africain",
        drivers=["BCE", "SARB", "commodities_minières", "risque_politique"],
        spread_sensitivity="high",
        warnings=["volatilité extrême — gaps overnight fréquents"])

    reg["USDCLP"] = _forex_exotic_profile("USDCLP", "Dollar US / Peso Chilien",
        drivers=["Fed", "BCCh", "cuivre"],
        spread_sensitivity="high",
        warnings=["proxy du cuivre — mais liquidité TRÈS faible"])

    reg["USDCZK"] = _forex_exotic_profile("USDCZK", "Dollar US / Couronne Tchèque",
        drivers=["Fed", "CNB", "zone_euro"],
        spread_sensitivity="high")

    # ── INDICES ────────────────────────────────────────────────────────────

    reg["US500"] = _index_profile("US500", "S&P 500",
        drivers=["Fed", "earnings", "tech_weight", "risk_sentiment"],
        correlation_cluster="us_equity",
        manipulation_intensity=0.7,
        primary_session="new_york",
        fvg_fill_rate=0.82,
        ob_respect_rate=0.72,
        warnings=["indice LE PLUS suivi — la manipulation est subtile mais systématique",
                   "les gaps d'ouverture cash (15h30 UTC) sont des signaux ICT majeurs"])

    reg["USTECH"] = _index_profile("USTECH", "Nasdaq 100",
        drivers=["Fed", "tech_earnings", "TINA", "risk_sentiment"],
        correlation_cluster="us_equity",
        manipulation_intensity=0.75,
        primary_session="new_york",
        displacement_frequency="frequent",
        rr_optimal=2.0,
        warnings=["PLUS volatil que le S&P — les displacements sont plus fréquents",
                   "dominé par 7 titres (Mag7) — un earning call peut tout changer"])

    reg["NAS100.fs"] = reg["USTECH"]  # alias courtier Axi

    reg["GER40"] = _index_profile("GER40", "DAX 40",
        drivers=["BCE", "industrie_allemande", "Chine_exports", "énergie"],
        correlation_cluster="eu_equity",
        primary_session="london",
        fvg_fill_rate=0.78,
        warnings=["corrélé au S&P mais décalé — suit souvent NY avec un lag",
                   "session Francfort (7-16 UTC) = la fenêtre utile"])

    reg["UK100"] = _index_profile("UK100", "FTSE 100",
        drivers=["BoE", "GBPUSD_inverse", "commodities_weight"],
        correlation_cluster="eu_equity",
        primary_session="london",
        warnings=["INVERSEMENT corrélé à GBPUSD — livre faible = FTSE fort (exportateurs)"])

    reg["JPN225"] = _index_profile("JPN225", "Nikkei 225",
        drivers=["BoJ", "USDJPY", "carry_trade", "tech_asiatique"],
        correlation_cluster="asian_equity",
        primary_session="asian",
        secondary_session="london",
        manipulation_intensity=0.6,
        rr_optimal=2.0,
        warnings=["TRÈS corrélé au yen — affaiblissement du yen = Nikkei haussier",
                   "session asiatique DOMINANTE — Londres ne fait que réagir"])

    reg["NK225.fs"] = reg["JPN225"]  # alias courtier

    reg["EU50"] = _index_profile("EU50", "Euro Stoxx 50",
        drivers=["BCE", "zone_euro_PMI", "énergie"],
        correlation_cluster="eu_equity",
        primary_session="london")

    reg["SPA35"] = _index_profile("SPA35", "IBEX 35",
        drivers=["BCE", "tourisme_espagnol", "bancaire"],
        correlation_cluster="eu_equity",
        primary_session="london",
        spread_sensitivity="medium",
        warnings=["indice secondaire — liquidité limitée, préférer GER40 ou EU50"])

    reg["HK50"] = _index_profile("HK50", "Hang Seng 50",
        drivers=["PBoC", "tech_chinois", "flux_HK_mainland", "geopolitique"],
        correlation_cluster="asian_equity",
        primary_session="asian",
        manipulation_intensity=0.6,
        warnings=["proxy de la Chine — politiques de Pékin imprévisibles",
                   "session asiatique UNIQUEMENT — mort hors horaires"])

    # ── CRYPTO ─────────────────────────────────────────────────────────────

    reg["BTCUSD"] = _crypto_profile("BTCUSD", "Bitcoin / Dollar US",
        drivers=["halving_cycle", "etf_flows", "regulatory", "whale_activity", "macro"],
        correlation_cluster="crypto_major",
        manipulation_intensity=0.85,
        fvg_fill_rate=0.68,
        ob_respect_rate=0.58,
        rr_optimal=2.0,
        warnings=["les whales manipulent via les liquidations de leviers",
                   "les niveaux ronds (50k, 60k, 100k) sont des aimants à liquidité",
                   "Florent le trade personnellement — le bot doit suivre en rang A"])

    reg["ETHUSD"] = _crypto_profile("ETHUSD", "Ethereum / Dollar US",
        drivers=["btc_correlation", "defi_tvl", "gas_fees", "staking_yield"],
        correlation_cluster="crypto_major",
        manipulation_intensity=0.80,
        rr_optimal=2.0,
        warnings=["suit BTC à ~85% de corrélation — trade rarement indépendamment",
                   "les mises à jour protocolaires créent des événements binaires"])

    reg["ETH-JPY"] = _crypto_profile("ETH-JPY", "Ethereum / Yen Japonais",
        drivers=["ethusd", "usdjpy", "yen_carry"],
        spread_sensitivity="high",
        warnings=["LEÇON E2 : spread = 21% du risque — GÉNÉRALEMENT NON TRADABLE",
                   "cross synthétique : double le risque de slippage"])

    reg["BTC-JPY"] = _crypto_profile("BTC-JPY", "Bitcoin / Yen Japonais",
        drivers=["btcusd", "usdjpy", "yen_carry"],
        spread_sensitivity="high",
        warnings=["cross synthétique exotique — vérifier le spread AVANT d'entrer"])

    reg["XLMUSD"] = _crypto_profile("XLMUSD", "Stellar / Dollar US",
        drivers=["btc_correlation", "partnerships", "remittance_flows"],
        manipulation_intensity=0.9,
        ob_respect_rate=0.45,
        warnings=["altcoin : manipulation intense, OB moins fiables",
                   "suit BTC mais avec un beta élevé — les mouvements sont amplifiés"])

    # ── COMMODITIES ────────────────────────────────────────────────────────

    reg["XAUUSD"] = _commodity_profile("XAUUSD", "Or / Dollar US",
        asset_class="precious_metal",
        drivers=["Fed", "taux_réels", "risk_sentiment", "dollar_index", "géopolitique"],
        correlation_cluster="safe_haven",
        dominant_participants=["banque_centrale", "souverain", "hedge_fund", "retail"],
        manipulation_intensity=0.6,
        fvg_fill_rate=0.73,
        ob_respect_rate=0.65,
        primary_session="london",
        secondary_session="new_york",
        rr_optimal=2.0,
        warnings=["valeur refuge ET actif spéculatif — double personnalité",
                   "les banques centrales (Chine, Inde) accumulent discrètement",
                   "session Londres = le fix de l'or, mouvement clé du matin"])

    reg["XAGUSD"] = _commodity_profile("XAGUSD", "Argent / Dollar US",
        asset_class="precious_metal",
        drivers=["or", "industrie", "dollar", "risk_sentiment"],
        correlation_cluster="safe_haven",
        manipulation_intensity=0.7,
        spread_sensitivity="medium",
        rr_optimal=2.5,
        warnings=["PLUS volatil que l'or — les mèches sont profondes",
                   "composante industrielle : sensible au cycle manufacturier"])

    reg["USOIL"] = _commodity_profile("USOIL", "WTI Crude Oil",
        drivers=["OPEC", "stocks_EIA", "géopolitique", "dollar", "demande_mondiale"],
        correlation_cluster="energy",
        dominant_participants=["corporate", "hedge_fund", "souverain"],
        manipulation_intensity=0.6,
        primary_session="new_york",
        secondary_session="london",
        rr_optimal=2.0,
        warnings=["rapport EIA le mercredi = volatilité garantie",
                   "OPEC+ peut changer les règles du jour au lendemain"])

    reg["WTI.fs"] = reg["USOIL"]  # alias

    reg["UKOIL"] = _commodity_profile("UKOIL", "Brent Crude Oil",
        drivers=["OPEC", "géopolitique_ME", "demande_europe_asie"],
        correlation_cluster="energy",
        primary_session="london",
        rr_optimal=2.0)

    reg["BRENT.fs"] = reg["UKOIL"]  # alias

    reg["COFFEE.fs"] = _commodity_profile("COFFEE.fs", "Coffee Futures",
        drivers=["météo_Brésil", "récolte", "stocks_ICE", "real_brésilien"],
        manipulation_intensity=0.3,
        displacement_frequency="rare",
        atr_stability=0.4,
        spread_sensitivity="medium",
        warnings=["saisonnier : la météo au Brésil est le driver principal",
                   "liquidité concentrée sur la session NY"])

    return reg


_REGISTRY: dict[str, AssetProfile] | None = None


def _registry() -> dict[str, AssetProfile]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def get_profile(symbol: str) -> AssetProfile | None:
    """Profil connu d'un actif, ou None si inconnu."""
    reg = _registry()
    # Essayer le symbole tel quel, puis sans suffixe courtier
    for candidate in [symbol, symbol.replace(".fs", "").replace(".s", ""),
                      symbol.replace("-", "")]:
        if candidate in reg:
            return reg[candidate]
    return None


def classify_asset(symbol: str) -> str:
    """Classification fine d'un actif. Repli sur heuristique si inconnu."""
    profile = get_profile(symbol)
    if profile:
        return profile.asset_class

    s = symbol.upper().replace(".FS", "").replace(".S", "").replace("-", "")

    # Crypto
    if any(c in s for c in ["BTC", "ETH", "XRP", "SOL", "ADA", "DOT", "XLM",
                             "DOGE", "LINK", "AVAX", "MATIC", "UNI"]):
        return "crypto"

    # Indices
    if any(idx in s for idx in ["US500", "US30", "US100", "NAS100", "USTECH",
                                 "GER40", "UK100", "JPN225", "NK225", "EU50",
                                 "SPA35", "HK50", "AUS200", "FRA40"]):
        return "index"

    # Precious metals
    if any(m in s for m in ["XAU", "XAG", "XPT", "XPD"]):
        return "precious_metal"

    # Energy
    if any(e in s for e in ["OIL", "WTI", "BRENT", "NATGAS", "NGAS"]):
        return "energy"

    # Soft commodities
    if any(c in s for c in ["COFFEE", "COCOA", "SUGAR", "COTTON", "WHEAT",
                             "CORN", "SOYBEAN"]):
        return "soft_commodity"

    # Forex — longueur 6 = deux devises de 3 lettres
    if len(s) == 6 and s.isalpha():
        majors = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"}
        base, quote = s[:3], s[3:]
        if base in majors and quote in majors:
            return "forex_major"
        if base in majors or quote in majors:
            return "forex_cross"
        return "forex_exotic"

    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE CONTEXTUELLE — adapter le bot à l'actif
# ═══════════════════════════════════════════════════════════════════════════════

def manipulation_context(symbol: str, now: datetime | None = None) -> dict:
    """Contexte de manipulation pour un actif à un instant donné.

    Ce que le bot doit savoir AVANT d'évaluer les piliers :
    - Sommes-nous dans une kill zone ?
    - Quel type de manipulation attendre ?
    - Les FVG/OB sont-ils fiables pour cet actif ?
    """
    profile = get_profile(symbol)
    session = current_session(now)
    kzs = active_kill_zones(now)

    if profile is None:
        return {
            "known": False,
            "session": session.value,
            "kill_zones": [kz.name for kz in kzs],
            "warnings": ["actif inconnu — profil par défaut, prudence accrue"],
        }

    # L'actif est-il dans sa session optimale ?
    in_primary = session.value == profile.primary_session
    in_secondary = session.value == profile.secondary_session
    in_avoid = session.value == profile.avoid_session

    # Probabilité de manipulation en cours
    manip_prob = profile.manipulation_intensity
    if kzs:
        manip_prob = max(manip_prob, max(kz.manipulation_prob for kz in kzs))
    if in_avoid:
        manip_prob *= 0.5  # manipulation moins probable hors session

    return {
        "known": True,
        "asset_class": profile.asset_class,
        "session": session.value,
        "in_primary_session": in_primary,
        "in_secondary_session": in_secondary,
        "in_avoid_session": in_avoid,
        "kill_zones": [kz.name for kz in kzs],
        "manipulation_probability": round(manip_prob, 2),
        "judas_swing_expected": profile.judas_swing_frequency == "frequent" and bool(kzs),
        "fvg_reliability": profile.fvg_fill_rate,
        "ob_reliability": profile.ob_respect_rate,
        "dominant_participants": profile.dominant_participants,
        "drivers": profile.drivers,
        "warnings": profile.warnings,
        "recommendations": {
            "trade_now": in_primary or in_secondary,
            "wait_for_session": profile.primary_session if in_avoid else None,
            "rr_optimal": profile.rr_optimal,
            "tf_entry": profile.timeframe_entry,
            "tf_structure": profile.timeframe_structure,
        },
    }


def optimal_assets_for_session(now: datetime | None = None) -> list[dict]:
    """Quels actifs sont dans leur session optimale en ce moment ?

    Retourne les actifs classés par pertinence pour la session en cours.
    """
    session = current_session(now)
    reg = _registry()

    results = []
    seen = set()  # éviter les doublons (alias)

    for sym, profile in reg.items():
        if profile.display_name in seen:
            continue
        seen.add(profile.display_name)

        in_primary = session.value == profile.primary_session
        in_secondary = session.value == profile.secondary_session
        if session == SessionType.OVERLAP:
            in_primary = profile.primary_session in ("london", "new_york")
            in_secondary = not in_primary

        if in_primary or in_secondary:
            score = 1.0 if in_primary else 0.6
            score *= (1.0 - profile.spread_sensitivity_score)
            score *= profile.ob_respect_rate

            results.append({
                "symbol": sym,
                "name": profile.display_name,
                "class": profile.asset_class,
                "session_fit": "primary" if in_primary else "secondary",
                "score": round(score, 3),
                "rr": profile.rr_optimal,
                "manipulation_risk": profile.manipulation_intensity,
                "warnings": profile.warnings[:1],
            })

    return sorted(results, key=lambda x: -x["score"])
