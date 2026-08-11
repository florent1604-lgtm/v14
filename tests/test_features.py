"""Tests des détecteurs et de l'assemblage des features.

C'est la pièce qui alimente la porte de confluence. Un détecteur qui rend
silencieusement 0 ou False rend un pilier **impossible à passer** sans que rien
ne le signale — c'est exactement ce qui s'était produit en V12 (pilier liquidité
figé à 0/20 en réel). Les tests visent donc en priorité les modes de panne
silencieux.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from titanium.features import candlesticks, smc
from titanium.features.builder import (
    NEUTRAL_EMOTION,
    _liquidity,
    _normalize,
    _ote_ob,
    _setup_family,
    _setup_side,
    _trend,
    _weekend_block,
    build_feats,
    risk_context_from,
)
from titanium.features.structure import (
    compute_fib_ote,
    compute_profile,
    compute_sr_levels,
    fib_context,
    sr_context,
    volume_context,
)
from titanium.gates import confluence_gate


def bougies(n=400, depart=100.0, pente=0.0, graine=7) -> pd.DataFrame:
    rng = np.random.default_rng(graine)
    base = np.cumsum(rng.normal(pente, 0.3, n)) + depart
    return pd.DataFrame({
        "open": base,
        "high": base + abs(rng.normal(0, 0.4, n)),
        "low": base - abs(rng.normal(0, 0.4, n)),
        "close": base + rng.normal(0, 0.1, n),
        "tick_volume": rng.integers(80, 400, n).astype(float),
    })


# ══════════════════════════ normalisation des colonnes ═══════════════════════

def test_tick_volume_devient_v():
    """MT5 rend `tick_volume`. Sans renommage, le profil de volume rend
    `available: False` et le pilier G2 est mort en silence."""
    d = _normalize(bougies(50))
    assert "v" in d.columns
    assert d["v"].sum() > 0


def test_volume_yfinance_reconnu():
    df = bougies(50).rename(columns={"tick_volume": "Volume"})
    assert "v" in _normalize(df).columns


def test_volume_absent_devient_zero():
    """Sans volume du tout, on continue avec 0 — G2 échouera, mais rien ne casse."""
    df = bougies(50).drop(columns=["tick_volume"])
    d = _normalize(df)
    assert "v" in d.columns
    assert (d["v"] == 0).all()


def test_colonnes_manquantes_rejetees():
    assert _normalize(bougies(50).drop(columns=["close"])) is None
    assert _normalize(None) is None
    assert _normalize(pd.DataFrame()) is None


def test_majuscules_toleres():
    df = bougies(50).rename(columns=str.upper)
    assert _normalize(df) is not None


# ═════════════════════════════ garde-fous data ═══════════════════════════════

def test_donnees_insuffisantes_donnent_data_valid_false():
    f = build_feats(bougies(10), bougies(10))
    assert f["data_valid"] is False
    assert confluence_gate.evaluate(f).code == "BLOCK_DATA_INVALID"


def test_aucune_donnee_ne_leve_pas():
    for a, b in ((None, None), (bougies(400), None), (None, bougies(400))):
        assert build_feats(a, b)["data_valid"] is False


def test_prix_invalide_donne_data_valid_false():
    for mauvais in (0, -1, float("nan")):
        assert build_feats(bougies(400), bougies(400), price=mauvais)["data_valid"] is False


def test_features_invalides_portent_quand_meme_emotion_et_cost():
    """La porte BLOQUE si `emotion` ou `cost` manquent. Même un dict invalide
    doit donc les porter, sinon le motif du refus devient trompeur."""
    f = build_feats(None, None)
    assert set(f["emotion"]) >= {"filter_block", "stale", "confidence", "wait"}
    assert set(f["cost"]) >= {"edge_ok", "weekend_block"}


# ════════════════════════════════ tendance ═══════════════════════════════════

def test_tendance_haussiere():
    assert _trend(_normalize(bougies(400, pente=0.05))) == 1


def test_tendance_baissiere():
    assert _trend(_normalize(bougies(400, pente=-0.05))) == -1


def test_historique_trop_court_donne_tendance_indeterminee():
    """L'EMA 200 exige 200 barres. Moins = 0, jamais une direction inventée."""
    assert _trend(_normalize(bougies(150))) == 0


