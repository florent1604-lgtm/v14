import json
from types import SimpleNamespace

import pytest

import titanium.execution.pending_context as pending_context
from tools.live_demo import (
    _attacher_contexte,
    _code_portabilite,
    _compter_refus_execution,
    _compter_tunnel,
    _journal_coverage,
    _memoriser_contexte_limit,
)


def test_compteur_tunnel_agrege_par_etape_et_motif():
    stats = {}

    _compter_tunnel(stats, "gate_code", "G1_TREND_SR")
    _compter_tunnel(stats, "gate_code", "G1_TREND_SR")
    _compter_tunnel(stats, "gate_code", "G2_STRUCTURE")
    _compter_tunnel(stats, "gate_verdict", "BLOCK")

    assert stats["tunnel"] == {
        "gate_code": {"G1_TREND_SR": 2, "G2_STRUCTURE": 1},
        "gate_verdict": {"BLOCK": 1},
    }


def test_compteur_tunnel_normalise_un_motif_vide():
    stats = {"tunnel": {}}
    _compter_tunnel(stats, "gate_code", "")
    assert stats["tunnel"]["gate_code"] == {"INCONNU": 1}


def test_compteur_tunnel_accepte_un_volume_et_reste_serialisable():
    stats = {}
    _compter_tunnel(stats, "flow", "catalogue", 149)
    _compter_tunnel(stats, "flow", "portables", 4)
    _compter_tunnel(stats, "flow", "portables", 0)
    assert stats["tunnel"]["flow"] == {"catalogue": 149, "portables": 4}
    json.dumps(stats)


def test_couverture_journal_est_stable_et_serialisable():
    coverage = _journal_coverage({
        "mt5_closed": 98,
        "journal_edge": 43,
        "missing_in_edge": 55,
        "missing_in_edge_rate": 55 / 98,
    })

    assert coverage == {
        "mt5_closed": 98,
        "journal_edge": 43,
        "missing_in_edge": 55,
        "missing_in_edge_rate": 55 / 98,
        "lookback_days": 7,
        "reason": "",
    }
    json.dumps(coverage)


def test_refus_execution_est_ventile_par_motif_et_porte():
    stats = {}
    resultat = SimpleNamespace(
        reason="RETCODE_10016",
        checks=[
            {"gate": "wall", "passed": True},
            {"gate": "send", "passed": False, "detail": "invalid stops"},
        ],
    )

    _compter_refus_execution(stats, resultat)

    assert stats["tunnel"] == {
        "post_enter_refusal": {"EXECUTION": 1},
        "execution_refusal": {"RETCODE_10016": 1},
        "execution_gate_failed": {"send": 1},
    }
    json.dumps(stats)


def test_refus_execution_sans_checks_reste_explicite():
    stats = {}
    _compter_refus_execution(stats, SimpleNamespace(reason="WALL_ERREUR", checks=[]))
    assert stats["tunnel"]["execution_gate_failed"] == {"NON_DETAILLE": 1}


def test_contexte_limite_sauve_retourne_une_preuve(monkeypatch, tmp_path):
    import tools.live_demo as live_demo

    monkeypatch.setattr(live_demo, "RACINE", tmp_path)
    monkeypatch.setattr(live_demo, "_contexte_exact", lambda *_: "EURUSD|long|x|3p")
    monkeypatch.setattr(live_demo, "_stratification", lambda *_: {})
    out = SimpleNamespace(side=1, stop_distance=0.005, contre_tendance=True)
    res = SimpleNamespace(
        price=1.1000,
        sl=1.0950,
        tp=1.1075,
        expires_at="2026-08-12T12:00:00+00:00",
        market_reference_price=1.1002,
        spread_saved_price=0.0002,
    )

    ok, reason = _memoriser_contexte_limit(555, "EURUSD", {}, out, res)

    assert (ok, reason) == (True, "SAVED")
    saved = json.loads((tmp_path / "results" / "pending_limits.json").read_text())
    state = saved["555"]["state"]
    assert state["context_key"] == "EURUSD|long|x|3p"
    assert state["limit_order_ticket"] == 555
    assert state["limit_planned_price"] == 1.1000
    assert state["limit_market_reference_price"] == 1.1002
    assert state["limit_target_saving_r"] == pytest.approx(0.04)
    assert state["contre_tendance"] is True


def test_contexte_ordre_marche_ne_se_fait_pas_passer_pour_une_limite(
    monkeypatch, tmp_path,
):
    import tools.live_demo as live_demo

    monkeypatch.setattr(live_demo, "RACINE", tmp_path)
    monkeypatch.setattr(live_demo, "_contexte_exact", lambda *_: "EURUSD|long|x|3p")
    monkeypatch.setattr(live_demo, "_stratification", lambda *_: {})
    out = SimpleNamespace(side=1, stop_distance=0.005, contre_tendance=True)
    res = SimpleNamespace(price=1.1000, sl=1.0950, tp=1.1075)

    _attacher_contexte(999, "EURUSD", {}, out, res, risque_devise=25.0)

    state = json.loads((tmp_path / "results" / "positions.json").read_text())["999"]
    assert state["limit_order_ticket"] == 0
    assert state["limit_planned_price"] == 0.0
    assert state["limit_market_reference_price"] == 0.0
    assert state["limit_target_saving_r"] is None
    assert state["contre_tendance"] is True


