"""Tests de la famille d'execution adaptative (laboratoire offline).

Ces tests verrouillent les proprietes qui rendent la famille MESURABLE :

1. isolement de l'arene historique (``POLICY_REGISTRY`` inchange) ;
2. echec ferme sur contexte non fiable, jamais d'ordre de repli ;
3. un ordre passif ne traverse jamais le carnet ;
4. une echeance court depuis le depart de la tranche, pas depuis l'arrivee ;
5. une cloture de rattrapage ne depasse jamais le reliquat reel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from titanium.execution_sim.adaptive import (
    ADAPTIVE_POLICIES,
    SEQUENTIAL_ADAPTIVE_POLICIES,
)
from titanium.execution_sim.adaptive_features import build_features
from titanium.execution_sim.config import load_config
from titanium.execution_sim.engine import BacktestExecutionEngine
from titanium.execution_sim.models import (
    BookLevel,
    ExecutionIntent,
    MarketSnapshot,
    OrderType,
    Side,
)
from titanium.execution_sim.policies import POLICY_REGISTRY, PolicyContext, get_policy
from titanium.execution_sim.runner import (
    ALL_POLICIES,
    _policy_config,
    _run_case,
    axes_adaptation,
    executer_sur_snapshots,
    generate_scenarios,
)
from titanium.macro.gate import MACRO_POSTURE_KEY

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot(bid=1.1000, ask=1.1002, *, volume=1_000.0, volatility_bps=8.0, depth=True):
    if depth:
        bid_levels = (BookLevel(bid, 5.0), BookLevel(bid - 0.0001, 8.0))
        ask_levels = (BookLevel(ask, 5.0), BookLevel(ask + 0.0001, 8.0))
    else:
        bid_levels, ask_levels = (), ()
    return MarketSnapshot(
        timestamp=NOW,
        symbol="EURUSD",
        bid=bid,
        ask=ask,
        bid_levels=bid_levels,
        ask_levels=ask_levels,
        open=bid,
        high=ask,
        low=bid,
        close=ask,
        volume=volume,
        volatility_bps=volatility_bps,
        event_id="v1",
    )


def context(**changes):
    values = {"snapshot": snapshot(), "tick_size": 0.0001, "historical_volumes": ()}
    values.update(changes)
    return PolicyContext(**values)


def intent(qty=1.0, **changes):
    values = {"intent_id": "alpha-1", "symbol": "EURUSD", "side": Side.BUY, "quantity": qty}
    values.update(changes)
    return ExecutionIntent(**values)


def plan(name, *, ctx=None, intr=None, config=None):
    section = dict(load_config()["execution"]["adapt"].get(name, {}))
    section.setdefault("maker_bps", 1.0)
    section.setdefault("taker_bps", 3.0)
    section["baseline_spread_bps"] = section.get("baseline_spread_bps", 3.0)
    section.update(config or {})
    return get_policy(name, section).plan(intr or intent(), ctx or context())


def test_seize_techniques_sont_enregistrees_et_selectables():
    assert len(ADAPTIVE_POLICIES) == 17
    assert len(set(ADAPTIVE_POLICIES)) == 17
    for name in ADAPTIVE_POLICIES:
        assert get_policy(name).name == name
    assert {
        "adapt_deadline_ladder",
        "adapt_depth_slice",
        "adapt_size_patience",
        "adapt_ladder_maker_taker",
        "adapt_spread_participation",
    } == set(SEQUENTIAL_ADAPTIVE_POLICIES)


def test_la_famille_adaptative_n_elargit_pas_l_arene_historique():
    """Ajouter des techniques ne doit pas changer la matrice a quinze politiques.

    Un run historique doit rester comparable a lui-meme : si ces noms
    entraient dans ``POLICY_REGISTRY`` ou ``ALL_POLICIES``, toute matrice deja
    publiee changerait de contenu sans changer de nom.
    """
    assert set(POLICY_REGISTRY).isdisjoint(ADAPTIVE_POLICIES)
    assert set(ALL_POLICIES).isdisjoint(ADAPTIVE_POLICIES)
    with pytest.raises(ValueError):
        get_policy("adapt_inconnue")


@pytest.mark.parametrize("name", ADAPTIVE_POLICIES)
def test_chaque_technique_echoue_ferme_sur_contexte_non_fiable(name):
    """Aucun ordre, et surtout pas un ordre de repli, si la decision n'est pas sure."""
    cas = [
        {"snapshot": snapshot(bid=1.1002, ask=1.1000)},  # carnet inverse
        {"tick_size": 0.0},
        {"snapshot": snapshot(bid=float("nan"))},
        {"snapshot": snapshot(bid=0.0)},
    ]
    for changes in cas:
        assert plan(name, ctx=context(**changes)) == []
    assert plan(name, intr=intent(qty=0.0)) == []


