"""Tests de l'orchestrateur — les trois règles de V14, vérifiées.

Ces tests ne valident pas « la chaîne marche ». Ils valident que **le LLM ne
peut pas déborder de son rôle**, ce qui est la seule chose qui rende
l'architecture défendable sur un compte réel :

  1. il ne crée jamais une entrée que les portes ont refusée ;
  2. il n'est jamais appelé quand les portes refusent (le coût) ;
  3. il ne peut pas contourner le RiskGate ;
  4. son indisponibilité n'arrête rien.

Tout est testable hors ligne : l'orchestrateur reçoit des features déjà
calculées et un délibérateur injecté.
"""

from __future__ import annotations

import pytest

from titanium.execution.mt5_executor import ExecutionPolicy
from titanium.orchestrator import (
    NEUTRAL_CONVICTION,
    STEP_EXECUTION,
    STEP_GATES,
    STEP_RISK,
    OrchestratorConfig,
    explain,
    run_once,
)
from titanium.risk.riskgate import ALLOW, DENY, REDUCE


def features(**over) -> dict:
    """Setup LONG continuation parfait : les portes rendront ENTER."""
    base = {
        "data_valid": True, "trend": 1, "setup_side": 1,
        "setup_family": "continuation", "on_sr_level": True,
        "fair_value": True, "liquidity": 1, "ote": 1, "candle": 1,
        "strengths": {},
        "emotion": {"filter_block": None, "stale": False, "confidence": 0.8, "wait": False},
        "cost": {"edge_ok": True, "weekend_block": False},
    }
    base.update(over)
    return base


def risque(**over) -> dict:
    base = dict(price=1.1000, atr=0.0020, equity=10_000.0, risk_pct=1.0,
                halted=False, circuit_breaker=False,
                fundamentals_block=False, fundamentals_reduce=False,
                trend=1, gross_exposure_pct=0.0,
                emotion_available=True, emotion_would_block=False,
                timeframe="H4", roundtrip_cost=0.8)
    base.update(over)
    return base


# ═══════════ Règle 1 — le LLM ne crée jamais une entrée refusée ══════════════

def test_conviction_maximale_ne_sauve_pas_un_setup_bloque():
    """LA règle. Une conviction de 1.0 sur un setup que les portes refusent ne
    doit produire aucun ordre, aucune taille, rien."""
    out = run_once("EURUSD", features(on_sr_level=False), risque(),
                   deliberate_fn=lambda *a: 1.0)
    assert out.stopped_at == STEP_GATES
    assert out.gate_verdict == "BLOCK"
    assert out.risk_money == 0.0
    assert out.order is None


def test_le_llm_ne_peut_pas_inverser_le_sens():
    """Le sens vient des portes. Le délibérateur n'a aucun moyen de le toucher."""
    out = run_once("EURUSD", features(setup_side=-1, trend=-1, liquidity=-1,
                                      ote=-1, candle=-1),
                   risque(trend=-1), deliberate_fn=lambda *a: 1.0)
    assert out.side == -1, "le sens doit rester celui des portes"


def test_donnees_invalides_arretent_avant_tout():
    out = run_once("EURUSD", features(data_valid=False), risque(),
                   deliberate_fn=lambda *a: 1.0)
    assert out.stopped_at == STEP_GATES
    assert out.gate_code == "BLOCK_DATA_INVALID"


def test_wait_narrive_pas_a_lexecution():
    """WAIT n'est pas ENTER : la chaîne s'arrête, même avec forte conviction."""
    out = run_once("EURUSD",
                   features(emotion={"filter_block": None, "stale": True,
                                     "confidence": 0.9, "wait": False}),
                   risque(), deliberate_fn=lambda *a: 1.0)
    assert out.gate_verdict == "WAIT"
    assert out.stopped_at == STEP_GATES


# ═══════════ Règle 2 — aucun appel LLM hors du chemin validé ═════════════════

def test_le_delibarateur_nest_pas_appele_si_les_portes_refusent():
    """C'est la porte de COÛT. Sur 149 actifs, appeler le LLM avant les portes
    coûterait des milliers de requêtes par jour au lieu de quelques-unes."""
    appels = []
    run_once("EURUSD", features(on_sr_level=False), risque(),
             deliberate_fn=lambda *a: appels.append(1) or 0.9)
    assert appels == [], "aucun appel LLM ne doit précéder le véto déterministe"


def test_le_delibarateur_est_appele_sur_un_enter():
    appels = []

    def delib(symbole, side, feats):
        appels.append((symbole, side))
        return 0.9

    run_once("EURUSD", features(), risque(), deliberate_fn=delib)
    assert appels == [("EURUSD", 1)]


def test_mode_deterministe_pur_nappelle_jamais_le_llm():
    appels = []
    out = run_once("EURUSD", features(), risque(),
                   config=OrchestratorConfig(deliberate=False),
                   deliberate_fn=lambda *a: appels.append(1) or 1.0)
    assert appels == []
    assert out.conviction == NEUTRAL_CONVICTION
    assert out.risk_verdict in (ALLOW, REDUCE)


# ═══════════ Règle 3 — le RiskGate garde le dernier mot ══════════════════════

def test_le_llm_ne_contourne_pas_le_riskgate():
    """Conviction pleine, mais circuit breaker actif : rien ne passe."""
    out = run_once("EURUSD", features(), risque(circuit_breaker=True),
                   deliberate_fn=lambda *a: 1.0)
    assert out.stopped_at == STEP_RISK
    assert out.risk_verdict == DENY
    assert out.reason == "CIRCUIT_BREAKER"


