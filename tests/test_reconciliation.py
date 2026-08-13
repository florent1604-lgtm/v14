from __future__ import annotations

from types import SimpleNamespace

from titanium.analysis.reconciliation import aggregate_mt5_deals, reconcile
from titanium.edge import ClosedTrade, PNL_R_MAX


def deal(position, *, entry, magic=0, reason=3, profit=0.0,
         commission=0.0, swap=0.0, fee=0.0, comment="", time=1,
         symbol="EURUSD"):
    return SimpleNamespace(
        position_id=position,
        entry=entry,
        magic=magic,
        reason=reason,
        profit=profit,
        commission=commission,
        swap=swap,
        fee=fee,
        comment=comment,
        time=time,
        time_msc=time * 1000,
        symbol=symbol,
    )


def test_sortie_serveur_magic_zero_reste_attribuee_a_v14() -> None:
    rows = aggregate_mt5_deals([
        deal(42, entry=0, magic=14_000, time=1),
        deal(42, entry=1, magic=0, reason=4, profit=-10, time=2),
    ])
    assert len(rows) == 1
    assert rows[0].position_id == "42"
    assert rows[0].close_reason == "SL"
    assert rows[0].manual_intervention is False


def test_position_legacy_est_acceptee_comme_position_id() -> None:
    entree = deal(43, entry=0, magic=14_000, time=1)
    sortie = deal(43, entry=2, profit=5, time=2)
    entree.position = entree.position_id
    sortie.position = sortie.position_id
    del entree.position_id
    del sortie.position_id

    rows = aggregate_mt5_deals([entree, sortie])

    assert len(rows) == 1
    assert rows[0].position_id == "43"


def test_net_inclut_commission_swap_et_fee() -> None:
    rows = aggregate_mt5_deals([
        deal(7, entry=0, magic=14_000, commission=-1, time=1),
        deal(7, entry=1, profit=12, commission=-1, swap=-2, fee=-0.5, time=2),
    ])
    assert rows[0].net_currency == 7.5


def test_fermeture_manuelle_est_censuree() -> None:
    rows = aggregate_mt5_deals([
        deal(8, entry=0, magic=14_000, time=1),
        deal(8, entry=1, reason=0, profit=2, time=2),
    ])
    assert rows[0].manual_intervention is True
    assert rows[0].censored is True
    assert rows[0].exit_class == "client_exit_unlabeled"
    assert rows[0].strategy_observation is False


def test_titanium_close_est_distingue_du_client_non_etiquete() -> None:
    rows = aggregate_mt5_deals([
        deal(8, entry=0, magic=14_000, time=1),
        deal(8, entry=1, reason=0, profit=2,
             comment="titanium close", time=2),
    ])
    assert rows[0].explicit_titanium_close is True
    assert rows[0].exit_class == "manual_batch_titanium_close"
    assert rows[0].strategy_observation is False


def test_position_non_v14_et_position_encore_ouverte_sont_ignorees() -> None:
    rows = aggregate_mt5_deals([
        deal(1, entry=0, magic=99, time=1),
        deal(1, entry=1, magic=0, time=2),
        deal(2, entry=0, magic=14_000, time=1),
        deal(2, entry=1, magic=0, time=2),
    ], open_position_ids={2})
    assert rows == []


def test_rapport_nomme_manquants_orphelins_et_ecart_pnl() -> None:
    positions = aggregate_mt5_deals([
        deal(10, entry=0, magic=14_000, time=1),
        deal(10, entry=1, profit=10, time=2),
        deal(11, entry=0, magic=14_000, time=1),
        deal(11, entry=1, profit=-5, time=2),
    ])
    journal = [
        ClosedTrade("ctx", 0.5, ticket="live:10", risk_money=10,
                    exact_cost=False),
        ClosedTrade("ctx", 1.0, ticket="live:99", risk_money=10,
                    exact_cost=True),
    ]
    report = reconcile(positions, journal)
    assert report["ok"] is False
    assert report["accounting_ok"] is False
    assert report["edge_ok"] is False
    assert report["missing_in_journal"] == ["11"]
    assert report["orphan_in_journal"] == ["99"]
    assert report["exact_cost_missing"] == ["10"]
    assert report["pnl_mismatches"][0]["position_id"] == "10"


def test_rapport_exact_est_vert() -> None:
    positions = aggregate_mt5_deals([
        deal(10, entry=0, magic=14_000, time=1),
        deal(10, entry=1, profit=10, time=2),
    ])
    journal = [ClosedTrade(
        "ctx", 1.0, ticket="live:10", risk_money=10, exact_cost=True,
    )]
    report = reconcile(positions, journal)
    assert report["ok"] is True
    assert report["accounting_ok"] is True
    assert report["edge_ok"] is True
    assert report["matched"] == 1


def test_rejet_historique_couvre_lecart_sans_polluer_edge() -> None:
    positions = aggregate_mt5_deals([
        deal(10, entry=0, magic=14_000, time=1),
        deal(10, entry=1, profit=-10, time=2),
    ])
    report = reconcile(positions, [], [{
        "ticket": "live:10",
        "reason": "CONTEXTE_ABSENT_CLOTURE_HISTORIQUE",
        "coverage_only": True,
    }])

    assert report["missing_in_journal"] == []
    assert report["missing_in_edge"] == ["10"]
    assert report["recovered_without_context"] == ["10"]
    assert report["accounted"] == 1
    assert report["journal_live"] == 0
    assert report["accounting_ok"] is True
    assert report["edge_ok"] is False
    assert report["ok"] is False