@pytest.mark.parametrize("name", ADAPTIVE_POLICIES)
def test_aucun_ordre_passif_ne_traverse_le_carnet(name):
    ctx = context()
    for order in plan(name, ctx=ctx):
        if order.order_type in {OrderType.MARKET, OrderType.IOC, OrderType.FOK}:
            continue
        assert order.limit_price is not None
        if order.side is Side.BUY:
            assert order.limit_price < ctx.snapshot.ask
        else:
            assert order.limit_price > ctx.snapshot.bid


def test_le_sens_vendeur_est_symetrique():
    ctx = context()
    vente = plan("adapt_improve_touch", intr=intent(side=Side.SELL), ctx=ctx)
    assert vente and vente[0].limit_price >= ctx.snapshot.ask
    achat = plan("adapt_improve_touch", intr=intent(side=Side.BUY), ctx=ctx)
    assert achat and achat[0].limit_price <= ctx.snapshot.bid


def test_ameliorer_le_touch_d_un_tick_quand_le_spread_le_permet():
    orders = plan(
        "adapt_improve_touch",
        ctx=context(snapshot=snapshot(bid=99.98, ask=100.23), tick_size=0.01),
        intr=intent(qty=1.0),
        config={"improve_ticks": 2},
    )
    assert orders[0].metadata["decision"] == "amelioration_tick"
    assert orders[0].limit_price == pytest.approx(100.00)
    serre = plan(
        "adapt_improve_touch",
        ctx=context(snapshot=snapshot(bid=1.1000, ask=1.1002), tick_size=0.0001),
        config={"improve_ticks": 2},
    )
    assert serre[0].metadata["decision"] == "jonction_touch"


def test_la_participation_de_spread_echoue_ferme_sans_reference():
    """Remplace ``adapt_spread_expansion`` : meme test d'echec ferme."""
    section = {"maker_bps": 1.0, "taker_bps": 3.0}
    assert get_policy("adapt_spread_participation", section).plan(intent(), context()) == []


def test_la_participation_repartit_quand_le_spread_s_expand():
    """La regle differe par la FORME, pas par un seuil : N tranches, pas un ordre."""
    large = plan(
        "adapt_spread_participation",
        ctx=context(snapshot=snapshot(bid=99.98, ask=100.23), tick_size=0.01),
        intr=intent(qty=8.0),
        config={"baseline_spread_bps": 3.0, "expansion_factor": 2.0, "max_slices": 5},
    )
    assert len(large) > 1
    assert sum(order.quantity for order in large) == pytest.approx(8.0)
    normal = plan(
        "adapt_spread_participation",
        ctx=context(snapshot=snapshot(bid=1.1000, ask=1.1002), tick_size=0.0001),
        intr=intent(qty=8.0),
        config={"baseline_spread_bps": 3.0, "expansion_factor": 2.0},
    )
    assert [order.order_type for order in normal] == [OrderType.MARKET]


def test_l_abandon_volatilite_ne_rend_aucun_ordre_au_dessus_du_seuil():
    orders = plan(
        "adapt_volatility_abort",
        ctx=context(snapshot=snapshot(volatility_bps=60.0)),
        config={"hard_bps": 25.0},
    )
    assert orders == []
    normal = plan(
        "adapt_volatility_abort",
        ctx=context(snapshot=snapshot(volatility_bps=2.0)),
        config={"hard_bps": 25.0},
    )
    assert [o.order_type for o in normal] == [OrderType.MARKET]


def test_l_horizon_declare_prime_sur_la_config_de_l_echeance():
    """L'axe horizon doit etre un vrai axe : la config n'est qu'un repli."""
    court = plan(
        "adapt_deadline_ladder",
        intr=intent(qty=6.0, metadata={"horizon_ms": 9_000}),
        config={"slices": 3, "horizon_ms": 60_000},
    )
    long = plan(
        "adapt_deadline_ladder",
        intr=intent(qty=6.0, metadata={"horizon_ms": 60_000}),
        config={"slices": 3, "horizon_ms": 9_000},
    )
    assert court[1].scheduled_offset_ms == 3_000
    assert long[1].scheduled_offset_ms == 20_000


