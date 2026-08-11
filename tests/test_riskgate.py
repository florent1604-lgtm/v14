"""Tests du RiskGate — la porte unique de risque de V14.

Deux exigences structurent ces tests :

1. **Fail-CLOSED.** C'est LA correction apportée à V12, qui est fail-open. Tout
   ce qui n'est pas explicitement sain doit produire un DENY. Les tests
   d'entrées dégradées (NaN, inf, None, zéro, champs manquants) sont donc au
   moins aussi importants que le cas nominal.

2. **Un DENY porte le nom de son contrôle.** La traçabilité des refus est un
   dataset : on vérifie le `reason` ET la trace `checks`.
"""

from __future__ import annotations

import math

import pytest

from titanium.risk.riskgate import (
    ALLOW,
    DENY,
    EMOTION_FLOOR,
    FUNDAMENTALS_REDUCE_FACTOR,
    NEUTRAL_CONFIDENCE,
    REDUCE,
    Decision,
    RiskGate,
    RiskInput,
    evaluate,
)


def inp(**overrides) -> RiskInput:
    """Une entrée saine : long, données valides, aucun véto, confiance pleine."""
    base = dict(
        side=1, price=100.0, atr=2.0, equity=10_000.0, risk_pct=1.0,
        halted=False, circuit_breaker=False,
        fundamentals_block=False, fundamentals_reduce=False,
        trend=1, gross_exposure_pct=0.0,
        emotion_available=True, emotion_would_block=False,
        confidence=1.0, timeframe="H4", roundtrip_cost=0.8,
    )
    base.update(overrides)
    return RiskInput(**base)


def gate_named(d: Decision, name: str) -> dict:
    return next(c for c in d.checks if c["gate"] == name)


# ─────────────────────────────── cas nominal ────────────────────────────────

def test_entree_saine_autorise():
    d = RiskGate().evaluate(inp())
    assert d.verdict == ALLOW
    assert d.allowed
    assert d.reason == "OK"
    assert d.side == 1
    assert d.size_factor == 1.0


def test_sizing_nominal():
    """10 000 € × 1 % × facteur 1.0 = 100 € de risque."""
    d = RiskGate().evaluate(inp())
    assert d.allowed_risk_money == pytest.approx(100.0)


def test_sl_tp_long():
    """SL = prix − 1.5×ATR ; TP = prix + 1.5×ATR×2 (R:R 2)."""
    d = RiskGate(sl_atr_k=1.5, rr_ratio=2.0).evaluate(inp(price=100.0, atr=2.0))
    assert d.stop_distance == pytest.approx(3.0)
    assert d.sl == pytest.approx(97.0)
    assert d.tp == pytest.approx(106.0)


def test_sl_tp_short_symetrique():
    d = RiskGate(sl_atr_k=1.5, rr_ratio=2.0).evaluate(inp(side=-1, trend=-1, price=100.0, atr=2.0))
    assert d.sl == pytest.approx(103.0)
    assert d.tp == pytest.approx(94.0)


def test_raccourci_fonctionnel():
    assert evaluate(inp()).verdict == ALLOW


# ─────────────────── fail-closed : la correction de fond ────────────────────

def test_valeurs_par_defaut_sont_dangereuses():
    """Un RiskInput minimal doit DENY : les booléens de danger valent True par
    défaut, pour qu'un champ oublié bloque au lieu de laisser passer."""
    d = RiskGate().evaluate(RiskInput(side=1, price=100.0, atr=2.0,
                                      equity=10_000.0, risk_pct=1.0))
    assert d.verdict == DENY
    assert d.reason == "HALT_ACTIF"


def test_ne_leve_jamais_meme_sur_entree_absurde():
    """Une entrée d'un type inattendu ne doit pas propager d'exception vers le
    moteur de trading — elle doit produire un DENY tracé."""
    class Cassee:
        def __getattr__(self, name):
            raise RuntimeError("état corrompu")

    d = RiskGate().evaluate(Cassee())
    assert d.verdict == DENY
    assert d.reason == "ERREUR_INTERNE"
    assert gate_named(d, "internal")["passed"] is False


