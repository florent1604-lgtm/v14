from datetime import datetime, timedelta, timezone

import pytest

from titanium.organism.contracts import (
    CORTEX_DECISION_MODEL_VERSION,
    CORTEX_DECISION_PRODUCER,
    build_decision_identity,
)
from titanium.organism.cortex import build_cortex_policy, market_observed_at, policy_ttl_s
from titanium.organism.memory import CentralMemory

NOW = datetime(2026, 9, 6, 6, 0, tzinfo=timezone.utc)
CONTEXT = "BTCUSD|long|continuation|4p|tf=M1>H1"


def policy(identity, **overrides):
    kwargs = {"context_key": CONTEXT, "action": "ALLOW", "confidence": .8,
                  "summary": "test", "evidence_digest": "e" * 64, "now": NOW,
                  "decision_model_version": CORTEX_DECISION_MODEL_VERSION,
                  "producer": CORTEX_DECISION_PRODUCER}
    kwargs.update(overrides)
    return build_cortex_policy(identity, **kwargs)


def read(memory, identity, now=NOW):
    return memory.policy_for(identity, CONTEXT, now=now,
                             expected_decision_model=CORTEX_DECISION_MODEL_VERSION,
                             expected_producer=CORTEX_DECISION_PRODUCER)


@pytest.mark.parametrize("change,code", [
    ({"decision_model_version": "qwen3.5:2b"}, "CORTEX_POLICY_MODEL_INVALID"),
    ({"producer": "local-fallback"}, "CORTEX_POLICY_PRODUCER_INVALID"),
])
def test_only_hermes_can_authorize(tmp_path, change, code):
    identity = build_decision_identity({"symbol": "BTCUSD", "side": 1})
    memory = CentralMemory(tmp_path / "core.sqlite3")
    memory.record_policy(policy(identity, **change))
    assert read(memory, identity) == (None, code)


def test_expired_newer_block_never_resurrects_old_allow(tmp_path):
    identity = build_decision_identity({"symbol": "BTCUSD", "side": 1})
    memory = CentralMemory(tmp_path / "core.sqlite3")
    # 60 s, et non 300 : le contexte est en M1 et le plafond suit desormais
    # l'horizon. Une politique M1 de 300 s survivrait a cinq barres. La preuve
    # du test est inchangee — a NOW+20 cette politique est TOUJOURS fraiche et
    # n'est pourtant pas ressuscitee par le BLOCK plus recent qui a expire.
    memory.record_policy(policy(identity, ttl_s=60))
    memory.record_policy(policy(identity, action="BLOCK", ttl_s=10,
                                now=NOW + timedelta(seconds=1)))
    assert read(memory, identity, NOW + timedelta(seconds=20)) == (
        None, "CORTEX_POLICY_STALE",
    )


def test_market_age_not_reset_when_old_bar_is_queued_again():
    observed = market_observed_at("2026-09-06T05:00:00Z", CONTEXT, NOW.isoformat())
    assert observed == NOW - timedelta(minutes=59)


def test_horizons_match_exactly():
    assert policy_ttl_s(CONTEXT) == 60
    # 225 s et non 300 : le TTL vaut desormais un quart de la barre M15.
    assert policy_ttl_s(CONTEXT.replace("M1>", "M15>")) == 225
    assert policy_ttl_s(CONTEXT.replace("M1>", "M5>")) == 120
    with pytest.raises(ValueError):
        market_observed_at("2026-09-06T05:59:00Z", "BTCUSD|long", NOW.isoformat())


def test_worker_does_not_send_expired_requests_to_hermes(tmp_path, monkeypatch):
    from titanium.avis import Demande
    from tools import analystes

    monkeypatch.setattr(analystes, "CENTRAL_MEMORY", CentralMemory(tmp_path / "core.sqlite3"))
    monkeypatch.setattr("titanium.hermes_cortex.analyse_entries", lambda _: pytest.fail("stale"))
    result = analystes._traiter_lot([Demande(
        "BTCUSD", 1, bar_time="2020-01-01T00:00:00Z", engine_context=CONTEXT,
        demande_a=datetime.now(timezone.utc).isoformat(),
    )])
    assert result[0][1].action == "WAIT"
    assert result[0][1].model_version == "none"