def test_l_echeance_court_depuis_le_depart_de_la_tranche():
    """Une tranche programmee tard ne doit pas naitre deja expiree."""
    orders = plan("adapt_deadline_ladder", config={"slices": 3, "horizon_ms": 9000})
    premier, deuxieme, cloture = orders
    assert deuxieme.scheduled_offset_ms == 3000
    assert deuxieme.expires_at == NOW + timedelta(milliseconds=6000)
    assert cloture.order_type is OrderType.MARKET
    assert cloture.metadata["catchup"] is True


def test_la_cloture_de_rattrapage_porte_la_quantite_totale():
    """Le rattrapage est borne par le runner au reliquat : il porte donc la taille pleine."""
    orders = plan("adapt_ladder_maker_taker", intr=intent(qty=9.0))
    cloture = orders[-1]
    assert cloture.order_type is OrderType.MARKET
    assert cloture.quantity == pytest.approx(9.0)
    assert cloture.metadata["cancel_previous"] is True


def test_le_selecteur_delegue_et_trace_son_choix():
    orders = plan(
        "adapt_selector",
        ctx=context(snapshot=snapshot(bid=99.98, ask=100.23, volatility_bps=12.0)),
        config={"wide_spread_bps": 8.0, "baseline_spread_bps": 3.0},
    )
    assert orders
    assert orders[0].metadata["selected_technique"].startswith("adapt_")


def test_le_selecteur_bascule_sur_l_abandon_en_volatilite_extreme():
    orders = plan(
        "adapt_selector",
        ctx=context(snapshot=snapshot(volatility_bps=60.0)),
        config={"abort_volatility_bps": 25.0},
    )
    assert orders == []


def test_les_features_sont_deterministes_et_decrivent_le_contexte():
    ctx = context(
        snapshot=snapshot(bid=99.98, ask=100.23, volume=50.0, volatility_bps=12.0),
        inventory=5.0,
    )
    premier = build_features(intent(qty=10.0), ctx, baseline_spread_bps=3.0, max_inventory=10.0)
    deuxieme = build_features(intent(qty=10.0), ctx, baseline_spread_bps=3.0, max_inventory=10.0)
    assert premier == deuxieme
    assert premier.spread_bps == pytest.approx(0.25 / 100.105 * 10_000.0)
    assert premier.depth_ratio == pytest.approx(13.0 / 10.0)
    assert premier.inventory_ratio == pytest.approx(0.5)
    assert premier.urgency_source == "default"


def test_l_urgence_declaree_prime_sur_le_defaut():
    features = build_features(intent(metadata={"urgency": 0.9}), context())
    assert features.urgency == pytest.approx(0.9)
    assert features.urgency_source == "metadata"
    derive = build_features(intent(metadata={"horizon_ms": 6_000}), context())
    assert derive.urgency_source == "horizon_ms"
    assert derive.urgency == pytest.approx(0.9)


def _marbres(n=10, debut=100.0):
    """Snapshots plats et liquides, communs aux DEUX entrees.

    Un seul jeu pour le runner et pour le moteur : sinon, comparer les deux
    entrees comparerait aussi deux marches differents.
    """
    return [
        MarketSnapshot(
            timestamp=NOW + timedelta(seconds=index),
            symbol="SYNTH",
            bid=debut + index * 0.01,
            ask=debut + 0.02 + index * 0.01,
            bid_levels=(BookLevel(debut + index * 0.01, 50.0),),
            ask_levels=(BookLevel(debut + 0.02 + index * 0.01, 50.0),),
            open=debut,
            high=debut + 0.1,
            low=debut - 0.1,
            close=debut,
            volume=500.0,
            volatility_bps=6.0,
            event_id=f"m{index}",
        )
        for index in range(n)
    ]