def test_halt_prime_sur_tout():
    out = run_once("EURUSD", features(), risque(halted=True),
                   deliberate_fn=lambda *a: 1.0)
    assert out.risk_verdict == DENY
    assert out.reason == "HALT_ACTIF"


def test_la_conviction_module_la_taille_et_rien_dautre():
    plein = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: 1.0)
    moitie = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: 0.5)
    assert moitie.risk_money == pytest.approx(plein.risk_money * 0.5)
    assert moitie.side == plein.side
    assert moitie.stop_distance == plein.stop_distance


def test_conviction_bornee_a_un():
    """Le LLM ne peut pas s'octroyer plus que la pleine taille."""
    out = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: 99.0)
    assert out.conviction == 1.0
    assert "bornée" in out.conviction_source


def test_conviction_nulle_devient_un_refus_nomme():
    out = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: 0.0)
    assert out.risk_verdict == DENY
    assert out.reason == "TAILLE_NULLE"


# ═══════════ Règle 4 — fail-closed : rien ne casse la chaîne ═════════════════

def test_delibarateur_qui_plante_retombe_sur_neutre():
    """Le LLM tombe : on continue en déterministe, on ne bloque pas."""
    def casse(*a):
        raise ConnectionError("API injoignable")

    out = run_once("EURUSD", features(), risque(), deliberate_fn=casse)
    assert out.conviction == NEUTRAL_CONVICTION
    assert "ConnectionError" in out.conviction_source
    assert out.risk_verdict in (ALLOW, REDUCE), "la chaîne doit continuer"


@pytest.mark.parametrize("retour", [None, "beaucoup", float("nan"), float("inf"), object()])
def test_conviction_illisible_retombe_sur_neutre(retour):
    out = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: retour)
    assert out.conviction == NEUTRAL_CONVICTION


def test_conviction_negative_bornee_a_zero():
    out = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: -5.0)
    assert out.conviction == 0.0


def test_features_corrompues_ne_levent_pas():
    out = run_once("EURUSD", None, risque(), deliberate_fn=lambda *a: 0.9)
    assert out.stopped_at == STEP_GATES
    assert "GATES_ERREUR" in out.reason


def test_contexte_de_risque_incomplet_ne_leve_pas():
    out = run_once("EURUSD", features(), {"champ_inconnu": 1},
                   deliberate_fn=lambda *a: 0.9)
    assert out.stopped_at == STEP_RISK
    assert "CONTEXTE_RISQUE_INVALIDE" in out.reason


def test_side_et_confidence_du_contexte_sont_ignores():
    """Ces deux champs viennent des étages précédents : un appelant qui les
    fournit ne doit pas pouvoir écraser la décision des portes."""
    ctx = risque()
    ctx["side"] = -1
    ctx["confidence"] = 1.0
    out = run_once("EURUSD", features(), ctx, deliberate_fn=lambda *a: 0.3)
    assert out.side == 1, "le sens des portes doit primer"
    assert out.conviction == pytest.approx(0.3)


# ═════════════════════════ exécution : désarmée ══════════════════════════════

def test_execution_non_demandee_par_defaut():
    out = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: 0.9)
    assert out.order is None
    assert out.stopped_at == STEP_EXECUTION
    assert out.reason == "EXECUTION_NON_DEMANDEE"


def test_execution_demandee_sans_mur_refusee():
    out = run_once("EURUSD", features(), risque(),
                   config=OrchestratorConfig(execute=True),
                   deliberate_fn=lambda *a: 0.9)
    assert out.reason == "AUCUNE_POLITIQUE"
    assert not out.executed


def test_execution_avec_mur_ferme_ne_part_pas():
    out = run_once("EURUSD", features(), risque(),
                   config=OrchestratorConfig(execute=True),
                   policy=ExecutionPolicy(),          # désarmé
                   deliberate_fn=lambda *a: 0.9)
    assert not out.executed
    assert out.order is not None
    assert out.order.reason == "EXEC_DISARMED"


# ═══════════════════════════ modes et traçabilité ════════════════════════════

def test_mode_prod_est_plus_strict_que_explore():
    """Même setup, deux modes : quorum 3/4 en prod contre 2/4 en explore."""
    partiel = features(fair_value=False, liquidity=0)
    assert run_once("EURUSD", partiel, risque()).gate_verdict == "ENTER"
    strict = run_once("EURUSD", partiel, risque(),
                      config=OrchestratorConfig(require_edge=True))
    assert strict.gate_verdict == "BLOCK"


def test_la_trace_couvre_chaque_etage():
    out = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: 0.9)
    joint = " | ".join(out.trace)
    assert "portes" in joint
    assert "délibération" in joint
    assert "risque" in joint
    assert "exécution" in joint


def test_explain_nomme_letage_darret():
    out = run_once("EURUSD", features(on_sr_level=False), risque())
    texte = explain(out)
    assert "EURUSD" in texte
    assert STEP_GATES in texte
    assert "BLOCK_PILLAR_MISSING" in texte


def test_outcome_serialisable():
    from dataclasses import asdict
    out = run_once("EURUSD", features(), risque(), deliberate_fn=lambda *a: 0.7)
    d = asdict(out)
    assert d["symbol"] == "EURUSD"
    assert d["conviction"] == pytest.approx(0.7)
    assert isinstance(d["trace"], list)
