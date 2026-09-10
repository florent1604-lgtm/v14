"""Garde de fraicheur d'ENTREE du cortex (`request_is_current`).

Regression du 08/09/2026 : la garde comparait l'age des faits au TTL DE
POLITIQUE et ecartait 53 % des demandes reelles alors qu'aucune donnee plus
fraiche n'existait — la barre suivante n'avait pas cloture. Ces tests fixent
les deux bords : ce qui doit passer, et ce qui doit rester refuse.
"""

from datetime import datetime, timedelta

import pytest

from titanium.organism.contracts import DecisionIdentity
from titanium.organism.cortex import (
    build_cortex_policy,
    market_observed_at,
    policy_is_fresh,
    policy_ttl_s,
    request_is_current,
)

CTX_H1 = "HSI.fs|long|reversal|3p|tf=H1>H4"
CTX_M15 = "US500|short|reversal|3p|tf=M15>H4"
BAR = "2026-09-08T05:00:00+00:00"


def _at(minutes: float) -> datetime:
    return datetime.fromisoformat(BAR) + timedelta(minutes=minutes)


@pytest.mark.unit
@pytest.mark.parametrize("minutes", [0.0, 30.0, 59.9, 60.0, 90.0, 119.9])
def test_barre_en_cours_et_barre_cloturee_sont_servies(minutes):
    """H1 : tant qu'aucune barre plus recente n'a cloture, les faits sont bons."""
    assert request_is_current(BAR, CTX_H1, _at(minutes).isoformat(), now=_at(minutes))


@pytest.mark.unit
@pytest.mark.parametrize("minutes", [120.0, 121.0, 360.0])
def test_barre_depassee_est_refusee(minutes):
    """Des qu'une barre plus recente a cloture, les faits sont reellement vieux."""
    assert not request_is_current(BAR, CTX_H1, _at(minutes).isoformat(), now=_at(minutes))


@pytest.mark.unit
def test_l_ancienne_garde_ecartait_ce_que_la_nouvelle_sert():
    """Le cas exact qui affamait le cortex.

    Barre H1 ouverte a 05:00, cloturee a 06:00, ancien TTL de politique 1800 s. Une
    demande nee a 06:40 porte encore sur la derniere barre cloturee — aucune
    barre plus recente n'existe avant 07:00 — mais l'ancienne garde la jugeait
    trop vieille de 2400 s.
    """
    demande = _at(100.0)
    observed = market_observed_at(BAR, CTX_H1, demande.isoformat())
    ancienne = demande <= observed + timedelta(seconds=1800)
    assert ancienne is False  # l'ancienne garde refusait
    assert request_is_current(BAR, CTX_H1, demande.isoformat(), now=demande)


@pytest.mark.unit
def test_m15_suit_sa_propre_barre():
    assert request_is_current(BAR, CTX_M15, _at(20).isoformat(), now=_at(20))
    assert not request_is_current(BAR, CTX_M15, _at(31).isoformat(), now=_at(31))


@pytest.mark.unit
def test_reponse_m15_rendue_en_96_secondes_reste_utilisable_avant_barre_suivante():
    """Regression live: une demande tardive ne doit pas expirer pendant l'inference."""
    request_at = _at(28.0)
    response_at = request_at + timedelta(seconds=96)
    identity = DecisionIdentity(
        decision_ref="d" * 64,
        context_digest="c" * 64,
        symbol="US500",
        side=-1,
        bar_time=BAR,
        model_version="qwen3.5:2b",
        prompt_version="fundamental-gate-v4-qwen35",
    )
    observed = market_observed_at(BAR, CTX_M15, request_at.isoformat())
    policy = build_cortex_policy(
        identity,
        context_key=CTX_M15,
        action="ALLOW",
        confidence=0.8,
        summary="decision fraiche avant la cloture suivante",
        evidence_digest="e" * 64,
        producer="hermes-cortex/qwen3.5:2b",
        source_observed_at=observed.isoformat(),
        decision_model_version="hermes:qwen3.5:2b",
        now=response_at,
    )

    assert policy_is_fresh(policy, now=response_at)
    assert datetime.fromisoformat(policy.expires_at) == _at(30.0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bar_time, context, requested",
    [
        (BAR, "HSI.fs|long|reversal|3p", _at(5).isoformat()),      # pas de |tf=
        (BAR, "HSI.fs|long|reversal|3p|tf=X9>H4", _at(5).isoformat()),  # horizon inconnu
        (BAR, CTX_H1, "pas-une-date"),                             # date illisible
        (BAR, CTX_H1, "2026-09-08T05:05:00"),                      # sans fuseau
        (_at(30).isoformat(), CTX_H1, BAR),                        # barre future
    ],
)
def test_contrat_casse_reste_fail_closed(bar_time, context, requested):
    assert not request_is_current(bar_time, context, requested, now=_at(30))


@pytest.mark.unit
def test_demande_antidatee_est_refusee():
    """Une demande estampillee dans le futur ne doit jamais ouvrir la garde."""
    assert not request_is_current(BAR, CTX_H1, _at(59).isoformat(), now=_at(10))


@pytest.mark.unit
def test_elargir_l_entree_ne_prolonge_aucune_politique():
    """L'invariant de surete vit dans la sortie, et il est intact.

    Une demande servie tardivement produit une politique qui expire toujours
    avant la barre suivante : `expires_at <= source_observed_at + ttl`.
    """
    demande = _at(110.0)  # servie par la nouvelle garde, refusee par l'ancienne
    assert request_is_current(BAR, CTX_H1, demande.isoformat(), now=demande)
    identity = DecisionIdentity(
        decision_ref="d" * 64,
        context_digest="c" * 64,
        symbol="HSI.fs",
        side=1,
        bar_time=BAR,
        model_version="qwen3.5:2b",
        prompt_version="fundamental-gate-v4-qwen35",
    )
    observed = market_observed_at(BAR, CTX_H1, demande.isoformat())
    policy = build_cortex_policy(
        identity,
        context_key=CTX_H1,
        action="WAIT",
        confidence=0.4,
        summary="regression garde d'entree",
        evidence_digest="e" * 64,
        producer="hermes-cortex",
        source_observed_at=observed.isoformat(),
        ttl_s=policy_ttl_s(CTX_H1),
        now=demande,
    )
    expires = datetime.fromisoformat(policy.expires_at)
    ttl = policy_ttl_s(CTX_H1)
    # L'invariant reel, inchange : l'expiration est calee sur la CLOTURE des
    # faits, jamais sur l'heure de reponse. Une reponse tardive ne rajeunit
    # rien — c'est `observed`, pas `created`, qui borne.
    assert expires <= observed + timedelta(seconds=ttl)
    assert expires < demande + timedelta(seconds=ttl)
    # Le TTL atteint au plus la cloture suivante : une nouvelle barre rendra
    # ensuite les faits precedents caducs.
    assert expires <= observed + timedelta(minutes=60)
