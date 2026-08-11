"""Tests du délibérateur — conversion note du graphe → conviction.

Le point à verrouiller n'est pas l'arithmétique : c'est que **le graphe ne peut
pas déborder de son rôle**. Il module la taille. Il ne choisit pas le sens, et
il ne peut pas annuler un trade que les portes ont validé.
"""

from __future__ import annotations

import pytest

from titanium.deliberation import (
    CONVICTION_FLOOR,
    NEUTRAL_CONVICTION,
    RATING_BIAS,
    GraphDeliberator,
    conviction_from_rating,
    rating_to_bias,
    static_deliberator,
)
from titanium.orchestrator import run_once


# ════════════════════════ le rôle du graphe est borné ════════════════════════

@pytest.mark.parametrize("note", list(RATING_BIAS))
def test_aucune_note_ne_produit_une_conviction_nulle(note):
    """LA garantie. Une conviction de 0 vaut TAILLE_NULLE côté RiskGate, donc un
    refus : ce serait rendre au graphe le droit de véto qu'on lui a refusé."""
    for side in (1, -1):
        c = conviction_from_rating(note, side)
        assert c >= CONVICTION_FLOOR > 0, f"{note}/{side} annulerait le trade"


@pytest.mark.parametrize("note", list(RATING_BIAS))
def test_aucune_note_ne_depasse_la_pleine_taille(note):
    for side in (1, -1):
        assert conviction_from_rating(note, side) <= 1.0


def test_le_plancher_est_configurable():
    """Si Florent décide un jour que la contradiction doit bloquer, c'est un
    choix explicite — pas un effet de bord."""
    assert conviction_from_rating("Sell", 1, floor=0.0) == 0.0
    assert conviction_from_rating("Sell", 1, floor=0.4) == pytest.approx(0.4)


def test_le_graphe_ne_choisit_pas_le_sens():
    """La conversion ne rend qu'un scalaire : structurellement, elle ne peut
    pas transmettre de direction."""
    c = conviction_from_rating("Sell", 1)
    assert isinstance(c, float)
    assert c > 0


# ═══════════════════════════ concordance ═════════════════════════════════════

def test_accord_parfait_donne_pleine_taille():
    assert conviction_from_rating("Buy", 1) == pytest.approx(1.0)
    assert conviction_from_rating("Sell", -1) == pytest.approx(1.0)


def test_neutre_donne_la_taille_de_reference():
    assert conviction_from_rating("Hold", 1) == pytest.approx(NEUTRAL_CONVICTION)
    assert conviction_from_rating("Hold", -1) == pytest.approx(NEUTRAL_CONVICTION)


def test_desaccord_total_donne_le_plancher():
    assert conviction_from_rating("Sell", 1) == pytest.approx(CONVICTION_FLOOR)
    assert conviction_from_rating("Buy", -1) == pytest.approx(CONVICTION_FLOOR)


def test_notes_intermediaires_entre_les_deux():
    """Overweight sur un long : plus que neutre, moins que Buy."""
    c = conviction_from_rating("Overweight", 1)
    assert NEUTRAL_CONVICTION < c < 1.0
    faible = conviction_from_rating("Underweight", 1)
    assert CONVICTION_FLOOR < faible < NEUTRAL_CONVICTION


def test_symetrie_long_short():
    for note in RATING_BIAS:
        oppose = {"Buy": "Sell", "Overweight": "Underweight", "Hold": "Hold",
                  "Underweight": "Overweight", "Sell": "Buy"}[note]
        assert conviction_from_rating(note, 1) == pytest.approx(
            conviction_from_rating(oppose, -1))


def test_monotonie_sur_un_long():
    ordre = ["Sell", "Underweight", "Hold", "Overweight", "Buy"]
    vals = [conviction_from_rating(n, 1) for n in ordre]
    assert vals == sorted(vals), "plus haussier ⇒ conviction jamais plus faible"


# ═════════════════════════ entrées douteuses ═════════════════════════════════

@pytest.mark.parametrize("brut", ["buy", "BUY", " Buy ", "bUy"])
def test_casse_et_espaces_toleres(brut):
    assert rating_to_bias(brut) == 1.0


@pytest.mark.parametrize("brut", ["Peut-être", "", None, 42, "Strong Buy"])
def test_note_inconnue_est_neutre(brut):
    """Une note qu'on ne sait pas lire ne doit jamais gonfler la taille."""
    assert rating_to_bias(brut) == 0.0
    assert conviction_from_rating(brut, 1) == pytest.approx(NEUTRAL_CONVICTION)


@pytest.mark.parametrize("side", [0, None, 2, "long"])
def test_side_invalide_donne_le_neutre(side):
    assert conviction_from_rating("Buy", side) == pytest.approx(NEUTRAL_CONVICTION)


# ═══════════════════════ délibérateur figé (rejeu) ═══════════════════════════