@pytest.mark.parametrize("champ,valeur", [
    ("price", None), ("price", 0), ("price", -5), ("price", float("nan")),
    ("price", float("inf")), ("price", "cher"),
    ("atr", None), ("atr", 0), ("atr", -1), ("atr", float("nan")),
    ("equity", None), ("equity", 0), ("equity", -100), ("equity", float("nan")),
    ("risk_pct", None), ("risk_pct", 0), ("risk_pct", -1), ("risk_pct", float("nan")),
])
def test_donnees_non_exploitables_denient(champ, valeur):
    d = RiskGate().evaluate(inp(**{champ: valeur}))
    assert d.verdict == DENY
    assert d.reason == "DONNEES_INSUFFISANTES"


def test_exposition_non_finie_denie():
    """Une exposition inconnue n'est jamais traitée comme zéro."""
    d = RiskGate().evaluate(inp(gross_exposure_pct=float("nan")))
    assert d.verdict == DENY
    assert d.reason == "EXPOSITION_INCONNUE"


@pytest.mark.parametrize("side", [0, None, 2, "long"])
def test_sens_invalide_denie(side):
    d = RiskGate().evaluate(inp(side=side))
    assert d.verdict == DENY
    assert d.reason == "PAS_DE_SENS"


# ───────────────────────── vétos, dans l'ordre ──────────────────────────────

def test_halt_prime_sur_tout():
    """HALT gagne même si tous les autres vétos sont aussi actifs."""
    d = RiskGate().evaluate(inp(halted=True, circuit_breaker=True,
                                fundamentals_block=True, side=0))
    assert d.reason == "HALT_ACTIF"
    assert len(d.checks) == 1  # on s'arrête au premier contrôle


def test_fondamentaux_bloquent():
    d = RiskGate().evaluate(inp(fundamentals_block=True))
    assert d.verdict == DENY
    assert d.reason == "FONDAMENTAUX_BLOCK"


def test_circuit_breaker_denie():
    d = RiskGate().evaluate(inp(circuit_breaker=True))
    assert d.verdict == DENY
    assert d.reason == "CIRCUIT_BREAKER"


def test_fondamentaux_priment_sur_circuit_breaker():
    """Ordre de l'entonnoir : le motif rendu doit être le premier rencontré."""
    d = RiskGate().evaluate(inp(fundamentals_block=True, circuit_breaker=True))
    assert d.reason == "FONDAMENTAUX_BLOCK"


def test_anti_fade_bloque_le_contre_tendance():
    d = RiskGate().evaluate(inp(side=-1, trend=1))
    assert d.verdict == DENY
    assert d.reason == "CONTRE_TENDANCE"


def test_anti_fade_inactif_sans_tendance_nette():
    d = RiskGate().evaluate(inp(side=-1, trend=0))
    assert d.verdict == ALLOW


def test_plafond_exposition():
    d = RiskGate(max_exposure_pct=50.0).evaluate(inp(gross_exposure_pct=50.0))
    assert d.verdict == DENY
    assert d.reason == "EXPOSITION_MAX"


def test_exposition_juste_sous_le_plafond_passe():
    d = RiskGate(max_exposure_pct=50.0).evaluate(inp(gross_exposure_pct=49.9))
    assert d.verdict == ALLOW


def test_veto_expositition_avant_calcul_de_taille():
    """V12 dimensionnait puis vétoait : le calcul était jeté. Ici un DENY
    d'exposition ne doit produire aucun SL/TP."""
    d = RiskGate(max_exposure_pct=10.0).evaluate(inp(gross_exposure_pct=99.0))
    assert d.verdict == DENY
    assert d.sl is None and d.tp is None
    assert d.allowed_risk_money == 0.0


# ────────────────────────────── dimensionnement ─────────────────────────────