def test_pnl_implausible_est_signale_meme_sans_risque_money() -> None:
    positions = aggregate_mt5_deals([
        deal(10, entry=0, magic=14_000, time=1),
        deal(10, entry=1, profit=10, time=2),
        deal(11, entry=0, magic=14_000, time=1),
        deal(11, entry=1, profit=5, time=2),
    ])
    journal = [
        ClosedTrade("ctx", PNL_R_MAX + 1, ticket="live:10"),
        ClosedTrade("ctx", -0.5, ticket="live:11"),
    ]

    report = reconcile(positions, journal)

    assert report["ok"] is False
    assert report["pnl_implausible"] == [
        {"position_id": "10", "journal_r": PNL_R_MAX + 1,
         "reason": "PNL_R_HORS_BORNES"},
        {"position_id": "11", "journal_r": -0.5,
         "reason": "SIGNE_OPPOSE_MT5"},
    ]


def test_signe_oppose_autour_du_breakeven_ne_cree_pas_de_bruit() -> None:
    positions = aggregate_mt5_deals([
        deal(10, entry=0, magic=14_000, time=1),
        deal(10, entry=1, profit=-0.01, time=2),
    ])
    journal = [ClosedTrade("ctx", 0.02, ticket="live:10")]

    report = reconcile(positions, journal)

    assert report["pnl_implausible"] == []


def test_rapport_separe_cohorte_strategie_et_sorties_client() -> None:
    positions = aggregate_mt5_deals([
        deal(10, entry=0, magic=14_000, time=1),
        deal(10, entry=1, reason=4, profit=-10, time=2),
        deal(11, entry=0, magic=14_000, time=1),
        deal(11, entry=1, reason=0, profit=3, time=2),
        deal(12, entry=0, magic=14_000, time=1),
        deal(12, entry=1, reason=0, profit=2,
             comment="titanium close", time=2),
    ])
    report = reconcile(positions, [])
    assert report["mt5_closed"] == 3
    assert report["mt5_net_currency"] == -5
    assert report["strategy_observations"] == 1
    assert report["strategy_net_currency"] == -10
    assert report["exit_class_counts"] == {
        "client_exit_unlabeled": 1,
        "manual_batch_titanium_close": 1,
        "server_exit": 1,
    }


class TestCoherenceDesObservations:
    """Une sortie MANUELLE n'est pas une observation de la strategie.

    Objection de revue (Claude, 08/08/2026) : `strategy_observation` valait
    `exit_class != "client_exit_unlabeled"`, si bien que les huit positions
    fermees par `tools/fermer_positions.py` — commentaire « titanium
    close » — comptaient comme des observations alors qu'elles portent
    `manual_intervention=True` et `censored=True`.

    Les deux classes sont manuelles ; les traiter differemment n'a pas de
    justification. L'ecart mesure : 36 observations et PF 0.311 en comptant
    les manuelles, 28 et PF 0.279 sans elles. Ce sont 22 % de la cohorte
    dont la SORTIE a ete decidee par un humain, pas par le stop ni la cible.

    Le sens compte : une position fermee a la main n'a jamais eu l'occasion
    d'atteindre son objectif. L'inclure mesure une decision humaine sous
    l'etiquette d'une mesure de strategie.
    """

    def _position(self, **kw):
        from titanium.analysis.reconciliation import Mt5ClosedPosition
        base = dict(
            position_id=1, symbol="EURUSD", opened_at="", closed_at="",
            net_currency=0.0, profit=0.0, commission=0.0, swap=0.0, fee=0.0,
            close_reason="SL", exit_class="server_exit",
            manual_intervention=False, explicit_titanium_close=False,
            strategy_observation=True, censored=False, deal_count=2)
        base.update(kw)
        return Mt5ClosedPosition(**base)

    def test_une_sortie_moteur_est_une_observation(self):
        p = self._position()
        assert p.strategy_observation is True

    def test_toute_intervention_manuelle_sort_de_la_cohorte(self):
        """Le critere doit etre `manual_intervention`, pas la classe.

        Les deux classes manuelles — batch etiquete et sortie client non
        etiquetee — doivent etre traitees identiquement.
        """
        for classe in ("manual_batch_titanium_close", "client_exit_unlabeled"):
            p = self._position(exit_class=classe, manual_intervention=True,
                               censored=True,
                               explicit_titanium_close=(classe.startswith("manual")))
            assert p.manual_intervention is True
            assert p.censored is True
            assert not p.strategy_observation, (
                f"{classe} est manuelle : elle ne peut pas compter comme "
                "observation de la strategie")

    def test_censored_et_observation_sont_exclusifs(self):
        """Une sortie censuree n'a pas atteint sa cible : elle ne mesure
        pas la strategie, elle mesure l'interruption."""
        from titanium.analysis.reconciliation import Mt5ClosedPosition
        import dataclasses
        for champ in dataclasses.fields(Mt5ClosedPosition):
            pass
        p = self._position(manual_intervention=True, censored=True,
                           exit_class="manual_batch_titanium_close",
                           explicit_titanium_close=True)
        assert not (p.censored and p.strategy_observation)