# ═════════════════════ sens et famille du setup ══════════════════════════════

def test_support_donne_un_long():
    assert _setup_side({"available": True, "on_level": 100.0,
                        "on_level_kind": "support"}) == 1


def test_resistance_donne_un_short():
    assert _setup_side({"available": True, "on_level": 100.0,
                        "on_level_kind": "resistance"}) == -1


def test_hors_niveau_aucun_setup():
    assert _setup_side({"available": True, "on_level": None}) is None
    assert _setup_side({"available": False}) is None


def test_famille_continuation_et_reversal():
    assert _setup_family(1, 1) == "continuation"
    assert _setup_family(-1, -1) == "continuation"
    assert _setup_family(1, -1) == "reversal"
    assert _setup_family(1, 0) == "reversal", "tendance indéterminée ⇒ reversal"
    assert _setup_family(None, 1) is None


# ══════════════════════════════ liquidité ════════════════════════════════════

def test_liquidite_ambigue_vaut_zero(monkeypatch):
    """LE bug de V12 : les deux côtés vrais en même temps ⇒ pilier figé à 0/20.
    Le comportement correct est bien 0 — mais il doit venir d'une ambiguïté
    réelle, pas d'une FVG rebouchée comptée comme signal."""
    monkeypatch.setattr(smc, "detect_liquidity_sweep", lambda *a, **k: True)
    assert _liquidity(_normalize(bougies(200)), 100.0, 0.5) == 0


def test_liquidite_directionnelle(monkeypatch):
    monkeypatch.setattr(smc, "detect_liquidity_sweep",
                        lambda df, side, atr=None, **k: smc.BUY in side)
    assert _liquidity(_normalize(bougies(200)), 100.0, 0.5) == 1


def test_fvg_comblee_nest_pas_un_signal():
    """Une FVG rebouchée n'est plus un déséquilibre : le prix est repassé au
    travers. C'est le correctif Codex du 22/07 qui a débloqué le pilier."""
    df = _normalize(bougies(200))
    toutes = smc.detect_fvg(df, smc.BUY)
    ouvertes = smc.detect_fvg_unfilled(df, smc.BUY)
    assert len(ouvertes) <= len(toutes)


# ════════════════════════════════ OTE / OB ═══════════════════════════════════

def test_ote_exige_la_zone_ET_lob():
    """Le pilier s'appelle « ote_ob » : le bug d'origine ne testait que l'OTE."""
    df = _normalize(bougies(200))
    assert _ote_ob(df, 100.0, {"available": False}) == 0
    assert _ote_ob(df, 100.0, {"available": True, "in_ote": False, "direction": 1}) == 0


def test_structure_invalidee_annule_lote():
    df = _normalize(bougies(200))
    ctx = {"available": True, "in_ote": True, "direction": 1, "invalidated": True}
    assert _ote_ob(df, 100.0, ctx) == 0


def test_ote_accepte_une_fvg_comme_alignement(monkeypatch):
    """Rejeter le statut « fvg » rendait G4 inatteignable par ce chemin — c'est
    le correctif présent dans la copie VIVANTE de V12 et absent de la copie morte."""
    monkeypatch.setattr(smc, "has_ob_or_fvg_alignment", lambda *a, **k: (True, "fvg", 0.85))
    ctx = {"available": True, "in_ote": True, "direction": 1, "invalidated": False}
    assert _ote_ob(_normalize(bougies(200)), 100.0, ctx) == 1


def test_ob_casse_nest_pas_un_alignement(monkeypatch):
    monkeypatch.setattr(smc, "has_ob_or_fvg_alignment", lambda *a, **k: (True, "broken", 0.0))
    ctx = {"available": True, "in_ote": True, "direction": 1, "invalidated": False}
    assert _ote_ob(_normalize(bougies(200)), 100.0, ctx) == 0


# ═════════════════════════════ bougie (réduit) ═══════════════════════════════