def test_old_qwen_fear_cannot_close_position():
    from titanium.position_sentiment import confirm_fear

    verdict = {"request_ref": "r", "model_version": "qwen3.5:2b", "state": "FEAR",
                   "confidence": .99, "rendered_at": NOW.isoformat()}
    assert not confirm_fear(verdict, last_ref="old", previous_streak=1, now=NOW).should_exit


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -1, 2])
def test_invalid_hermes_confidence_rejected(monkeypatch, confidence):
    from titanium import hermes_cortex as cortex

    monkeypatch.setattr(cortex, "collect", lambda _: [])
    monkeypatch.setattr(cortex, "_ask", lambda _, **_kw: {"verdicts": [
        {"decision_ref": "a", "action": "ALLOW", "confidence": confidence},
    ]})
    with pytest.raises(cortex.HermesCortexUnavailable, match="confiance"):
        cortex.analyse_entries([{"decision_ref": "a", "symbol": "BTCUSD", "side": 1}])


def test_opposite_hermes_allow_is_wait(monkeypatch):
    from titanium import hermes_cortex as cortex

    monkeypatch.setattr(cortex, "collect", lambda _: [])
    monkeypatch.setattr(cortex, "_ask", lambda _, **_kw: {"verdicts": [
        {"decision_ref": ref, "action": "ALLOW", "confidence": .8} for ref in ("a", "b")
    ]})
    results = cortex.analyse_entries([
        {"decision_ref": "a", "symbol": "BTCUSD", "side": 1},
        {"decision_ref": "b", "symbol": "BTCUSD", "side": -1},
    ])
    assert [item["action"] for item in results] == ["WAIT", "WAIT"]


def test_duplicate_reply_rejected(monkeypatch):
    from titanium import hermes_cortex as cortex

    monkeypatch.setattr(cortex, "collect", lambda _: [])
    monkeypatch.setattr(cortex, "_ask", lambda _, **_kw: {"verdicts": [
        {"decision_ref": "a", "action": "ALLOW", "confidence": .8},
        {"decision_ref": "a", "action": "BLOCK", "confidence": .8},
    ]})
    with pytest.raises(cortex.HermesCortexUnavailable, match="decision_ref"):
        cortex.analyse_entries([{"decision_ref": "a", "symbol": "BTCUSD", "side": 1}])


# ── TTL proportionnel a la barre (07/09/2026) ──────────────────────────────

def test_le_ttl_suit_la_duree_de_la_barre():
    """Un TTL fixe affamait le cortex sur les horizons longs.

    Mesure du 07/09 : sur 40 demandes consecutives, ZERO n'atteignait Hermes.
    Le decalage minimal barre -> demande est de 466 s — la boucle balaie ~50
    portables par tour — donc superieur au TTL de 300 s dans 100 % des cas.
    Sur H1 la fenetre valait 5 minutes sur 60 ; sur H4, 5 sur 240.
    """
    cle = "BTCUSD|long|continuation|4p|tf=%s>H4"
    assert policy_ttl_s(cle % "M1") == 60        # plancher herite
    assert policy_ttl_s(cle % "M5") == 120       # plancher herite
    assert policy_ttl_s(cle % "M15") == 225      # 25 % de 900
    assert policy_ttl_s(cle % "H1") == 900       # 25 % de 3 600
    assert policy_ttl_s(cle % "H4") == 3600      # 25 % de 14 400
    assert policy_ttl_s(cle % "D1") == 21600     # 25 % de 86 400


def test_le_ttl_ne_depasse_jamais_la_barre():
    """Une politique ne doit jamais survivre a la barre qui l'a produite."""
    from titanium.organism.cortex import BARRE_MINUTES
    for horizon, minutes in BARRE_MINUTES.items():
        ttl = policy_ttl_s(f"X|long|c|3p|tf={horizon}>H4")
        assert ttl <= minutes * 60, f"{horizon}: TTL {ttl}s > barre {minutes*60}s"


def test_un_horizon_inconnu_retombe_sur_l_ancienne_borne():
    """Le sens sur de l'erreur : un horizon non reconnu ne relache rien."""
    from titanium.organism.cortex import CORTEX_POLICY_TTL_S
    assert policy_ttl_s("X|long|c|3p|tf=Z9>H4") == CORTEX_POLICY_TTL_S