def test_echec_contexte_limite_n_est_plus_silencieux(monkeypatch):
    def fail(*_args, **_kwargs):
        raise TypeError("champ de stratification inconnu")

    monkeypatch.setattr(pending_context, "save_pending_context", fail)
    out = SimpleNamespace(side=1, stop_distance=0.005)
    res = SimpleNamespace(
        price=1.1000,
        sl=1.0950,
        tp=1.1075,
        expires_at="2026-08-12T12:00:00+00:00",
    )

    assert _memoriser_contexte_limit(555, "EURUSD", {}, out, res) == (
        False,
        "ERROR_TYPEERROR",
    )


def test_motifs_de_portabilite_sont_stables():
    assert _code_portabilite("marché fermé") == "MARCHE_FERME"
    assert _code_portabilite("actif hors de portée : lot minimum") == "LOT_MIN_HORS_PORTEE"
    assert _code_portabilite("spread trop élevé") == "COUT_SPREAD"
    assert _code_portabilite("injouable à toute échelle — meilleur H4 à 20 %") == "COUT_SPREAD"


def test_niveaux_entree_calcule_les_distances_en_r():
    from tools.live_demo import _niveaux_entree

    feats = {"_trace": {
        "sr_level": 1.0950, "vpoc": 1.0980,
        "ote_zone": (1.0960, 1.0970),
        "fvg_open": [[1.0990, 1.1000]],
        "atr": 0.0040,
    }}
    # Long, entree 1.1000, R = 0.0100 -> sr_level est 0.0050 SOUS l'entree,
    # cote defavorable pour un long : dist_sr_r = (1.0950-1.1000)/0.01*1 = -0.5
    niveaux = _niveaux_entree(feats, entry=1.1000, side=1, r=0.0100)

    assert niveaux["sr_level"] == 1.0950
    assert niveaux["vpoc"] == 1.0980
    assert niveaux["ote_zone"] == [1.0960, 1.0970]
    assert niveaux["fvg_open"] == [[1.0990, 1.1000]]
    assert niveaux["dist_sr_r"] == pytest.approx(-0.5)
    assert niveaux["dist_vpoc_r"] == pytest.approx(-0.2)
    # borne la plus proche de l'entree parmi (1.0960, 1.0970)
    assert niveaux["dist_ote_r"] == pytest.approx(-0.3)
    # fvg_open borne haute == entree -> distance nulle, c'est la plus proche
    assert niveaux["dist_fvg_r"] == pytest.approx(0.0)


def test_niveaux_entree_absents_rend_un_dict_de_none():
    from tools.live_demo import _niveaux_entree

    niveaux = _niveaux_entree({}, entry=1.1000, side=1, r=0.0100)
    assert niveaux["sr_level"] is None
    assert niveaux["dist_sr_r"] is None
    assert niveaux["ote_zone"] is None
    assert niveaux["fvg_open"] == []


def test_niveaux_entree_r_invalide_rend_un_dict_vide():
    from tools.live_demo import _niveaux_entree

    assert _niveaux_entree({"_trace": {"sr_level": 1.1}}, entry=1.1, side=1, r=0.0) == {}
    assert _niveaux_entree({"_trace": {"sr_level": 1.1}}, entry=1.1, side=0, r=0.01) == {}


def test_attacher_contexte_journalise_niveaux_et_atr(monkeypatch, tmp_path):
    import tools.live_demo as live_demo

    monkeypatch.setattr(live_demo, "RACINE", tmp_path)
    monkeypatch.setattr(live_demo, "_contexte_exact", lambda *_: "EURUSD|long|x|3p")
    monkeypatch.setattr(live_demo, "_stratification", lambda *_: {})
    out = SimpleNamespace(side=1, stop_distance=0.005)
    res = SimpleNamespace(price=1.1000, sl=1.0950, tp=1.1075)
    feats = {"_trace": {"sr_level": 1.0950, "atr": 0.0040}}

    _attacher_contexte(999, "EURUSD", feats, out, res, risque_devise=25.0)

    state = json.loads((tmp_path / "results" / "positions.json").read_text())["999"]
    assert state["entry_levels"]["sr_level"] == 1.0950
    assert state["entry_atr"] == pytest.approx(0.0040)


def test_memoriser_contexte_limite_journalise_niveaux_et_atr(monkeypatch, tmp_path):
    import tools.live_demo as live_demo

    monkeypatch.setattr(live_demo, "RACINE", tmp_path)
    monkeypatch.setattr(live_demo, "_contexte_exact", lambda *_: "EURUSD|long|x|3p")
    monkeypatch.setattr(live_demo, "_stratification", lambda *_: {})
    out = SimpleNamespace(side=1, stop_distance=0.005)
    res = SimpleNamespace(
        price=1.1000, sl=1.0950, tp=1.1075,
        expires_at="2026-08-12T12:00:00+00:00",
        market_reference_price=1.1002, spread_saved_price=0.0002,
    )
    feats = {"_trace": {"vpoc": 1.0980, "atr": 0.0040}}

    ok, reason = _memoriser_contexte_limit(555, "EURUSD", feats, out, res)

    assert (ok, reason) == (True, "SAVED")
    saved = json.loads((tmp_path / "results" / "pending_limits.json").read_text())
    state = saved["555"]["state"]
    assert state["entry_levels"]["vpoc"] == 1.0980
    assert state["entry_atr"] == pytest.approx(0.0040)