def test_signaux_opposes_rendent_la_bougie_muette():
    """Deux patterns contradictoires ⇒ 0, jamais un choix arbitraire."""
    df = _normalize(bougies(50))
    b = candlesticks.net_bias_on_df(df)
    assert b["direction"] in (-1, 0, 1)
    if b["direction"] == 0:
        assert b["score"] == 0.0


def test_avalement_haussier_reconnu():
    """Avalement au sens strict : la bougie verte doit ENGLOBER le corps rouge
    précédent (ouvrir sous sa clôture, clôturer au-dessus de son ouverture) et
    avoir un corps plus grand."""
    df = pd.DataFrame({
        "open":  [100.0, 101.0,  98.8],   # corps rouge [99.0 ; 101.0]
        "high":  [101.0, 101.2, 102.0],
        "low":   [ 99.0,  98.8,  98.5],
        "close": [100.5,  99.0, 101.5],   # corps vert [98.8 ; 101.5] : englobe
    })
    b = candlesticks.net_bias_on_df(df)
    assert b["direction"] == 1
    assert "avalement_haussier" in b["patterns"]


def test_avalement_partiel_refuse():
    """Une verte qui n'englobe pas complètement le corps rouge n'est pas un
    avalement — sinon presque toute bougie verte en deviendrait un."""
    df = pd.DataFrame({
        "open":  [100.0, 101.0,  99.5],   # 99.5 > clôture rouge 99.0
        "high":  [101.0, 101.2, 102.0],
        "low":   [ 99.0,  98.8,  99.3],
        "close": [100.5,  99.0, 101.5],
    })
    assert "avalement_haussier" not in candlesticks.net_bias_on_df(df)["patterns"]


def test_bougie_trop_courte_est_muette():
    assert candlesticks.net_bias_on_df(pd.DataFrame({"open": [1], "high": [1],
                                         "low": [1], "close": [1]}))["direction"] == 0
    assert candlesticks.net_bias_on_df(None)["direction"] == 0


# ═══════════════════════════ coût et week-end ════════════════════════════════

def test_edge_inconnu_par_defaut():
    """`edge_ok=True` par défaut serait un fail-OPEN — l'erreur corrigée en V12."""
    f = build_feats(bougies(400), bougies(400))
    assert f["cost"]["edge_ok"] is None


def test_edge_inconnu_bloque_en_prod():
    """`edge_ok=None` bloque en PROD — l'edge inconnu n'autorise jamais.

    ⚠️ Le blocage de week-end s'interpose AVANT celui d'edge : lancé un
    samedi, ce test recevait `BLOCK_COST_WEEKEND` et échouait. Il passait
    donc du lundi au vendredi et tombait le week-end — un test qui dépend
    du jour ne verrouille rien. On neutralise explicitement le week-end
    pour que l'assertion porte sur ce qu'elle prétend tester.
    """
    f = build_feats(bougies(400), bougies(400))
    f.update(setup_side=1, setup_family="continuation", trend=1, on_sr_level=True,
             fair_value=True, liquidity=1, ote=1, candle=1)
    f["cost"] = {**(f.get("cost") or {}), "edge_ok": None,
                 "weekend_block": False}
    assert confluence_gate.evaluate(f, require_edge=True).code == "BLOCK_EDGE_UNPROVEN"


def test_weekend_bloque_avant_l_edge():
    """L'ordre des vétos est délibéré : marché fermé prime sur edge inconnu.

    Les deux bloquent, mais le motif doit nommer la cause la plus proche —
    un actif dont le marché est fermé n'a pas de problème d'edge, il a un
    problème d'horaire.
    """
    f = build_feats(bougies(400), bougies(400))
    f.update(setup_side=1, setup_family="continuation", trend=1, on_sr_level=True,
             fair_value=True, liquidity=1, ote=1, candle=1)
    f["cost"] = {**(f.get("cost") or {}), "edge_ok": None,
                 "weekend_block": True}
    assert confluence_gate.evaluate(f, require_edge=True).code == "BLOCK_COST_WEEKEND"