def test_le_plafond_de_construction_suit_l_horizon():
    """Le plafond etait une constante ; il doit borner CHAQUE horizon."""
    identity = build_decision_identity({"symbol": "BTCUSD", "side": 1})
    m1 = "BTCUSD|long|continuation|4p|tf=M1>H1"
    h4 = "BTCUSD|long|continuation|4p|tf=H4>D1"

    # 300 s sur M1 : refuse, la barre ne dure que 60 s.
    with pytest.raises(ValueError, match="TTL cortex hors borne"):
        policy(identity, context_key=m1, ttl_s=300)

    # 3 600 s sur H4 : accepte, c'est un quart de sa barre.
    accepte = policy(identity, context_key=h4, ttl_s=3600)
    assert accepte.context_key == h4


def test_le_defaut_suit_l_horizon_du_contexte():
    """Sans `ttl_s`, la politique prend le TTL de sa propre barre."""
    identity = build_decision_identity({"symbol": "BTCUSD", "side": 1})
    for horizon, attendu in (("M1", 60), ("H1", 900), ("H4", 3600)):
        cle = f"BTCUSD|long|continuation|4p|tf={horizon}>D1"
        p = policy(identity, context_key=cle)
        duree = (datetime.fromisoformat(p.expires_at)
                 - datetime.fromisoformat(p.created_at)).total_seconds()
        assert duree == attendu, f"{horizon}: {duree}s au lieu de {attendu}s"


# ── Identite de decision : un chronometre n'est pas une donnee (07/09/2026) ──

def test_le_temps_de_calcul_ne_change_pas_l_identite():
    """Deux lectures de la MEME barre doivent sceller la MEME decision.

    Mesure du 07/09 : 869 demandes d'avis pour 31 couples (symbole, barre)
    distincts — un facteur 28. `AAVE-USD` sur la barre de 12:00 a ete soumis
    87 fois au cortex. Sur les 108 cles du panel, une seule variait :
    `jepa_latency_ms`, le temps d'inference de Market-JEPA. Elle suffisait a
    faire deriver le decision_ref et a annuler toute deduplication.
    """
    from titanium.organism.contracts import build_decision_identity

    base = {
        "symbol": "AAVE-USD", "side": -1, "bar_time": "2026-09-07T12:00:00+00:00",
        "verdict": "ENTER", "code": "ENTER_CONFLUENCE", "piliers": 4,
        "total_piliers": 4, "famille": "reversal",
        "engine_context": "AAVE-USD|short|reversal|4p|tf=H1>H4",
        "indicateurs": {"ltf_adx": 22.0, "jepa_impulse_r": 1.44},
    }
    lent = {**base, "indicateurs": {**base["indicateurs"], "jepa_latency_ms": 0.925}}
    vite = {**base, "indicateurs": {**base["indicateurs"], "jepa_latency_ms": 0.874}}

    assert (build_decision_identity(lent).decision_ref
            == build_decision_identity(vite).decision_ref)


def test_un_vrai_changement_de_marche_change_bien_l_identite():
    """Le filtre ne doit pas rendre le sceau aveugle a ce qui compte."""
    from titanium.organism.contracts import build_decision_identity

    base = {
        "symbol": "AAVE-USD", "side": -1, "bar_time": "2026-09-07T12:00:00+00:00",
        "engine_context": "AAVE-USD|short|reversal|4p|tf=H1>H4",
        "indicateurs": {"ltf_adx": 22.0, "jepa_impulse_r": 1.44},
    }
    autre_adx = {**base, "indicateurs": {**base["indicateurs"], "ltf_adx": 31.0}}
    autre_jepa = {**base, "indicateurs": {**base["indicateurs"], "jepa_impulse_r": 0.9}}
    autre_barre = {**base, "bar_time": "2026-09-07T13:00:00+00:00"}
    autre_sens = {**base, "side": 1}

    reference = build_decision_identity(base).decision_ref
    for nom, variante in (("ADX", autre_adx), ("impulsion JEPA", autre_jepa),
                          ("barre", autre_barre), ("sens", autre_sens)):
        assert build_decision_identity(variante).decision_ref != reference, nom


def test_seul_le_chronometre_est_filtre():
    """Un filtre trop large effacerait de vraies mesures de marche."""
    from titanium.organism.contracts import MESURES_INSTRUMENTALES

    assert MESURES_INSTRUMENTALES == {"jepa_latency_ms"}
    # Les autres cles jepa_* sont des predictions, pas de l'instrumentation.
    for garde in ("jepa_available", "jepa_impulse_r", "jepa_vol_ratio"):
        assert garde not in MESURES_INSTRUMENTALES