def test_aucune_entree_ne_remplit_plus_que_la_quantite_voulue():
    """Jamais de sur-remplissage, verifie sur la QUANTITE remplie, pas un ratio.

    ``fill_ratio`` vaut ``min(1, rempli/voulu)`` (``metrics``) : affirmer
    ``fill_ratio * voulu <= voulu`` est donc vrai par construction et ne peut
    detecter AUCUN sur-remplissage. La porte porte ici sur
    ``sum(order.filled_quantity)``, et couvre les deux entrees, le runner et le
    moteur generique.
    """
    config = load_config()
    total = 0.0
    for name in sorted(SEQUENTIAL_ADAPTIVE_POLICIES):
        for voulu in (1.0, 6.0, 9.0):
            par_runner = executer_sur_snapshots(
                name,
                _marbres(),
                intent(qty=voulu),
                config=config,
                seed=1,
                latency_ms=10,
                tick_size=0.01,
            )
            rempli = sum(order.filled_quantity for order in par_runner)
            assert rempli <= voulu + 1e-9, f"{name}: runner {rempli} pour {voulu}"
            par_moteur = BacktestExecutionEngine(policy=name, seed=1).execute(
                intent(qty=voulu), _marbres(), tick_size=0.01
            )
            rempli_moteur = sum(order.filled_quantity for order in par_moteur)
            assert rempli_moteur <= voulu + 1e-9, f"{name}: moteur {rempli_moteur} pour {voulu}"
            total += rempli_moteur
    # Sans cela, la porte passerait aussi sur un executeur qui ne remplit rien.
    assert total > 0


def test_les_deux_entrees_suivent_le_meme_ordonnancement():
    """Une tranche differee ne se remplit pas avant son horaire, des deux cotes.

    Le moteur generique remplissait chaque ordre des le PREMIER evenement : une
    tranche programmee a t + 3 s s'executait a t0, et le meme plan rendait deux
    resultats selon l'entree. Les deux passent desormais par le meme
    proprietaire de l'ordonnancement.
    """
    config = load_config()
    # La fenetre doit couvrir TOUT l'echange : un ordre programme au-dela du
    # dernier evenement est ramene au dernier evenement disponible (borne de
    # ``index_activation``), et la porte comparerait alors un remplissage a une
    # activation hors fenetre. L'echeance par defaut dure 20 s par tranches de
    # 6,7 s : 24 snapshots d'une seconde la contiennent.
    jeux = _marbres(24)
    par_runner = executer_sur_snapshots(
        "adapt_deadline_ladder",
        jeux,
        intent(qty=6.0),
        config=config,
        seed=1,
        latency_ms=0,
        tick_size=0.01,
    )
    par_moteur = BacktestExecutionEngine(
        policy="adapt_deadline_ladder",
        policy_config={"slices": 3, "horizon_ms": 9_000},
        seed=1,
    ).execute(intent(qty=6.0), jeux, tick_size=0.01)
    for entree, ordres in (("runner", par_runner), ("moteur", par_moteur)):
        differes = [order for order in ordres if order.scheduled_offset_ms > 0]
        assert differes, f"{entree}: aucun ordre differe, la porte ne prouverait rien"
        for order in differes:
            activation = jeux[0].timestamp + timedelta(milliseconds=order.scheduled_offset_ms)
            for fill in order.fills:
                assert fill.timestamp >= activation, (
                    f"{entree}: ordre programme a {order.scheduled_offset_ms} ms "
                    f"rempli a {fill.timestamp}, avant son activation {activation}"
                )


def test_le_moteur_generique_ne_sur_remplit_pas_non_plus():
    """Le contrat de remplissage appartient au modele, pas a un seul executeur.

    Mesure du 12/09/2026 : une echelle de trois tranches remplissait 10 unites
    pour une intention de 6 (66 %) via ``BacktestExecutionEngine``, alors que le
    runner la bornait deja. Le defaut etait invisible depuis le seul chemin
    teste jusqu'ici.
    """
    snapshots = [
        MarketSnapshot(
            timestamp=NOW + timedelta(seconds=index),
            symbol="SYNTH",
            bid=100.0 + index * 0.01,
            ask=100.02 + index * 0.01,
            bid_levels=(BookLevel(100.0, 50.0),),
            ask_levels=(BookLevel(100.02 + index * 0.01, 50.0),),
            open=100.0,
            high=100.1,
            low=99.9,
            close=100.0,
            volume=500.0,
            volatility_bps=6.0,
            event_id=f"e{index}",
        )
        for index in range(10)
    ]
    moteur = BacktestExecutionEngine(
        policy="adapt_deadline_ladder",
        policy_config={"slices": 3, "horizon_ms": 9_000},
        seed=1,
    )
    ordres = moteur.execute(intent(qty=6.0), snapshots, tick_size=0.01)
    rempli = sum(order.filled_quantity for order in ordres)
    assert rempli <= 6.0 + 1e-9