@pytest.mark.parametrize("quand,attendu", [
    (datetime(2026, 8, 7, 19, 0, tzinfo=timezone.utc), False),  # vendredi 19 h
    (datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc), True),   # vendredi 20 h
    (datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc), True),   # samedi
    (datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc), True),   # dimanche
    (datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc), False),  # lundi
])
def test_fenetre_week_end(quand, attendu):
    assert _weekend_block(quand) is attendu


def test_week_end_bloque_la_porte():
    f = build_feats(bougies(400), bougies(400),
                    now=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc))
    f.update(setup_side=1, setup_family="continuation", trend=1, on_sr_level=True,
             fair_value=True, liquidity=1, ote=1, candle=1)
    assert confluence_gate.evaluate(f).code == "BLOCK_COST_WEEKEND"


# ══════════════════════════════ émotion ══════════════════════════════════════

def test_emotion_neutre_par_defaut():
    f = build_feats(bougies(400), bougies(400))
    assert f["emotion"] == NEUTRAL_EMOTION


def test_emotion_fournie_est_transmise():
    emo = {"filter_block": 1, "stale": False, "confidence": 0.9, "wait": False}
    assert build_feats(bougies(400), bougies(400), emotion=emo)["emotion"] == emo


def test_emotion_transmise_est_une_copie():
    """Le dict de l'appelant ne doit pas être muté par la chaîne."""
    emo = dict(NEUTRAL_EMOTION)
    f = build_feats(bougies(400), bougies(400), emotion=emo)
    f["emotion"]["confidence"] = 0.0
    assert emo["confidence"] == 1.0


# ═══════════════════════ contrat complet avec la porte ═══════════════════════

def test_le_dict_produit_est_accepte_par_la_porte():
    """Le test qui relie les deux moitiés : la porte ne doit jamais lever sur un
    dict produit par le constructeur."""
    d = confluence_gate.evaluate(build_feats(bougies(400), bougies(400)))
    assert d.verdict in ("ENTER", "WAIT", "BLOCK")


def test_toutes_les_cles_attendues_sont_presentes():
    f = build_feats(bougies(400), bougies(400))
    for cle in ("data_valid", "trend", "setup_side", "setup_family", "on_sr_level",
                "fair_value", "liquidity", "ote", "candle", "emotion", "cost",
                "strengths"):
        assert cle in f, f"clé manquante : {cle}"


def test_les_strengths_couvrent_les_cinq_piliers():
    s = build_feats(bougies(400), bougies(400))["strengths"]
    assert set(s) == {"trend_sr", "fair_value", "liquidity", "ote_ob", "candle_confirmed"}


def test_la_trace_est_exploitable():
    t = build_feats(bougies(400), bougies(400))["_trace"]
    assert t["version"]
    assert t["price"] > 0
    assert t["bars"]["ltf"] > 0


def test_horodatage_de_la_trace_lu_par_la_porte():
    quand = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    f = build_feats(bougies(400), bougies(400), now=quand)
    assert confluence_gate.evaluate(f).decided_at == quand.isoformat()


# ═════════════════════════ contexte de risque ════════════════════════════════

def test_contexte_de_risque_reprend_prix_et_atr():
    f = build_feats(bougies(400), bougies(400))
    ctx = risk_context_from(f, equity=1000.0, risk_pct=1.0)
    assert ctx["price"] == f["_trace"]["price"]
    assert ctx["atr"] == f["_trace"]["atr"]
    assert ctx["equity"] == 1000.0


def test_contexte_de_risque_sans_side_ni_confidence():
    """Ces deux champs viennent des portes et de la délibération."""
    ctx = risk_context_from(build_feats(bougies(400), bougies(400)),
                            equity=1000.0, risk_pct=1.0)
    assert "side" not in ctx
    assert "confidence" not in ctx


def test_contexte_de_risque_surchargeable():
    ctx = risk_context_from(build_feats(bougies(400), bougies(400)),
                            equity=1000.0, risk_pct=1.0, circuit_breaker=True)
    assert ctx["circuit_breaker"] is True