def test_confiance_module_le_lot():
    """Le lot est proportionnel à la confiance mesurée du contexte."""
    plein = RiskGate().evaluate(inp(confidence=1.0)).allowed_risk_money
    moitie = RiskGate().evaluate(inp(confidence=0.5)).allowed_risk_money
    assert moitie == pytest.approx(plein * 0.5)


def test_confiance_faible_donne_reduce():
    d = RiskGate().evaluate(inp(confidence=0.3))
    assert d.verdict == REDUCE
    assert d.reason == "réduction appliquée"


def test_confiance_bornee():
    assert RiskGate().evaluate(inp(confidence=5.0)).confidence == 1.0
    assert RiskGate().evaluate(inp(confidence=-3.0)).verdict == DENY  # facteur nul


def test_confiance_non_finie_retombe_sur_neutre():
    d = RiskGate().evaluate(inp(confidence=float("nan")))
    assert d.confidence == NEUTRAL_CONFIDENCE


def test_emotion_bloquante_reduit_sans_interdire():
    """L'émotion dimensionne, elle n'est pas une barrière (choix de Florent)."""
    d = RiskGate().evaluate(inp(emotion_available=True, emotion_would_block=True))
    assert d.verdict == REDUCE
    assert d.size_factor == pytest.approx(EMOTION_FLOOR)


def test_emotion_indisponible_reste_neutre():
    d = RiskGate().evaluate(inp(emotion_available=False, emotion_would_block=True))
    assert d.size_factor == pytest.approx(1.0)


def test_reduction_fondamentale():
    d = RiskGate().evaluate(inp(fundamentals_reduce=True))
    assert d.verdict == REDUCE
    assert d.size_factor == pytest.approx(FUNDAMENTALS_REDUCE_FACTOR)


def test_reductions_se_composent():
    """Fondamentaux 0.5 × émotion 0.5 × confiance 0.5 = 0.125."""
    d = RiskGate().evaluate(inp(fundamentals_reduce=True, emotion_would_block=True,
                                confidence=0.5))
    assert d.size_factor == pytest.approx(0.125)
    assert d.allowed_risk_money == pytest.approx(12.5)


def test_taille_nulle_est_un_refus_nomme():
    """Un lot à zéro n'est pas un ALLOW : c'est un refus, il doit se nommer."""
    d = RiskGate().evaluate(inp(confidence=0.0))
    assert d.verdict == DENY
    assert d.reason == "TAILLE_NULLE"


def test_cout_hors_formule():
    """Règle de Florent : le coût est tracé, jamais dans le calcul du lot."""
    cher = RiskGate().evaluate(inp(roundtrip_cost=999.0))
    gratuit = RiskGate().evaluate(inp(roundtrip_cost=0.0))
    assert cher.allowed_risk_money == gratuit.allowed_risk_money
    assert "999.0" in gate_named(cher, "cost_info")["detail"]


# ──────────────────────────────── traçabilité ───────────────────────────────

def test_trace_complete_sur_allow():
    d = RiskGate().evaluate(inp())
    noms = [c["gate"] for c in d.checks]
    assert noms == ["halt", "fundamentals", "circuit_breaker", "side", "market_data",
                    "trend_align", "exposure_cap", "sizing", "cost_info"]
    assert all(c["passed"] for c in d.checks)


def test_deny_nomme_son_controle():
    d = RiskGate().evaluate(inp(circuit_breaker=True))
    echec = gate_named(d, "circuit_breaker")
    assert echec["passed"] is False
    assert d.checks[-1] is echec  # le contrôle fautif est le dernier tracé


def test_decision_est_serialisable():
    """La décision doit pouvoir aller au journal telle quelle."""
    from dataclasses import asdict
    d = asdict(RiskGate().evaluate(inp()))
    assert d["verdict"] == ALLOW
    assert isinstance(d["checks"], list)
    assert all(math.isfinite(d[k]) for k in ("allowed_risk_money", "size_factor", "confidence"))