def test_les_clotures_de_rattrapage_seulement_au_marche_sont_tracees():
    config = load_config()
    snapshots = [
        MarketSnapshot(
            timestamp=NOW + timedelta(seconds=index),
            symbol="SYNTH",
            bid=100.0 + index * 0.01,
            ask=100.02 + index * 0.01,
            bid_levels=(BookLevel(100.0 + index * 0.01, 50.0),),
            ask_levels=(BookLevel(100.02 + index * 0.01, 50.0),),
            open=100.0,
            high=100.1 + index * 0.01,
            low=99.9 + index * 0.01,
            close=100.0 + index * 0.01,
            volume=200.0,
            volatility_bps=6.0,
            event_id=f"e{index}",
        )
        for index in range(8)
    ]
    executed = executer_sur_snapshots(
        "adapt_deadline_ladder",
        snapshots,
        intent(qty=6.0),
        config=config,
        seed=1,
        latency_ms=10,
        tick_size=0.01,
    )
    rattrapage = [order for order in executed if order.metadata.get("catchup")]
    assert rattrapage
    assert all(order.order_type is OrderType.MARKET for order in rattrapage)
    rempli = sum(order.filled_quantity for order in executed)
    assert rempli <= 6.0 + 1e-9


def test_le_runner_transmet_reference_et_frais_aux_techniques():
    section = _policy_config("adapt_spread_participation", load_config())
    assert section["baseline_spread_bps"] == 3.0
    assert section["maker_bps"] == 1.0
    assert section["taker_bps"] == 3.0


def test_les_axes_d_adaptation_sont_variables_et_reproductibles():
    """Inventaire, urgence et horizon doivent VARIE R, sinon les techniques
    qui en dependent ne peuvent pas s'adapter (defaut mesure avant correctif)."""
    scenarios = generate_scenarios(seed=14_082_026, quick=True)
    for scenario in scenarios:
        assert axes_adaptation(scenario) == axes_adaptation(scenario)
    inventaires = {axes_adaptation(s)["inventory_ratio"] for s in scenarios}
    urgences = {axes_adaptation(s)["urgency"] for s in scenarios}
    horizons = {axes_adaptation(s)["horizon_ms"] for s in scenarios}
    assert len(inventaires) > 1 and len(urgences) > 1 and len(horizons) > 1
    # L'inventaire doit atteindre des paliers qui deplacent le prix de plusieurs
    # ticks, pas d'un arrondi. 0,75 x 10 = 7,5 unites avec un tick de 0,01.
    assert max(abs(v) for v in inventaires) >= 0.5


def test_aucune_paire_de_techniques_n_est_identique_sur_tous_les_scenarios():
    """Porte d'independance : deux noms identiques au bit pres ne font qu'un resultat.

    Avant correctif : ``adapt_inventory_skew`` == ``adapt_join_touch`` et
    ``adapt_spread_budget`` == ``adapt_spread_expansion``, 864/864 scenarios.
    """
    config = load_config()
    scenarios = generate_scenarios(seed=14_082_026, quick=False)
    signatures = {name: {} for name in ADAPTIVE_POLICIES}
    for scenario in scenarios:
        for name in ADAPTIVE_POLICIES:
            row = _run_case(name, scenario, config, 100_000.0)
            signatures[name][scenario.scenario_id] = (
                round(float(row["net_pnl"]), 10),
                round(float(row["fill_ratio"]), 10),
                round(float(row["total_cost_bps"]), 10),
            )
    collisions = []
    for index, gauche in enumerate(ADAPTIVE_POLICIES):
        for droite in ADAPTIVE_POLICIES[index + 1 :]:
            if signatures[gauche] == signatures[droite]:
                collisions.append(f"{gauche} == {droite}")
    assert collisions == []


# ───────────────────── Posture macro consommee par l'execution ───────────────
# Mesure du 14/09 sur le contrat livre : tout etat qui EXECUTE rend
# ``score = 0`` et ``conservative = False`` par construction, et ELEVATED fait
# WAIT. Une posture tiree de ces deux valeurs, ou coupee a l'horizon du veto,
# serait donc identiquement nulle partout ou l'execution a lieu : un lecteur
# mort. La posture passe donc par l'axe que les techniques LISENT : `urgency`.