def test_contexte_accepte_par_le_riskgate():
    from titanium.risk.riskgate import RiskGate, RiskInput
    f = build_feats(bougies(400), bougies(400))
    ctx = risk_context_from(f, equity=1000.0, risk_pct=1.0)
    d = RiskGate().evaluate(RiskInput(side=1, confidence=0.8, **ctx))
    assert d.verdict in ("ALLOW", "REDUCE", "DENY")


# ═══════════════════════ briques de structure ════════════════════════════════

def test_niveaux_sr_tries_par_force():
    niveaux = compute_sr_levels(_normalize(bougies(400)))
    assert niveaux
    assert all(a.strength >= b.strength for a, b in zip(niveaux, niveaux[1:]))
    assert all(lv.kind in ("support", "resistance") for lv in niveaux)


def test_supports_sous_le_prix_resistances_au_dessus():
    df = _normalize(bougies(400))
    prix = float(df["close"].iloc[-1])
    for lv in compute_sr_levels(df):
        assert (lv.price < prix) == (lv.kind == "support")


def test_profil_de_volume_coherent():
    prof = compute_profile(_normalize(bougies(400)), window=200)
    assert prof is not None
    assert prof.val <= prof.vpoc <= prof.vah


def test_profil_sans_volume_est_indisponible():
    df = _normalize(bougies(400).drop(columns=["tick_volume"]))
    assert compute_profile(df) is None


def test_zone_ote_du_bon_cote():
    fz = compute_fib_ote(_normalize(bougies(400)))
    if fz is not None:
        assert fz.direction in (-1, 1)
        lo, hi = sorted((fz.ote_low, fz.ote_high))
        assert lo <= fz.golden <= hi


@pytest.mark.parametrize("fn", [sr_context, volume_context, fib_context])
def test_contextes_indisponibles_sur_prix_absurde(fn):
    assert fn(_normalize(bougies(400)), -1)["available"] is False


@pytest.mark.parametrize("fn", [sr_context, volume_context, fib_context])
def test_contextes_indisponibles_sur_historique_court(fn):
    assert fn(_normalize(bougies(8)), 100.0)["available"] is False


class TestPanelDiffere:
    """Le panel d'indicateurs ne doit JAMAIS entrer dans la décision.

    Mesuré le 07/08/2026 : 392 ms avec panel, 30 ms sans — 92 % du coût
    d'un balayage pour une information qu'aucune porte ne lit. Il est donc
    calculé sur le seul chemin ENTER. Ce test verrouille l'invariant qui
    rend ce report légitime : sans lui, différer le panel changerait les
    décisions au lieu de les accélérer.
    """

    def _feats(self, avec):
        import numpy as np
        import pandas as pd

        from titanium.features.builder import build_feats

        rng = np.random.default_rng(7)
        n = 420
        base = 1.10 + np.cumsum(rng.normal(0, 0.0004, n))
        idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "open": base, "high": base + 0.0006,
            "low": base - 0.0006, "close": base,
            "v": rng.integers(80, 900, n).astype(float)}, index=idx)
        return build_feats(df, df, with_indicators=avec)

    def test_le_verdict_est_identique_avec_ou_sans_panel(self):
        from titanium.gates import confluence_gate as cg
        a = cg.evaluate(self._feats(True))
        b = cg.evaluate(self._feats(False))
        assert a.verdict == b.verdict
        assert a.side == b.side
        assert a.support_passed == b.support_passed
        assert a.code == b.code

    def test_le_panel_est_absent_quand_non_demande(self):
        trace = self._feats(False).get("_trace") or {}
        assert not (trace.get("indicators") or {})

    def test_le_panel_est_present_quand_demande(self):
        trace = self._feats(True).get("_trace") or {}
        assert len(trace.get("indicators") or {}) > 0

    def test_la_barre_de_reference_ne_change_pas(self):
        """L'idempotence est ancrée sur `bar_time` : s'il différait entre
        les deux appels, un même setup pourrait être envoyé deux fois."""
        a = (self._feats(True).get("_trace") or {}).get("bar_time")
        b = (self._feats(False).get("_trace") or {}).get("bar_time")
        assert a == b
