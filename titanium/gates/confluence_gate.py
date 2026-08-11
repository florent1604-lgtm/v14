"""Portes ET de confluence — le véto déterministe de Titanium.

Porté de V12 (`fusion/confluence_gate.py`, GATE_VERSION 1.2.0) le 06/08/2026.
La logique de décision est **inchangée** : c'est la méthode de Florent, elle a
été éprouvée en démo puis en réel, on ne la relitige pas ici.

Ce qui change par rapport à V12 :
  · le quorum micro-structure devient un **paramètre** au lieu d'un `if` en dur
    (V12 : 3 en prod / 2 en explore, codé dans la fonction) ;
  · plus aucun import du reste de la base — la fonction est pure, elle ne
    connaît que son dict de features ;
  · les seuils émotionnels remontent en constantes nommées.

RÔLE DANS V14 — c'est le point clé de l'architecture hybride : cette porte
s'exécute **avant** toute délibération LLM. Un setup qui ne passe pas les portes
n'atteint jamais les agents. La délibération ne sert qu'à moduler la taille et
le plan d'entrée d'un setup **déjà validé** par la mécanique déterministe.

Piliers (portes ET — un pilier fort ne compense JAMAIS un pilier absent) :
  G0 data_valid        — bougies clôturées + données finies (fail-closed).
  G1 trend_sr          — tendance HTF définie ET prix sur un gros niveau S/R.
  G2 fair_value        — prix sur une zone de juste prix (VPOC/HVN).
  G3 liquidity         — sweep de liquidité / FVG dans le sens.
  G4 ote_ob            — dans la golden zone OTE (non invalidée) / OB valide.
  G5 candle_confirmed  — bougie de confirmation dans le sens.

Modérateurs (PAS des piliers) :
  emotion → WAIT (timing pas mûr) ou BLOCK (ce côté est interdit).
  cost    → BLOCK (frais week-end ; edge négatif ; edge non prouvé en PROD).

Verdict : ENTER / WAIT / BLOCK. Le `rank` sert UNIQUEMENT à classer entre
plusieurs ENTER — jamais à décider. FAIL-CLOSED : toute porte non évaluable
⇒ BLOCK.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

GATE_VERSION = "2.0.0"  # incrémenter à CHAQUE changement de logique de décision

# Quorum sur les 4 piliers de micro-structure (fair_value, liquidity, ote_ob,
# candle_confirmed). G1 reste obligatoire quoi qu'il arrive.
QUORUM_PROD = 3     # réel : on exige 3 des 4
QUORUM_EXPLORE = 2  # démo/labo : 2 suffisent, pour générer assez de trades à mesurer

# Seuil sous lequel la confiance émotionnelle fait attendre plutôt qu'entrer.
EMOTION_MIN_CONFIDENCE = 0.35

_SUPPORT_PILLARS = frozenset({"fair_value", "liquidity", "ote_ob", "candle_confirmed"})
_EMOTION_KEYS = frozenset({"filter_block", "stale", "confidence", "wait"})
_COST_KEYS = frozenset({"edge_ok", "weekend_block"})


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reason: str
    code: str = ""  # code machine stable (ex: 'G1_TREND_SR', 'G1_TREND_SR_MISSING')


@dataclass(frozen=True)
class Decision:
    verdict: str                      # 'ENTER' | 'WAIT' | 'BLOCK'
    side: int                         # +1 long / −1 short / 0
    gates: list[GateResult] = field(default_factory=list)
    rank: float = 0.0                 # score de CLASSEMENT (non décisionnel)
    reasons: list[str] = field(default_factory=list)
    code: str = ""                    # reason-code stable du verdict
    mode: str = "prod"                # 'prod' (edge exigé) | 'explore' (démo/labo)
    version: str = GATE_VERSION
    decided_at: str | None = None     # horodatage ISO
    decision_id: str = ""             # empreinte stable (traçabilité)
    setup_family: str = ""            # 'continuation' | 'reversal'
    # ── Comptage EXACT des piliers de micro-structure ALIGNÉS.
    #    Exposé ici parce que c'est la porte, et elle seule, qui sait ce
    #    qu'elle a jugé aligné. Le déduire ailleurs depuis `strengths` mesure
    #    la non-nullité (`liquidity != 0`) au lieu de l'alignement
    #    (`liquidity == side`) et sur-compte : un setup à 2 piliers était
    #    journalisé « 4p ». Défaut relevé le 07/08/2026.
    support_passed: int = 0           # S ∈ 0..4
    quorum: int = 0                   # quorum en vigueur pour CETTE décision

    @property
    def entered(self) -> bool:
        return self.verdict == "ENTER"


def _aligned(value: int | None, side: int) -> bool:
    """`value` doit valoir exactement `side` (0/None = pilier absent = échec)."""
    return value is not None and value == side and side != 0


def _decision_id(decided_at: str, side: int, family: str, code: str, mode: str,
                 gates: list[GateResult], rank: float) -> str:
    """Empreinte reproductible d'une décision (clic dashboard → preuves)."""
    payload = "|".join([
        decided_at, str(side), family, code, mode, f"{rank:.4f}",
        ",".join(f"{g.name}:{int(g.passed)}" for g in gates),
    ])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _resolve_timestamp(feats: dict, decided_at: datetime | None) -> str:
    """Horodatage de la décision : explicite > trace des features > maintenant."""
    if decided_at is None:
        trace_at = (feats.get("_trace") or {}).get("decided_at")
        if trace_at:
            try:
                decided_at = datetime.fromisoformat(str(trace_at).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                decided_at = None
    at = decided_at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.isoformat()


def evaluate(feats: dict, *, side: int | None = None, require_edge: bool = False,
             decided_at: datetime | None = None,
             quorum: int | None = None) -> Decision:
    """Évalue la confluence sur des features DÉJÀ calculées.

    La fonction est pure : elle ne lit aucune donnée de marché, n'appelle aucun
    détecteur. C'est ce qui la rend testable sans terminal MT5.

    Args:
        feats: sorties des détecteurs. Champs attendus —
            ``data_valid: bool`` ; ``trend: int`` ; ``setup_side: int|None`` ;
            ``setup_family: 'continuation'|'reversal'|None`` ;
            ``on_sr_level: bool`` ; ``fair_value: bool`` ; ``liquidity: int`` ;
            ``ote: int`` ; ``candle: int`` ; ``strengths: {gate: 0..1}`` ;
            ``emotion: {filter_block, stale, confidence, wait}`` ;
            ``cost: {edge_ok, weekend_block}``.
        side: sens imposé. Doit correspondre à ``setup_side`` s'il est fourni.
        require_edge: True = PROD (edge prouvé requis, quorum 3/4) ;
            False = EXPLORE (edge observé non bloquant, quorum 2/4).
        decided_at: horodatage à figer (tests, rejeu).
        quorum: force le quorum micro-structure. None = selon ``require_edge``.

    Returns:
        Decision — verdict, sens, trace de chaque porte, reason-code stable.
    """
    mode = "prod" if require_edge else "explore"
    at_iso = _resolve_timestamp(feats, decided_at)
    if quorum is None:
        quorum = QUORUM_PROD if require_edge else QUORUM_EXPLORE

    family = ""

    def _decide(verdict, side_, gates_, code_, reasons_, rank_=0.0) -> Decision:
        support = sum(1 for g in gates_
                      if g.name in _SUPPORT_PILLARS and g.passed)
        return Decision(
            verdict, side_, gates_, rank=rank_, reasons=reasons_, code=code_,
            mode=mode, decided_at=at_iso, setup_family=family,
            decision_id=_decision_id(at_iso, side_, family, code_, mode, gates_, rank_),
            support_passed=support, quorum=quorum,
        )

    # ── G0 — données valides : préalable absolu (fail-closed).
    if not feats.get("data_valid", False):
        g = [GateResult("data_valid", False, "données non clôturées/valides", "G0_DATA_INVALID")]
        return _decide("BLOCK", 0, g, "BLOCK_DATA_INVALID", ["G0 données invalides → BLOCK"])

    try:
        trend = int(feats.get("trend", 0) or 0)
        setup_side = feats.get("setup_side")
        setup_side = int(setup_side) if setup_side is not None else None
    except (TypeError, ValueError):
        g = [GateResult("setup", False, "contrat setup non numérique", "G1_SETUP_INVALID")]
        return _decide("BLOCK", 0, g, "BLOCK_SETUP_INVALID", ["Setup invalide → BLOCK"])

    family_raw = feats.get("setup_family")
    if setup_side in (None, 0) and family_raw in (None, ""):
        g = [GateResult("setup", False, "aucun setup déclaré", "G1_NO_SETUP")]
        return _decide("WAIT", 0, g, "WAIT_NO_SETUP",
                       ["Aucun setup continuation/reversal à évaluer → WAIT"])

    family = str(family_raw or "").lower()
    try:
        explicit_side = int(side) if side is not None else setup_side
    except (TypeError, ValueError):
        explicit_side = None
    if (setup_side not in (-1, 1) or explicit_side not in (-1, 1)
            or explicit_side != setup_side or family not in ("continuation", "reversal")):
        g = [GateResult("setup", False, "side/famille de setup incohérents", "G1_SETUP_INVALID")]
        return _decide("BLOCK", 0, g, "BLOCK_SETUP_INVALID",
                       ["Setup incomplet ou incohérent → BLOCK"])
    side = setup_side

    gates: list[GateResult] = [GateResult("data_valid", True, "OK", "G0_OK")]

    # ── G1 structure. Deux familles de setup (Florent : « ne pas trop restreindre ») :
    #  · CONTINUATION : exige tendance alignée ET sur niveau S/R.
    #  · REVERSAL : exige sur niveau S/R ; la tendance n'est qu'un CONTEXTE (elle ne
    #    bloque pas), la confirmation venant des autres piliers.
    is_reversal = family == "reversal"
    structure_ok = bool(feats.get("on_sr_level")) and (True if is_reversal else _aligned(trend, side))
    structure_desc = ("setup reversal sur niveau S/R (tendance = contexte)" if is_reversal
                      else "tendance HTF + sur niveau S/R")

    checks = [
        ("trend_sr", structure_ok, structure_desc, "G1_TREND_SR"),
        ("fair_value", bool(feats.get("fair_value")),
         "sur zone de juste prix (VPOC/HVN)", "G2_FAIR_VALUE"),
        ("liquidity", _aligned(feats.get("liquidity"), side),
         "sweep/FVG dans le sens", "G3_LIQUIDITY"),
        ("ote_ob", _aligned(feats.get("ote"), side),
         "golden zone OTE valide / OB non cassé", "G4_OTE_OB"),
        ("candle_confirmed", _aligned(feats.get("candle"), side),
         "bougie de confirmation dans le sens", "G5_CANDLE"),
    ]
    for name, ok, desc, base_code in checks:
        gates.append(GateResult(name, bool(ok), desc if ok else f"MANQUE : {desc}",
                                base_code if ok else f"{base_code}_MISSING"))

    failed = [g.name for g in gates if not g.passed]
    trend_sr_ok = any(g.name == "trend_sr" and g.passed for g in gates)
    support_passed = sum(1 for g in gates if g.name in _SUPPORT_PILLARS and g.passed)

    if not trend_sr_ok or support_passed < quorum:
        return _decide("BLOCK", side, gates, "BLOCK_PILLAR_MISSING",
                       [f"Confluence incomplète ({support_passed}/{len(_SUPPORT_PILLARS)} "
                        f"piliers, quorum {quorum}), manque : {', '.join(failed)} → BLOCK"])

    # ── Modérateurs (les piliers sont alignés).
    emo = feats.get("emotion")
    cost = feats.get("cost")
    if not isinstance(emo, dict) or not _EMOTION_KEYS.issubset(emo):
        return _decide("BLOCK", side, gates, "BLOCK_EMOTION_UNAVAILABLE",
                       ["Émotion indisponible/incomplète → BLOCK"])
    if not isinstance(cost, dict) or not _COST_KEYS.issubset(cost):
        return _decide("BLOCK", side, gates, "BLOCK_COST_UNAVAILABLE",
                       ["Coût indisponible/incomplet → BLOCK"])
    if _aligned(emo.get("filter_block"), side):
        return _decide("BLOCK", side, gates, "BLOCK_EMOTION_SIDE",
                       ["Émotion BLOQUE ce côté (ex : ne pas acheter l'euphorie) → BLOCK"])
    if cost.get("weekend_block"):
        return _decide("BLOCK", side, gates, "BLOCK_COST_WEEKEND",
                       ["Coût : frais du vendredi soir / hold week-end → BLOCK"])

    # Coût/edge — FAIL-CLOSED en PROD : jamais d'entrée réelle sans edge prouvé.
    edge_ok = cost.get("edge_ok")
    if edge_ok is False:
        return _decide("BLOCK", side, gates, "BLOCK_EDGE_NEGATIVE",
                       ["Coût : edge attendu < coût accepté → BLOCK"])
    if require_edge and edge_ok is not True:
        return _decide("BLOCK", side, gates, "BLOCK_EDGE_UNPROVEN",
                       ["PROD : edge non prouvé (labo requis) → BLOCK. "
                        "Utiliser le mode EXPLORE pour tester et mesurer."])

    if (emo.get("stale") or emo.get("wait")
            or emo.get("confidence", 1.0) < EMOTION_MIN_CONFIDENCE):
        # Entrer quand l'émotion n'est pas prête produit des trades impulsifs.
        return _decide("WAIT", side, gates, "WAIT_EMOTION_TIMING",
                       ["Émotion : timing pas mûr (attendre, ne pas entrer impulsif) → WAIT"])

    # ── Tous piliers alignés + modérateurs OK → ENTER.
    st = feats.get("strengths") or {}
    rank = round(sum(float(st.get(g.name, 0.0)) for g in gates if g.passed), 3)
    tag = "LONG" if side > 0 else "SHORT"
    note = "" if mode == "prod" else " [EXPLORE : pris pour mesurer]"
    return _decide("ENTER", side, gates, "ENTER_CONFLUENCE",
                   [f"Confluence complète {tag} → ENTER (rank {rank}){note}"], rank_=rank)