def test_static_deliberator_respecte_le_contrat():
    d = static_deliberator("Buy")
    assert d("EURUSD", 1, {}) == pytest.approx(1.0)
    assert d("EURUSD", -1, {}) == pytest.approx(CONVICTION_FLOOR)


# ═══════════════════ délibérateur adossé au graphe ═══════════════════════════

class FauxGraphe:
    def __init__(self, note="Buy"):
        self.note = note
        self.appels = []

    def propagate(self, symbole, date, asset_type="stock"):
        self.appels.append((symbole, date))
        return {}, self.note


def brancher(monkeypatch, delib, faux):
    monkeypatch.setattr(delib, "_get_graph", lambda: faux)


def test_deliberateur_graphe_convertit(monkeypatch):
    d = GraphDeliberator(trade_date="2026-08-05")
    brancher(monkeypatch, d, FauxGraphe("Buy"))
    assert d("EURUSD", 1, {}) == pytest.approx(1.0)
    assert d.last["rating"] == "Buy"


def test_le_graphe_nest_lance_quune_fois_par_symbole_et_date(monkeypatch):
    """Un passage coûte plusieurs dizaines d'appels LLM (~96 s mesuré).
    Plusieurs setups le même jour ne doivent pas le repayer."""
    d = GraphDeliberator(trade_date="2026-08-05")
    faux = FauxGraphe("Overweight")
    brancher(monkeypatch, d, faux)
    d("EURUSD", 1, {})
    d("EURUSD", -1, {})
    d("EURUSD", 1, {})
    assert len(faux.appels) == 1


def test_symboles_differents_relancent(monkeypatch):
    d = GraphDeliberator(trade_date="2026-08-05")
    faux = FauxGraphe()
    brancher(monkeypatch, d, faux)
    d("EURUSD", 1, {})
    d("XAUUSD", 1, {})
    assert len(faux.appels) == 2


def test_prose_complete_est_parsee(monkeypatch):
    """Si un appelant rend la décision entière au lieu de la note, on la lit."""
    d = GraphDeliberator(trade_date="2026-08-05")
    brancher(monkeypatch, d,
             FauxGraphe("**Rating**: Overweight\n\nLe setup est correct."))
    d("EURUSD", 1, {})
    assert d.last["rating"] == "Overweight"


def test_instancier_ne_construit_pas_le_graphe():
    """Créer le délibérateur ne doit contacter aucun fournisseur LLM."""
    d = GraphDeliberator(trade_date="2026-08-05")
    assert d._graph is None


def test_erreur_du_graphe_remonte_a_lorchestrateur(monkeypatch):
    """Le délibérateur ne rattrape pas : c'est l'orchestrateur qui est
    fail-closed. Doubler le filet masquerait la cause."""
    class Casse:
        def propagate(self, *a, **k):
            raise ConnectionError("fournisseur LLM injoignable")

    d = GraphDeliberator(trade_date="2026-08-05")
    brancher(monkeypatch, d, Casse())
    with pytest.raises(ConnectionError):
        d("EURUSD", 1, {})


# ════════════════ bout en bout, dans la chaîne complète ══════════════════════

def feats() -> dict:
    return {"data_valid": True, "trend": 1, "setup_side": 1,
            "setup_family": "continuation", "on_sr_level": True,
            "fair_value": True, "liquidity": 1, "ote": 1, "candle": 1,
            "strengths": {},
            "emotion": {"filter_block": None, "stale": False,
                        "confidence": 0.8, "wait": False},
            "cost": {"edge_ok": True, "weekend_block": False}}


def ctx() -> dict:
    return dict(price=1.1540, atr=0.0020, equity=10_000.0, risk_pct=1.0,
                halted=False, circuit_breaker=False, fundamentals_block=False,
                fundamentals_reduce=False, trend=1, gross_exposure_pct=0.0,
                emotion_available=True, emotion_would_block=False,
                timeframe="H4", roundtrip_cost=0.8)


def test_graphe_daccord_donne_une_grosse_taille():
    out = run_once("EURUSD", feats(), ctx(), deliberate_fn=static_deliberator("Buy"))
    assert out.conviction == pytest.approx(1.0)
    assert out.risk_verdict == "ALLOW"
    assert out.risk_money == pytest.approx(100.0)


def test_graphe_en_desaccord_reduit_sans_annuler():
    """Le trade passe quand même — les portes l'ont validé — mais tout petit."""
    out = run_once("EURUSD", feats(), ctx(), deliberate_fn=static_deliberator("Sell"))
    assert out.conviction == pytest.approx(CONVICTION_FLOOR)
    assert out.risk_verdict == "REDUCE"
    assert 0 < out.risk_money < 100.0


def test_graphe_enthousiaste_ne_sauve_pas_un_setup_refuse():
    mauvais = feats()
    mauvais["on_sr_level"] = False
    out = run_once("EURUSD", mauvais, ctx(), deliberate_fn=static_deliberator("Buy"))
    assert out.gate_verdict == "BLOCK"
    assert out.risk_money == 0.0
