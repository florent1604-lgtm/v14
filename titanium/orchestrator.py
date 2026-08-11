"""L'orchestrateur — la chaîne complète, et les règles qui la gouvernent.

    portes ET déterministes  →  délibération LLM  →  RiskGate  →  exécution
         (décide SI)            (décide COMBIEN)    (dernier mot)   (agit)

C'est ici que la thèse de V14 devient du code : **V12 exécute, V13 délibère.**

LES TROIS RÈGLES, ET COMMENT ELLES SONT TENUES
-----------------------------------------------
1. **Le LLM n'a jamais l'autorité d'exécution.** Sa seule sortie est une
   conviction ∈ [0,1] qui module la TAILLE. Il ne peut ni créer une entrée que
   les portes ont refusée, ni inverser un sens, ni lever un véto. Une conviction
   de 1.0 sur un setup BLOCK ne produit rien.

2. **Aucun appel LLM dans le chemin critique.** La délibération n'est atteinte
   que si les portes ont déjà rendu ENTER. Sur 149 actifs balayés, cela ramène
   le coût à quelques décisions par jour au lieu de quelques milliers. C'est la
   réponse à la question de coût laissée ouverte par le diagnostic.

3. **Fail-closed de bout en bout.** Délibération indisponible, lente ou
   incohérente ⇒ on retombe sur la conviction neutre et on continue en
   déterministe. Jamais un blocage, jamais un pari.

CE QUE L'ORCHESTRATEUR NE FAIT PAS
-----------------------------------
Il ne lit aucune donnée de marché et ne calcule aucun indicateur : il reçoit des
features déjà calculées. Il reste donc **pur en dehors de l'étage d'exécution**,
et se teste intégralement sans terminal MT5 ni appel LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from titanium.gates import confluence_gate
from titanium.risk.riskgate import ALLOW, DENY, REDUCE, RiskGate, RiskInput
from titanium.execution.mt5_executor import ExecutionPolicy, OrderResult, place_market_order

# Étapes de la chaîne, dans l'ordre. Sert aux motifs d'arrêt.
STEP_GATES = "gates"
STEP_DELIBERATION = "deliberation"
STEP_RISK = "risk"
STEP_EXECUTION = "execution"

#: Conviction retenue quand la délibération est absente, lente ou illisible.
NEUTRAL_CONVICTION = 0.5


@dataclass
class Outcome:
    """Résultat complet d'un passage. Journalisable tel quel.

    ``stopped_at`` nomme l'étage qui a arrêté la chaîne — c'est la question
    qu'on se pose toujours en premier devant un trade qui n'est pas parti.
    """
    symbol: str = ""
    side: int = 0
    stopped_at: str = ""
    reason: str = ""
    gate_verdict: str = ""
    gate_code: str = ""
    conviction: float = 0.0
    conviction_source: str = ""
    risk_verdict: str = ""
    risk_money: float = 0.0
    stop_distance: float | None = None
    order: OrderResult | None = None
    trace: list[str] = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return bool(self.order and self.order.sent)

    def _step(self, texte: str) -> None:
        self.trace.append(texte)


@dataclass(frozen=True)
class OrchestratorConfig:
    """Réglages de la chaîne. Défauts = défauts sûrs."""
    require_edge: bool = False          # True = mode PROD (quorum 3/4, edge exigé)
    deliberate: bool = True             # False = 100 % déterministe, zéro coût LLM
    execute: bool = False               # False = on décide, on n'envoie rien
    rr_ratio: float = 2.0


def run_once(
    symbol: str,
    features: dict,
    risk_context: dict,
    *,
    config: OrchestratorConfig | None = None,
    policy: ExecutionPolicy | None = None,
    deliberate_fn: Callable[[str, int, dict], float] | None = None,
    risk_gate: RiskGate | None = None,
    idempotency_key: str = "",
) -> Outcome:
    """Fait passer un setup dans toute la chaîne. Ne lève jamais.

    Args:
        symbol: instrument.
        features: sorties des détecteurs, contrat de `confluence_gate.evaluate`.
        risk_context: champs de `RiskInput` hors ``side``/``confidence``, qui
            sont fournis par les étages précédents.
        config: réglages de la chaîne.
        policy: mur d'exécution. Requis seulement si ``config.execute``.
        deliberate_fn: ``(symbol, side, features) -> conviction ∈ [0,1]``.
            Reçoit un setup **déjà validé**. Toute erreur ⇒ conviction neutre.
        risk_gate: porte de risque (injectable pour les tests).
        idempotency_key: transmise à l'exécuteur.

    Returns:
        Outcome — toujours, même en cas d'arrêt précoce ou d'erreur interne.
    """
    cfg = config or OrchestratorConfig()
    out = Outcome(symbol=symbol)

    # ══ 1. PORTES ET — le véto déterministe. Aucun LLM n'est appelé avant.
    try:
        decision = confluence_gate.evaluate(features, require_edge=cfg.require_edge)
    except Exception as exc:  # noqa: BLE001 — fail-closed
        out.stopped_at = STEP_GATES
        out.reason = f"GATES_ERREUR: {type(exc).__name__}"
        return out

    out.gate_verdict, out.gate_code, out.side = decision.verdict, decision.code, decision.side
    out._step(f"portes : {decision.verdict} ({decision.code})")

    if not decision.entered:
        out.stopped_at = STEP_GATES
        out.reason = decision.code
        return out

    # ══ 2. DÉLIBÉRATION — n'est atteinte QUE sur un ENTER. C'est la porte de coût.
    conviction, source = NEUTRAL_CONVICTION, "neutre (délibération désactivée)"
    if cfg.deliberate and deliberate_fn is not None:
        try:
            brut = deliberate_fn(symbol, decision.side, features)
            valeur = float(brut)
            if valeur != valeur or valeur in (float("inf"), float("-inf")):
                raise ValueError(f"conviction non finie : {brut!r}")
            # Bornage : le LLM ne peut pas s'octroyer plus que la pleine taille.
            conviction = min(1.0, max(0.0, valeur))
            source = "délibération"
            if conviction != valeur:
                source = "délibération (bornée)"
        except Exception as exc:  # noqa: BLE001 — fail-closed : on continue en déterministe
            conviction, source = NEUTRAL_CONVICTION, f"neutre ({type(exc).__name__})"
    elif cfg.deliberate:
        source = "neutre (aucun délibérateur fourni)"

    out.conviction, out.conviction_source = conviction, source
    out._step(f"délibération : conviction {conviction:.2f} — {source}")

    # ══ 3. RISKGATE — le dernier mot. Le LLM ne peut pas le contourner.
    gate = risk_gate or RiskGate(rr_ratio=cfg.rr_ratio)
    try:
        entree = RiskInput(side=decision.side, confidence=conviction,
                           **{k: v for k, v in risk_context.items()
                              if k not in ("side", "confidence")})
    except TypeError as exc:
        out.stopped_at = STEP_RISK
        out.reason = f"CONTEXTE_RISQUE_INVALIDE: {exc}"
        return out

    verdict = gate.evaluate(entree)
    out.risk_verdict = verdict.verdict
    out.risk_money = verdict.allowed_risk_money
    out.stop_distance = verdict.stop_distance
    out._step(f"risque : {verdict.verdict} — {verdict.reason} "
              f"({verdict.allowed_risk_money} à risque)")

    if verdict.verdict == DENY:
        out.stopped_at = STEP_RISK
        out.reason = verdict.reason
        return out

    # ══ 4. EXÉCUTION — derrière le mur, désarmée par défaut.
    if not cfg.execute:
        out.stopped_at = STEP_EXECUTION
        out.reason = "EXECUTION_NON_DEMANDEE"
        out._step("exécution : non demandée (décision seule)")
        return out

    if policy is None:
        out.stopped_at = STEP_EXECUTION
        out.reason = "AUCUNE_POLITIQUE"
        out._step("exécution : refusée, aucun mur fourni")
        return out

    tp = (verdict.stop_distance * cfg.rr_ratio) if verdict.stop_distance else None
    out.order = place_market_order(
        symbol, decision.side, verdict.allowed_risk_money,
        verdict.stop_distance or 0.0, policy=policy, tp_distance=tp,
        idempotency_key=idempotency_key,
    )
    out.reason = out.order.reason
    out.stopped_at = "" if out.order.sent else STEP_EXECUTION
    out._step(f"exécution : {'envoyé' if out.order.sent else 'refusé'} — {out.order.reason}")
    return out


def explain(out: Outcome) -> str:
    """Rend un Outcome lisible par un humain — pour le journal et le dashboard."""
    verdict = "EXÉCUTÉ" if out.executed else "AUCUN ORDRE"
    lignes = [f"{out.symbol} — {verdict}"]
    lignes += [f"  {i + 1}. {t}" for i, t in enumerate(out.trace)]
    if not out.executed:
        etage = out.stopped_at or "?"
        lignes.append(f"  ⇒ arrêté à l'étage « {etage} » : {out.reason}")
    return "\n".join(lignes)
