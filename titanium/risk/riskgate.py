"""RiskGate — la porte unique de risque. Fail-CLOSED.

Porté de V12 (`risk/riskgate.py`) le 06/08/2026, avec trois corrections de fond.

CE QUI CHANGE PAR RAPPORT À V12
-------------------------------
1. **Fail-CLOSED.** V12 est fail-OPEN par conception : l'appelant enveloppe
   `evaluate()` dans un `try/except` qui avale tout, « un bug du RiskGate ne
   bloque jamais un trade ». Une porte de risque qui laisse passer quand elle
   casse ne protège rien. Ici, toute erreur ou entrée invalide ⇒ **DENY**.

2. **Un seul calcul de taille.** Dans V12, `pillar_size` est calculé ligne 154
   depuis le barème par piliers, puis **écrasé** ligne 173 par la confiance de
   Cloe. Le barème et sa fonction `pillar_size()` sont du code mort. Ici il n'y
   a qu'un modulateur : la confiance mesurée du contexte.

3. **Découplé.** V12 dépend de `core.state.SystemState` et `core.config`. Ici
   l'entrée est un dataclass explicite : la porte se teste sans démarrer le bot.

ORDRE DES CONTRÔLES — entonnoir strict, tous les vétos avant tout calcul :
    HALT → fondamentaux → circuit breaker → validité (sens + données)
         → anti-fade tendance → plafond d'exposition → dimensionnement

Le dimensionnement n'est atteint que si aucun véto n'a parlé. C'est ce qui rend
la trace lisible : un DENY porte toujours le nom du contrôle qui l'a produit.

RAPPEL (leçon coûteuse de V12) : le coût n'entre PAS dans la formule du lot. Le
lot est proportionnel au RISQUE, modulé par la confiance mesurée du contexte.
Le coût agit indirectement — un contexte où le coût tue l'edge mesure une
performance négative, donc une confiance basse, donc un petit lot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

ALLOW = "ALLOW"
REDUCE = "REDUCE"
DENY = "DENY"

# Confiance neutre quand aucune mesure n'est disponible pour ce contexte.
NEUTRAL_CONFIDENCE = 0.5
# Facteur appliqué quand l'émotion voudrait bloquer : on réduit, on n'interdit pas.
EMOTION_FLOOR = 0.5
# Facteur appliqué en zone fondamentale intermédiaire.
FUNDAMENTALS_REDUCE_FACTOR = 0.5


def _finite(x) -> bool:
    """True si x est un nombre réel exploitable (ni None, ni NaN, ni inf)."""
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class RiskInput:
    """Tout ce dont la porte a besoin. Explicite : aucun accès à un état global.

    Un champ manquant n'est jamais interprété comme zéro — les booléens de
    danger sont à True par défaut, pour que l'oubli d'un champ bloque au lieu
    de laisser passer.
    """
    side: int                          # +1 long / −1 short
    price: float
    atr: float
    equity: float
    risk_pct: float                    # % d'equity risqué par trade

    # Vétos (défauts dangereux = fail-closed)
    halted: bool = True
    circuit_breaker: bool = True
    fundamentals_block: bool = True
    fundamentals_reduce: bool = False

    # Contexte
    trend: int = 0                     # 0 = pas de tendance nette → pas d'anti-fade
    gross_exposure_pct: float = 0.0
    emotion_available: bool = False
    emotion_would_block: bool = False
    confidence: float = NEUTRAL_CONFIDENCE   # [0,1], perf mesurée du contexte
    timeframe: str = ""
    roundtrip_cost: float | None = None      # informatif, hors formule


@dataclass
class Decision:
    """Décision de la porte. Journalisable telle quelle."""
    verdict: str = DENY
    reason: str = ""
    side: int = 0
    allowed_risk_money: float = 0.0
    size_factor: float = 0.0
    confidence: float = 0.0
    sl: float | None = None
    tp: float | None = None
    stop_distance: float | None = None
    checks: list[dict] = field(default_factory=list)

    def _add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"gate": name, "passed": bool(passed), "detail": detail})

    def _deny(self, name: str, reason: str, detail: str = "") -> "Decision":
        self._add(name, False, detail)
        self.verdict = DENY
        self.reason = reason
        return self

    @property
    def allowed(self) -> bool:
        return self.verdict in (ALLOW, REDUCE)


class RiskGate:
    """Porte unique. Pure : aucune I/O, aucun ordre.

    C'est l'appelant (couche exécution) qui agit sur un ALLOW/REDUCE. Un DENY
    n'est jamais contournable en aval.
    """

    def __init__(self, *, sl_atr_k: float = 1.5, rr_ratio: float = 2.0,
                 max_exposure_pct: float = 100.0) -> None:
        self.sl_atr_k = sl_atr_k
        self.rr_ratio = rr_ratio
        self.max_exposure_pct = max_exposure_pct

    def evaluate(self, inp: RiskInput) -> Decision:
        """Consolide tous les contrôles et rend une Decision.

        Ne lève jamais : toute anomalie devient un DENY tracé. C'est la
        différence de fond avec V12.
        """
        try:
            return self._evaluate(inp)
        except Exception as exc:  # noqa: BLE001 — fail-closed délibéré
            d = Decision()
            return d._deny("internal", "ERREUR_INTERNE", f"{type(exc).__name__}: {exc}")

    def _evaluate(self, inp: RiskInput) -> Decision:
        d = Decision()

        # ── 0. HALT souverain : prime sur tout.
        if inp.halted:
            return d._deny("halt", "HALT_ACTIF", "arrêt manuel actif")
        d._add("halt", True)

        # ── 1. Véto fondamentaux (les faits macro).
        reduce_factor = 1.0
        if inp.fundamentals_block:
            return d._deny("fundamentals", "FONDAMENTAUX_BLOCK", "risque macro bloquant")
        if inp.fundamentals_reduce:
            reduce_factor *= FUNDAMENTALS_REDUCE_FACTOR
            d._add("fundamentals", True, "réduction (zone intermédiaire)")
        else:
            d._add("fundamentals", True)

        # ── 2. Circuit breaker (winrate / drawdown).
        if inp.circuit_breaker:
            return d._deny("circuit_breaker", "CIRCUIT_BREAKER", "seuil perf franchi")
        d._add("circuit_breaker", True)

        # ── 3. Validité : sens défini et données exploitables.
        #      La conversion est défensive : un `side` non numérique doit produire
        #      PAS_DE_SENS, pas ERREUR_INTERNE — le motif du refus est un dataset,
        #      il doit nommer la cause réelle.
        try:
            side = int(inp.side or 0)
        except (TypeError, ValueError):
            side = 0
        d.side = side
        if side not in (-1, 1):
            return d._deny("side", "PAS_DE_SENS", f"side={inp.side!r}")
        d._add("side", True)

        if not _finite(inp.price) or float(inp.price) <= 0:
            return d._deny("market_data", "DONNEES_INSUFFISANTES", f"price={inp.price!r}")
        if not _finite(inp.atr) or float(inp.atr) <= 0:
            return d._deny("market_data", "DONNEES_INSUFFISANTES", f"atr={inp.atr!r}")
        if not _finite(inp.equity) or float(inp.equity) <= 0:
            return d._deny("market_data", "DONNEES_INSUFFISANTES", f"equity={inp.equity!r}")
        if not _finite(inp.risk_pct) or float(inp.risk_pct) <= 0:
            return d._deny("market_data", "DONNEES_INSUFFISANTES", f"risk_pct={inp.risk_pct!r}")
        price, atr = float(inp.price), float(inp.atr)
        d._add("market_data", True, f"tf={inp.timeframe or '?'} atr={atr:.6g}")

        # ── 4. Anti-fade : ne jamais fader une tendance nette (correctif V12 du 26/07).
        trend = int(inp.trend or 0)
        if trend != 0 and side == -trend:
            return d._deny("trend_align", "CONTRE_TENDANCE", f"side={side} vs trend={trend}")
        d._add("trend_align", True)

        # ── 5. Plafond d'exposition — véto AVANT tout calcul de taille (V12 le
        #      plaçait après, ce qui produisait un dimensionnement jeté).
        gross = float(inp.gross_exposure_pct) if _finite(inp.gross_exposure_pct) else None
        if gross is None:
            return d._deny("exposure_cap", "EXPOSITION_INCONNUE",
                           f"gross={inp.gross_exposure_pct!r}")
        if gross >= self.max_exposure_pct:
            return d._deny("exposure_cap", "EXPOSITION_MAX",
                           f"gross={gross}% >= {self.max_exposure_pct}%")
        d._add("exposure_cap", True, f"gross={gross}%")

        # ── 6. Dimensionnement. Aucun véto n'a parlé : on calcule.
        #      Un seul modulateur de conviction — la confiance mesurée du contexte.
        confidence = float(inp.confidence) if _finite(inp.confidence) else NEUTRAL_CONFIDENCE
        confidence = min(1.0, max(0.0, confidence))

        emotion_mult = (EMOTION_FLOOR
                        if (inp.emotion_available and inp.emotion_would_block)
                        else 1.0)

        size_factor = reduce_factor * emotion_mult * confidence
        allowed_risk = max(0.0, float(inp.equity) * (float(inp.risk_pct) / 100.0) * size_factor)

        stop = self.sl_atr_k * atr
        sign = 1 if side > 0 else -1

        d.confidence = round(confidence, 4)
        d.size_factor = round(size_factor, 4)
        d.stop_distance = round(stop, 8)
        d.sl = round(price - sign * stop, 8)
        d.tp = round(price + sign * stop * self.rr_ratio, 8)
        d.allowed_risk_money = round(allowed_risk, 2)
        d._add("sizing", True,
               f"stop={stop:.6g} risk={allowed_risk:.2f} sf={size_factor:.3f} conf={confidence:.3f}")

        # Le coût est tracé mais n'entre PAS dans la formule (règle de Florent).
        d._add("cost_info", True, f"coût={inp.roundtrip_cost} (accepté, hors formule lot)")

        # Un lot nul n'est pas un ALLOW : c'est un refus déguisé, on le nomme.
        if allowed_risk <= 0:
            return d._deny("sizing_floor", "TAILLE_NULLE", f"sf={size_factor:.4f}")

        d.verdict = REDUCE if size_factor < 1.0 else ALLOW
        d.reason = "OK" if d.verdict == ALLOW else "réduction appliquée"
        return d


def evaluate(inp: RiskInput, **kwargs) -> Decision:
    """Raccourci fonctionnel (porte par défaut)."""
    return RiskGate(**kwargs).evaluate(inp)