POSTURE_CLEAR = {"allows_new_risk": True, "conservative": False, "state": "CLEAR"}


def bloc_posture(tension):
    return {**POSTURE_CLEAR, MACRO_POSTURE_KEY: tension}


def decision(orders):
    return orders[0].metadata["decision"] if orders else "aucun_ordre"


def test_la_posture_macro_deplace_la_decision_d_execution():
    """Meme intention, meme carnet : SEULE la posture change, et elle decide."""
    config = {"high_urgency": 0.66, "medium_urgency": 0.33}
    immediat = plan(
        "adapt_urgency_ladder", intr=intent(metadata={"urgency": 0.9}), config=config
    )
    patient = plan(
        "adapt_urgency_ladder",
        intr=intent(metadata={"urgency": 0.9}),
        ctx=context(macro=bloc_posture(1.0)),
        config=config,
    )
    assert decision(immediat) == "urgence_haute"
    assert decision(patient) == "urgence_basse"
    assert immediat[0].order_type != patient[0].order_type
    assert patient[0].metadata["urgency_source"] == f"metadata+{MACRO_POSTURE_KEY}"


def test_la_posture_est_graduee_et_pas_binaire():
    """Trois tensions, trois paliers : la posture dose, elle ne bascule pas."""
    config = {"high_urgency": 0.66, "medium_urgency": 0.33}
    urgence = 0.9
    paliers = []
    for tension in (0.0, 1.0 - 0.5 / urgence, 1.0):
        orders = plan(
            "adapt_urgency_ladder",
            intr=intent(metadata={"urgency": urgence}),
            ctx=context(macro=bloc_posture(tension)),
            config=config,
        )
        paliers.append(decision(orders))
    assert paliers == ["urgence_haute", "urgence_moyenne", "urgence_basse"]


def test_la_posture_ne_rend_jamais_plus_agressif_que_l_intention():
    """La posture ne peut que reduire l'agressivite demandee, jamais l'augmenter."""
    base = build_features(intent(), context())
    assert base is not None
    precedente = base.urgency
    for tension in (0.0, 0.25, 0.5, 0.75, 1.0):
        courante = build_features(intent(), context(macro=bloc_posture(tension)))
        assert courante is not None
        assert courante.urgency <= precedente + 1e-12
        precedente = courante.urgency
    assert precedente < base.urgency


def test_la_posture_neutre_rend_exactement_le_vecteur_d_avant():
    """Sans bloc, avec un bloc neutre, ou sans la cle : le meme vecteur, au bit."""
    sans_bloc = build_features(intent(), context())
    sans_cle = build_features(intent(), context(macro=dict(POSTURE_CLEAR)))
    nulle = build_features(intent(), context(macro=bloc_posture(0.0)))
    assert sans_bloc is not None
    assert sans_bloc == sans_cle == nulle
    assert nulle.urgency_source == sans_bloc.urgency_source


def test_une_posture_illisible_refuse_de_planifier():
    """Une posture qu'on ne sait pas lire n'est PAS une posture neutre."""
    for valeur in ("0.5", 1.5, -0.1, float("nan")):
        assert build_features(intent(), context(macro=bloc_posture(valeur))) is None


def test_le_selecteur_change_de_technique_sous_posture():
    """La SELECTION d'entree est une decision d'execution : elle doit reagir."""
    config = {"high_urgency": 0.6}
    calme = plan("adapt_selector", intr=intent(metadata={"urgency": 0.9}), config=config)
    tendu = plan(
        "adapt_selector",
        intr=intent(metadata={"urgency": 0.9}),
        ctx=context(macro=bloc_posture(1.0)),
        config=config,
    )
    assert calme[0].metadata["selected_technique"] == "adapt_urgency_ladder"
    assert tendu[0].metadata["selected_technique"] == "adapt_midpoint_aggressive"


def test_les_axes_declares_ne_sont_pas_inertes():
    """Chaque technique qui declare un axe doit y REAGIR, verifie en isolation."""
    from tools.execution_adaptative import AXE_DECLARE, _sonde_axes

    config = load_config()
    scenarios = generate_scenarios(seed=14_082_026, quick=False)[:12]
    sonde = _sonde_axes(config, scenarios)
    for name, axe in AXE_DECLARE.items():
        assert sonde[name][axe], f"axe inerte : {name} / {axe}"
