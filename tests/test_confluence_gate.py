"""Tests de la porte de confluence — le véto déterministe de V14.

C'est la brique qui décide si un setup atteint la délibération LLM. Un faux
ENTER coûte de l'argent réel ; un faux BLOCK coûte une opportunité. Les deux
sens sont testés.

Convention V12 conservée : `feats` est un dict de features DÉJÀ calculées, donc
tout est testable sans terminal MT5 ni données de marché.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from titanium.gates.confluence_gate import (
    EMOTION_MIN_CONFIDENCE,
    QUORUM_EXPLORE,
    QUORUM_PROD,
    evaluate,
)

FIXED_AT = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


def feats(**overrides) -> dict:
    """Un setup LONG continuation parfait : tous les piliers alignés, modérateurs OK."""
    base = {
        "data_valid": True,
        "trend": 1,
        "setup_side": 1,
        "setup_family": "continuation",
        "on_sr_level": True,
        "fair_value": True,
        "liquidity": 1,
        "ote": 1,
        "candle": 1,
        "strengths": {"trend_sr": 1.0, "fair_value": 0.8, "liquidity": 0.9,
                      "ote_ob": 0.7, "candle_confirmed": 0.6},
        "emotion": {"filter_block": None, "stale": False, "confidence": 0.8, "wait": False},
        "cost": {"edge_ok": True, "weekend_block": False},
    }
    base.update(overrides)
    return base


# ─────────────────────────────── cas nominal ────────────────────────────────

def test_setup_parfait_entre():
    d = evaluate(feats(), decided_at=FIXED_AT)
    assert d.verdict == "ENTER"
    assert d.entered
    assert d.side == 1
    assert d.code == "ENTER_CONFLUENCE"
    assert d.rank == pytest.approx(4.0)  # somme des strengths des 5 portes passées


def test_short_symetrique():
    d = evaluate(feats(trend=-1, setup_side=-1, liquidity=-1, ote=-1, candle=-1),
                 decided_at=FIXED_AT)
    assert d.verdict == "ENTER"
    assert d.side == -1


def test_decision_id_reproductible():
    """Mêmes entrées + même horodatage ⇒ même empreinte. C'est ce qui permet
    de relier une décision du dashboard à ses preuves."""
    a = evaluate(feats(), decided_at=FIXED_AT)
    b = evaluate(feats(), decided_at=FIXED_AT)
    assert a.decision_id == b.decision_id
    assert len(a.decision_id) == 16


def test_decision_id_change_si_une_porte_change():
    a = evaluate(feats(), decided_at=FIXED_AT)
    b = evaluate(feats(fair_value=False), decided_at=FIXED_AT)
    assert a.decision_id != b.decision_id


# ──────────────────────────── G0 : fail-closed ──────────────────────────────

def test_donnees_invalides_bloquent():
    d = evaluate(feats(data_valid=False), decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_DATA_INVALID"


def test_data_valid_absent_bloque():
    """Absence de la clé = fail-closed, pas de valeur par défaut permissive."""
    f = feats()
    del f["data_valid"]
    assert evaluate(f, decided_at=FIXED_AT).code == "BLOCK_DATA_INVALID"


# ──────────────────────── G1 : structure et famille ─────────────────────────

def test_aucun_setup_attend():
    d = evaluate(feats(setup_side=None, setup_family=None), decided_at=FIXED_AT)
    assert d.verdict == "WAIT"
    assert d.code == "WAIT_NO_SETUP"


def test_side_explicite_contradictoire_bloque():
    d = evaluate(feats(), side=-1, decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_SETUP_INVALID"


def test_famille_inconnue_bloque():
    d = evaluate(feats(setup_family="scalp"), decided_at=FIXED_AT)
    assert d.code == "BLOCK_SETUP_INVALID"


def test_setup_side_non_numerique_bloque():
    d = evaluate(feats(setup_side="haut"), decided_at=FIXED_AT)
    assert d.code == "BLOCK_SETUP_INVALID"


def test_continuation_exige_tendance_alignee():
    """En continuation, une tendance opposée fait échouer G1 → BLOCK."""
    d = evaluate(feats(trend=-1), decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_PILLAR_MISSING"


def test_reversal_tolere_tendance_opposee():
    """En reversal, la tendance n'est qu'un contexte : elle ne bloque pas.
    C'est la demande explicite de Florent (« ne pas trop restreindre »)."""
    d = evaluate(feats(setup_family="reversal", trend=-1), decided_at=FIXED_AT)
    assert d.verdict == "ENTER"


def test_reversal_exige_quand_meme_le_niveau_sr():
    d = evaluate(feats(setup_family="reversal", trend=-1, on_sr_level=False),
                 decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_PILLAR_MISSING"


def test_g1_obligatoire_meme_avec_quorum_atteint():
    """G1 n'est pas dans le quorum : sans lui, 4/4 piliers de support ne
    suffisent pas."""
    d = evaluate(feats(on_sr_level=False), decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_PILLAR_MISSING"


# ──────────────────────────── quorum de support ─────────────────────────────

def test_explore_accepte_2_piliers_sur_4():
    d = evaluate(feats(fair_value=False, liquidity=0), require_edge=False,
                 decided_at=FIXED_AT)
    assert d.verdict == "ENTER"
    assert d.mode == "explore"


def test_prod_refuse_2_piliers_sur_4():
    """Même setup : accepté en explore, refusé en prod. C'est toute la
    différence entre mesurer et engager de l'argent."""
    d = evaluate(feats(fair_value=False, liquidity=0), require_edge=True,
                 decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_PILLAR_MISSING"


def test_prod_accepte_3_piliers_sur_4():
    d = evaluate(feats(fair_value=False), require_edge=True, decided_at=FIXED_AT)
    assert d.verdict == "ENTER"
    assert d.mode == "prod"


def test_quorum_explicite_prime_sur_le_mode():
    """Le quorum paramétrable est l'ajout de V14 : il permet de durcir sans
    toucher au code."""
    d = evaluate(feats(fair_value=False), require_edge=True, quorum=4,
                 decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"


def test_quorum_defaut_coherent_avec_les_constantes():
    assert QUORUM_EXPLORE == 2
    assert QUORUM_PROD == 3


def test_pilier_dans_le_mauvais_sens_ne_compte_pas():
    """Un sweep de liquidité SHORT sur un setup LONG n'est pas un demi-point :
    c'est un pilier absent. Le principe même du ET."""
    d = evaluate(feats(liquidity=-1, ote=0), require_edge=True, decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"


# ───────────────────────── modérateurs : émotion ────────────────────────────

def test_emotion_absente_bloque():
    d = evaluate(feats(emotion=None), decided_at=FIXED_AT)
    assert d.code == "BLOCK_EMOTION_UNAVAILABLE"


def test_emotion_incomplete_bloque():
    d = evaluate(feats(emotion={"stale": False}), decided_at=FIXED_AT)
    assert d.code == "BLOCK_EMOTION_UNAVAILABLE"


def test_emotion_bloque_ce_cote():
    d = evaluate(feats(emotion={"filter_block": 1, "stale": False,
                                "confidence": 0.9, "wait": False}),
                 decided_at=FIXED_AT)
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_EMOTION_SIDE"


def test_emotion_bloque_seulement_le_cote_concerne():
    """filter_block=-1 sur un setup LONG ne doit pas bloquer."""
    d = evaluate(feats(emotion={"filter_block": -1, "stale": False,
                                "confidence": 0.9, "wait": False}),
                 decided_at=FIXED_AT)
    assert d.verdict == "ENTER"


def test_emotion_stale_fait_attendre():
    d = evaluate(feats(emotion={"filter_block": None, "stale": True,
                                "confidence": 0.9, "wait": False}),
                 decided_at=FIXED_AT)
    assert d.verdict == "WAIT"
    assert d.code == "WAIT_EMOTION_TIMING"


def test_emotion_confiance_faible_fait_attendre():
    d = evaluate(feats(emotion={"filter_block": None, "stale": False,
                                "confidence": EMOTION_MIN_CONFIDENCE - 0.01,
                                "wait": False}),
                 decided_at=FIXED_AT)
    assert d.verdict == "WAIT"


def test_emotion_confiance_au_seuil_passe():
    """Le seuil est strictement inférieur : à la valeur exacte, on entre."""
    d = evaluate(feats(emotion={"filter_block": None, "stale": False,
                                "confidence": EMOTION_MIN_CONFIDENCE, "wait": False}),
                 decided_at=FIXED_AT)
    assert d.verdict == "ENTER"


# ────────────────────────── modérateurs : coût ──────────────────────────────

def test_cost_absent_bloque():
    d = evaluate(feats(cost=None), decided_at=FIXED_AT)
    assert d.code == "BLOCK_COST_UNAVAILABLE"


def test_weekend_bloque():
    d = evaluate(feats(cost={"edge_ok": True, "weekend_block": True}),
                 decided_at=FIXED_AT)
    assert d.code == "BLOCK_COST_WEEKEND"


def test_edge_negatif_bloque_partout():
    """Un edge explicitement négatif bloque même en explore : c'est la leçon
    coûteuse de V12 (le coût est le tueur dominant, PF ≈ 0.20)."""
    for require_edge in (False, True):
        d = evaluate(feats(cost={"edge_ok": False, "weekend_block": False}),
                     require_edge=require_edge, decided_at=FIXED_AT)
        assert d.code == "BLOCK_EDGE_NEGATIVE"


def test_edge_inconnu_bloque_en_prod_seulement():
    f = feats(cost={"edge_ok": None, "weekend_block": False})
    assert evaluate(f, require_edge=True, decided_at=FIXED_AT).code == "BLOCK_EDGE_UNPROVEN"
    assert evaluate(f, require_edge=False, decided_at=FIXED_AT).verdict == "ENTER"


# ──────────────────────────────── traçabilité ───────────────────────────────

def test_chaque_porte_est_tracee():
    d = evaluate(feats(fair_value=False), decided_at=FIXED_AT)
    noms = [g.name for g in d.gates]
    assert noms == ["data_valid", "trend_sr", "fair_value", "liquidity",
                    "ote_ob", "candle_confirmed"]
    echec = next(g for g in d.gates if g.name == "fair_value")
    assert not echec.passed
    assert echec.code == "G2_FAIR_VALUE_MISSING"
    assert "MANQUE" in echec.reason


def test_le_rank_ne_decide_jamais():
    """Un rank élevé ne rattrape pas un pilier manquant en prod."""
    fort = feats(fair_value=False, liquidity=0,
                 strengths={k: 10.0 for k in
                            ["trend_sr", "fair_value", "liquidity", "ote_ob", "candle_confirmed"]})
    assert evaluate(fort, require_edge=True, decided_at=FIXED_AT).verdict == "BLOCK"


def test_horodatage_naif_devient_utc():
    d = evaluate(feats(), decided_at=datetime(2026, 8, 6, 12, 0, 0))
    assert d.decided_at is not None
    assert d.decided_at.endswith("+00:00")


def test_horodatage_lu_depuis_la_trace():
    d = evaluate(feats(_trace={"decided_at": "2026-08-06T12:00:00Z"}))
    assert d.decided_at == "2026-08-06T12:00:00+00:00"
