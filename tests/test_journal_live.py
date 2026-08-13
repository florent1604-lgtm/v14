"""Tests du flux retour — la journalisation des trades clos en live.

POURQUOI CE FICHIER EXISTE
---------------------------
C'était la rupture principale de V14 : `results/trades.ndjson` n'avait qu'un
seul écrivain, `tools/backtest.py`. La boucle live détectait la clôture d'une
position, purgeait le ticket… et n'écrivait rien.

Conséquence en chaîne : registre d'edge vide → `edge_ok` toujours inconnu →
**le mode PROD ne pouvait jamais s'ouvrir**, quel que soit le nombre de trades.

Deux exigences dominent ces tests :

1. **Le contexte doit survivre jusqu'à la clôture.** C'est le seul instant où
   résultat et contexte d'entrée coexistent. Ne pas le capturer à l'ouverture
   rend la mesure impossible pour toujours.
2. **Journaliser ne casse jamais le trading.** Disque plein, historique
   illisible, état corrompu : la boucle continue.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from titanium.analysis.discriminants import depuis_journal
from titanium.edge import TradeJournal
from titanium.execution.mt5_executor import ExecutionPolicy
from titanium.execution.pending_context import limit_lifecycle_summary
from titanium.execution.position_manager import (
    PHASE_BREAKEVEN,
    PHASE_INIT,
    PHASE_TRAILING,
    ManageParams,
    PositionSnapshot,
    TrackedState,
    decide_new_sl,
    journaliser_cloture,
    load_state,
    manage_once,
    save_state,
)

P = ManageParams(breakeven_r=0.8, trail_start_r=1.2, trail_dist_r=0.8)


def etat(**over) -> TrackedState:
    """Position long EURUSD, R = 0.0100, contexte complet."""
    base = dict(r=0.0100, phase=PHASE_INIT, peak_fav_r=0.0, symbol="EURUSD",
                side=1, entry=1.1000, sl_initial=1.0900, tp_initial=1.1200,
                context_key="EURUSD|long|continuation|3p",
                indicators={"ltf_rsi_14": 55.2}, ts_open="2026-08-07T04:00:00+00:00",
                mae_r=0.0, timeframe="H1", risque_devise=100.0)
    base.update(over)
    return TrackedState(**base)


# ════════════ le contexte survit jusqu'à la clôture ═════════════════════════

def test_letat_retient_le_contexte_dentree():
    """Sans ces champs, un trade clos n'est rattachable à aucun contexte."""
    st = etat()
    for champ in ("entry", "sl_initial", "tp_initial", "context_key",
                  "indicators", "ts_open"):
        assert hasattr(st, champ), f"{champ} manquant : la mesure serait aveugle"


def test_aller_retour_disque_preserve_le_contexte(tmp_path):
    f = tmp_path / "s.json"
    save_state(f, {"42": etat(peak_fav_r=1.4, mae_r=-0.3)})
    relu = load_state(f)["42"]
    assert relu.entry == 1.1000
    assert relu.context_key == "EURUSD|long|continuation|3p"
    assert relu.indicators == {"ltf_rsi_14": 55.2}
    assert relu.peak_fav_r == 1.4
    assert relu.mae_r == -0.3
    assert relu.timeframe == "H1"


def test_etat_ancien_se_relit_sans_planter(tmp_path):
    """Un fichier écrit AVANT l'ajout du contexte doit rester lisible : le
    gestionnaire doit survivre à une mise à jour du code alors que des
    positions sont déjà ouvertes."""
    f = tmp_path / "s.json"
    f.write_text(json.dumps({
        "7": {"r": 0.01, "phase": "init", "peak_fav_r": 0.5,
              "symbol": "EURUSD", "side": 1}}), encoding="utf-8")
    st = load_state(f)["7"]
    assert st.r == 0.01
    assert st.entry == 0.0          # champ absent → valeur neutre, pas d'erreur
    assert st.context_key == ""
    assert st.indicators == {}
    assert st.timeframe == ""


def test_la_mae_se_suit_au_fil_de_leau():
    """À la clôture MT5 ne montre plus rien : la pire excursion doit avoir été
    mesurée pendant la vie de la position."""
    st = etat()
    pos = PositionSnapshot(ticket="1", symbol="EURUSD", side=1, entry=1.1000,
                           current=1.0960, sl=1.0900, tp=1.1200)
    decide_new_sl(pos, st, P)
    assert st.mae_r == pytest.approx(-0.4)
    # Le prix remonte : la MAE ne doit PAS s'effacer.
    decide_new_sl(PositionSnapshot(ticket="1", symbol="EURUSD", side=1,
                                   entry=1.1000, current=1.1050,
                                   sl=1.0900, tp=1.1200), st, P)
    assert st.mae_r == pytest.approx(-0.4)


# ════════════════════ écriture du journal ═══════════════════════════════════

def test_journalise_un_gagnant(tmp_path):
    j = tmp_path / "trades.ndjson"
    ok = journaliser_cloture(etat(peak_fav_r=2.1, phase=PHASE_TRAILING), "555",
                             prix_sortie=1.1180, ts_exit="2026-08-07T05:00:00+00:00",
                             journal_path=j, net_devise=180.0)
    assert ok
    trades = TradeJournal(j).read_all()
    assert len(trades) == 1
    assert trades[0].pnl_r == pytest.approx(1.8, abs=0.01)
    assert trades[0].context == "EURUSD|long|continuation|3p"
    assert trades[0].timeframe == "H1"
    assert trades[0].ticket == "live:555"


def test_journalise_un_perdant(tmp_path):
    j = tmp_path / "trades.ndjson"
    journaliser_cloture(etat(mae_r=-1.05), "556", prix_sortie=1.0900,
                        ts_exit="", journal_path=j, net_devise=-100.0)
    assert TradeJournal(j).read_all()[0].pnl_r == pytest.approx(-1.0, abs=0.01)


def test_cloture_limite_complete_le_cycle_avec_pnl_net(tmp_path):
    j = tmp_path / "trades.ndjson"
    st = etat(
        limit_order_ticket=555,
        limit_planned_price=1.1000,
        limit_market_reference_price=1.1002,
        limit_target_saving_r=0.02,
        limit_realized_saving_r=0.03,
        limit_slippage_r=-0.01,
    )
    assert journaliser_cloture(
        st, "999", prix_sortie=1.1150,
        ts_exit="2026-08-07T05:00:00+00:00",
        journal_path=j, net_devise=150.0,
    )
    summary = limit_lifecycle_summary(tmp_path / "limit_lifecycle.ndjson")
    assert summary["closed"] == 1
    assert summary["net_pnl_r"] == pytest.approx(1.5)


def test_le_journal_est_relu_par_le_registre_dedge(tmp_path):
    """Le format doit être exactement celui qu'attend `titanium.edge` — sinon
    la chaîne reste rompue plus loin."""
    j = tmp_path / "trades.ndjson"
    for i in range(3):
        journaliser_cloture(etat(peak_fav_r=1.5), str(600 + i),
                            prix_sortie=1.1150, ts_exit="", journal_path=j,
                            net_devise=150.0)
    from titanium.edge import EdgeBook
    livre = EdgeBook(journal=TradeJournal(j))
    livre.refresh()
    assert "EURUSD|long|continuation|3p" in livre._cache


def test_short_calcule_dans_le_bon_sens(tmp_path):
    j = tmp_path / "trades.ndjson"
    journaliser_cloture(etat(side=-1, sl_initial=1.1100, tp_initial=1.0800),
                        "557", prix_sortie=1.0900, ts_exit="", journal_path=j,
                        net_devise=100.0)
    assert TradeJournal(j).read_all()[0].pnl_r == pytest.approx(1.0, abs=0.01)


# ════════════════════════ idempotence ═══════════════════════════════════════

def test_le_meme_ticket_nest_journalise_quune_fois(tmp_path):
    """Une relance du gestionnaire relit un état où le ticket a disparu de MT5.
    Sans garde-fou, chaque redémarrage dupliquerait la ligne."""
    j = tmp_path / "trades.ndjson"
    st = etat(peak_fav_r=1.5)
    assert journaliser_cloture(st, "999", prix_sortie=1.1150, ts_exit="",
                               journal_path=j, net_devise=150.0)
    assert not journaliser_cloture(st, "999", prix_sortie=1.1150, ts_exit="",
                                   journal_path=j, net_devise=150.0)
    assert len(TradeJournal(j).read_all()) == 1


def test_tickets_differents_journalises_separement(tmp_path):
    j = tmp_path / "trades.ndjson"
    journaliser_cloture(etat(), "1", prix_sortie=1.1100, ts_exit="",
                        journal_path=j, net_devise=100.0)
    journaliser_cloture(etat(), "2", prix_sortie=1.1100, ts_exit="",
                        journal_path=j, net_devise=100.0)
    assert len(TradeJournal(j).read_all()) == 2


# ═══════════════════ le giveback, piège hérité de V12 ═══════════════════════

def test_giveback_nul_si_le_trade_na_jamais_ete_en_gain(tmp_path):
    """Compter un giveback sans gain préalable double-compte la MAE — défaut
    relevé par un test dans V12."""
    j = tmp_path / "trades.ndjson"
    journaliser_cloture(etat(peak_fav_r=0.0, mae_r=-1.1), "700",
                        prix_sortie=1.0900, ts_exit="", journal_path=j,
                        net_devise=-110.0)
    d = json.loads((tmp_path / "excursions.ndjson").read_text(encoding="utf-8").strip())
    assert d["giveback_r"] == 0.0


def test_giveback_compte_le_gain_rendu(tmp_path):
    j = tmp_path / "trades.ndjson"
    journaliser_cloture(etat(peak_fav_r=2.0, phase=PHASE_TRAILING), "701",
                        prix_sortie=1.1120, ts_exit="", journal_path=j,
                        net_devise=120.0)
    d = json.loads((tmp_path / "excursions.ndjson").read_text(encoding="utf-8").strip())
    assert d["giveback_r"] == pytest.approx(0.8, abs=0.01)


def test_le_detail_porte_la_censure():
    """Une sortie au stop tronque la MFE. Sans ce drapeau, toute statistique
    future de MFE est biaisée à la baisse, définitivement."""
    import inspect

    from titanium.execution import position_manager
    src = inspect.getsource(position_manager.journaliser_cloture)
    assert "censored" in src


def test_le_detail_porte_les_indicateurs(tmp_path):
    j = tmp_path / "trades.ndjson"
    journaliser_cloture(etat(indicators={"ltf_rsi_14": 62.5, "htf_adx": 31.0}),
                        "702", prix_sortie=1.1150, ts_exit="", journal_path=j,
                        net_devise=150.0)
    d = json.loads((tmp_path / "excursions.ndjson").read_text(encoding="utf-8").strip())
    assert d["indicators"]["ltf_rsi_14"] == 62.5
    assert d["source"] == "live"


# ═════════════ journaliser ne casse jamais le trading ═══════════════════════

def test_r_invalide_ne_journalise_pas(tmp_path):
    j = tmp_path / "trades.ndjson"
    assert not journaliser_cloture(etat(r=0.0), "1", prix_sortie=1.11,
                                   ts_exit="", journal_path=j)
    assert not j.exists()


def test_chemin_impossible_ne_leve_pas(tmp_path):
    """Un disque inaccessible ne doit pas remonter vers la boucle."""
    interdit = tmp_path / "fichier.txt"
    interdit.write_text("x", encoding="utf-8")
    assert journaliser_cloture(etat(), "1", prix_sortie=1.11, ts_exit="",
                               journal_path=interdit / "sous" / "t.ndjson",
                               net_devise=100.0) is False


def test_sans_net_comptable_on_refuse_lexcursion(tmp_path):
    """Une excursion n'est pas un PnL net et ne doit pas promouvoir PROD."""
    j = tmp_path / "trades.ndjson"
    diagnostic = {}
    assert not journaliser_cloture(
        etat(peak_fav_r=1.7, phase=PHASE_TRAILING), "800",
        prix_sortie=None, ts_exit="", journal_path=j, diagnostic=diagnostic)
    assert diagnostic == {"reason": "PNL_NET_INCONNU", "permanent": True}
    assert TradeJournal(j).read_all() == []


# ═══════════════ intégration dans le passage de gestion ═════════════════════

class FakePos:
    def __init__(self, ticket=1, magic=14_000):
        self.ticket = ticket
        self.magic = magic
        self.comment = "titanium-v14"
        self.symbol = "EURUSD"
        self.type = 0
        self.price_open = 1.1000
        self.price_current = 1.1100
        self.sl = 1.0900
        self.tp = 1.1200
        self.volume = 1.0


class FakeMt5:
    TRADE_ACTION_SLTP = 2
    TRADE_RETCODE_DONE = 10009
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, positions=()):
        self._positions = positions
        self.envois = []

    def positions_get(self):
        return self._positions

    def symbol_info(self, s):
        class I:
            point = 1e-5
            digits = 5
            trade_stops_level = 10
        return I()

    def symbol_info_tick(self, s):
        class T:
            bid = 1.1099
            ask = 1.1101
        return T()

    def order_send(self, req):
        self.envois.append(req)

        class R:
            retcode = 10009
        return R()

    def order_calc_profit(self, order_type, symbol, volume, entry, stop):
        return -100.0

    def history_deals_get(self, *a, **k):
        class D:
            time = 1_770_000_000
            price = 1.1150
            position_id = 1
            symbol = "EURUSD"
            entry = 1
            profit = 0.0
        return [D()]


ARMEE = ExecutionPolicy(enabled=True, expected_demo_login=50061786, magic=14_000)


def compte_demo():
    from titanium.data.mt5_vendor import AccountSnapshot
    return AccountSnapshot(login=50061786, server="Axi-US50-Demo", currency="USD",
                           balance=100.0, equity=100.0, margin_free=90.0,
                           is_demo=True, trade_mode=0)


def test_le_contexte_est_capture_a_la_premiere_observation(tmp_path):
    f = tmp_path / "s.json"
    manage_once(FakeMt5(positions=(FakePos(),)), policy=ARMEE, params=P,
                state_path=f, account=compte_demo())
    st = load_state(f)["1"]
    assert st.entry == pytest.approx(1.1000)
    assert st.sl_initial == pytest.approx(1.0900)
    assert st.tp_initial == pytest.approx(1.1200)
    assert st.ts_open, "l'horodatage d'ouverture doit être posé"
    assert st.context_key == "", "une perception passée ne doit pas être inventée"
    assert st.mode == "unknown"
    assert st.risque_devise == pytest.approx(100.0)


def test_position_adoptee_est_quarantaine_sans_polluer_edge(tmp_path):
    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    manage_once(FakeMt5(positions=(FakePos(),)), policy=ARMEE, params=P,
                state_path=f, account=compte_demo(), journal_path=j)

    rapport = manage_once(FakeMt5(positions=()), policy=ARMEE, params=P,
                          state_path=f, account=compte_demo(), journal_path=j)

    assert rapport["journalises"] == 0
    assert rapport["journal_rejected"] == 1
    assert TradeJournal(j).read_all() == []
    rejet = json.loads((tmp_path / "journal_rejets.ndjson").read_text(
        encoding="utf-8").strip())
    assert rejet["reason"] == "CONTEXTE_INCONNU"


def test_la_cloture_produit_une_ligne_de_journal(tmp_path):
    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    # Le contexte a été attaché à l'envoi de l'ordre.
    save_state(f, {"1": etat()})
    r = manage_once(FakeMt5(positions=()), policy=ARMEE, params=P,
                    state_path=f, account=compte_demo(), journal_path=j)
    assert r.get("journalises") == 1
    assert len(depuis_journal(j)) >= 0        # format lisible
    assert len(TradeJournal(j).read_all()) == 1
    assert "1" not in load_state(f), "le ticket doit être purgé de l'état"


def test_cloture_complete_marque_le_net_comptable_exact(tmp_path):
    class Mt5Complet(FakeMt5):
        def history_deals_get(self, *a, **k):
            base = {
                "position_id": 1, "symbol": "EURUSD", "volume": 0.1,
                "commission": 0.0, "swap": 0.0, "fee": 0.0,
            }
            return [
                type("D", (), {
                    **base, "time": 1_770_000_000, "price": 1.1000,
                    "entry": 0, "profit": 0.0,
                })(),
                type("D", (), {
                    **base, "time": 1_770_000_100, "price": 1.1150,
                    "entry": 1, "profit": 100.0,
                })(),
            ]

    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    save_state(f, {"1": etat()})
    rapport = manage_once(
        Mt5Complet(positions=()), policy=ARMEE, params=P,
        state_path=f, account=compte_demo(), journal_path=j,
    )
    assert rapport["journalises"] == 1
    trade = TradeJournal(j).read_all()[0]
    assert trade.exact_net is True
    assert trade.exact_cost is False


def test_cloture_symbole_a_suffixe_courtier_est_journalisee(tmp_path):
    """NAS100 logique et NAS100.fs courtier designent le meme instrument."""
    class Mt5Suffixe(FakeMt5):
        def history_deals_get(self, *a, **k):
            base = {
                "position_id": 1, "symbol": "NAS100.fs", "volume": 0.1,
                "swap": 0.0, "fee": 0.0,
            }
            return [
                type("D", (), {
                    **base, "time": 1_799_999_900, "price": 20_000.0,
                    "entry": 0, "profit": 0.0, "commission": 0.0,
                })(),
                type("D", (), {
                    **base, "time": 1_800_000_000, "price": 20_100.0,
                    "entry": 1, "profit": 110.0, "commission": -10.0,
                })(),
            ]

    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    save_state(f, {"1": etat(
        symbol="NAS100", entry=20_000.0, r=100.0,
        risque_devise=100.0, spread_r=0.2, spread_exact=True,
    )})

    rapport = manage_once(
        Mt5Suffixe(positions=()), policy=ARMEE, params=P,
        state_path=f, account=compte_demo(), journal_path=j,
    )

    assert rapport.get("journalises") == 1
    trade_clos = TradeJournal(j).read_all()[0]
    assert trade_clos.pnl_r == pytest.approx(1.0)
    assert trade_clos.cost_r == pytest.approx(0.3)  # spread 0.2 + frais 0.1
    assert trade_clos.exact_cost is True


def test_suffixe_ambigu_ne_fusionne_pas_deux_contrats():
    from titanium.execution.position_manager import _symboles_mt5_equivalents

    assert _symboles_mt5_equivalents("XAUUSD", "XAUUSD.pro")
    assert not _symboles_mt5_equivalents("US500", "US500M")
    assert not _symboles_mt5_equivalents("US500", "US500+")


def test_mode_desarme_observe_et_journalise_sans_ordre(tmp_path):
    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    desarmee = ExecutionPolicy(
        enabled=False, expected_demo_login=50061786, magic=14_000,
    )
    ouverte = FakeMt5(positions=(FakePos(),))
    premier = manage_once(
        ouverte, policy=desarmee, params=P, state_path=f,
        account=compte_demo(), journal_path=j, manage_stops=False,
    )
    assert premier["managed"] == 1
    assert premier["moved"] == 0
    assert ouverte.envois == []
    assert "1" in load_state(f)

    # Dans le vrai flux, `_attacher_contexte` complète l'état dès l'envoi.
    save_state(f, {"1": etat()})

    fermee = FakeMt5(positions=())
    second = manage_once(
        fermee, policy=desarmee, params=P, state_path=f,
        account=compte_demo(), journal_path=j, manage_stops=False,
    )
    assert second.get("journalises") == 1
    assert fermee.envois == []
    assert len(TradeJournal(j).read_all()) == 1


def test_observation_refuse_un_compte_reel_sans_purger_etat(tmp_path):
    from titanium.data.mt5_vendor import AccountSnapshot

    f = tmp_path / "s.json"
    save_state(f, {"1": etat()})
    reel = AccountSnapshot(
        login=60261188, server="Axi-US52-Live", currency="EUR",
        balance=20.0, equity=20.0, margin_free=15.0,
        is_demo=False, trade_mode=2,
    )
    r = manage_once(
        FakeMt5(positions=()), policy=ExecutionPolicy(), params=P,
        state_path=f, account=reel, manage_stops=False,
    )
    assert r["reason"] == "OBSERVE_NOT_DEMO"
    assert "1" in load_state(f)


def test_une_seconde_purge_ne_duplique_pas(tmp_path):
    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    save_state(f, {"1": etat()})
    manage_once(FakeMt5(positions=()), policy=ARMEE, params=P,
                state_path=f, account=compte_demo(), journal_path=j)
    # on réinjecte le même état, comme après un redémarrage mal séquencé
    save_state(f, {"1": etat()})
    manage_once(FakeMt5(positions=()), policy=ARMEE, params=P,
                state_path=f, account=compte_demo(), journal_path=j)
    assert len(TradeJournal(j).read_all()) == 1


def test_echec_journal_conserve_etat_et_signale_ecart(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    save_state(f, {"1": etat()})
    monkeypatch.setattr(
        "titanium.execution.position_manager.journaliser_cloture",
        lambda *args, **kwargs: False,
    )
    r = manage_once(FakeMt5(positions=()), policy=ARMEE, params=P,
                    state_path=f, account=compte_demo(), journal_path=j)
    assert r["reason"] == "JOURNAL_GAP"
    assert r["journal_failures"] == 1
    assert "1" in load_state(f), "le contexte doit rester disponible pour réessai"


def test_disparition_sans_deal_sortie_conserve_etat(tmp_path):
    class Mt5EntreeSeule(FakeMt5):
        def history_deals_get(self, *args, **kwargs):
            return [type("D", (), {
                "time": 1_800_000_000,
                "price": 1.10,
                "position_id": 1,
                "symbol": "EURUSD",
                "entry": 0,
                "profit": 0.0,
                "commission": -1.0,
                "swap": 0.0,
                "fee": 0.0,
                "magic": 14_000,
                "comment": "titanium-v14",
            })()]

    state = tmp_path / "positions.json"
    journal = tmp_path / "trades.ndjson"
    save_state(state, {"1": etat()})

    report = manage_once(
        Mt5EntreeSeule(positions=()),
        policy=ARMEE,
        params=P,
        state_path=state,
        account=compte_demo(),
        journal_path=journal,
    )

    assert report["reason"] == "JOURNAL_GAP"
    assert report["journal_failures"] == 1
    tracked = load_state(state)["1"]
    assert tracked.history_missing_attempts == 1
    assert tracked.history_missing_since
    assert TradeJournal(journal).read_all() == []


def test_sortie_introuvable_ne_s_escalade_pas_sur_le_nombre_de_tours(tmp_path):
    class Mt5EntreeSeule(FakeMt5):
        def history_deals_get(self, *args, **kwargs):
            return [type("D", (), {
                "time": 1_800_000_000,
                "price": 1.10,
                "position_id": 1,
                "symbol": "EURUSD",
                "entry": 0,
                "profit": 0.0,
                "commission": -1.0,
                "swap": 0.0,
                "fee": 0.0,
                "magic": 14_000,
                "comment": "titanium-v14",
            })()]

    state = tmp_path / "positions.json"
    journal = tmp_path / "trades.ndjson"
    save_state(state, {"1": etat(
        history_missing_since=datetime.now(timezone.utc).isoformat(),
        history_missing_attempts=14,
    )})

    report = manage_once(
        Mt5EntreeSeule(positions=()),
        policy=ARMEE,
        params=P,
        state_path=state,
        account=compte_demo(),
        journal_path=journal,
    )

    tracked = load_state(state)["1"]
    assert tracked.history_missing_attempts == 15
    assert report["history_missing"] == 1
    assert not (tmp_path / "journal_rejets.ndjson").exists()
    assert TradeJournal(journal).read_all() == []


def test_sortie_introuvable_escalade_sur_age_et_conserve_snapshot_complet(tmp_path):
    class Mt5EntreeSeule(FakeMt5):
        def history_deals_get(self, *args, **kwargs):
            return [type("D", (), {
                "time": 1_800_000_000,
                "price": 1.10,
                "position_id": 1,
                "symbol": "EURUSD",
                "entry": 0,
                "profit": 0.0,
                "commission": -1.0,
                "swap": 0.0,
                "fee": 0.0,
                "magic": 14_000,
                "comment": "titanium-v14",
            })()]

    state = tmp_path / "positions.json"
    journal = tmp_path / "trades.ndjson"
    missing_since = datetime.now(timezone.utc).timestamp() - 16 * 60
    original = etat(
        history_missing_since=datetime.fromtimestamp(
            missing_since, timezone.utc,
        ).isoformat(),
        history_missing_attempts=1,
    )
    save_state(state, {"1": original})

    report = manage_once(
        Mt5EntreeSeule(positions=()),
        policy=ARMEE,
        params=P,
        state_path=state,
        account=compte_demo(),
        journal_path=journal,
    )

    assert "1" not in load_state(state)
    assert report["journal_rejected"] == 1
    rejected = json.loads(
        (tmp_path / "journal_rejets.ndjson").read_text(encoding="utf-8")
    )
    assert rejected["reason"].startswith("SORTIE_INTROUVABLE_APRES_")
    assert rejected["history_missing_attempts"] == 2
    assert rejected["history_missing_age_seconds"] >= 15 * 60
    assert rejected["quarantine_recoverable"] is True
    assert rejected["tracked_state"] == original.to_dict() | {
        "history_missing_attempts": 2,
    }


def test_rejet_definitif_est_quarantine_sans_interblocage(tmp_path):
    f = tmp_path / "s.json"
    j = tmp_path / "trades.ndjson"
    impossible = etat(r=1e-9, entry=1.10, risque_devise=25.0)
    save_state(f, {"1": impossible})
    r = manage_once(FakeMt5(positions=()), policy=ARMEE, params=P,
                    state_path=f, account=compte_demo(), journal_path=j)
    assert r.get("journal_rejected") == 1
    assert r["reason"] == ""
    assert "1" not in load_state(f)
    rejet = tmp_path / "journal_rejets.ndjson"
    assert rejet.exists()
    contenu = rejet.read_text(encoding="utf-8")
    assert '"reason": "R_HORS_BORNES"' in contenu
    assert '"ticket": "live:1"' in contenu


def test_journal_par_defaut_a_cote_de_letat(tmp_path):
    """Sans `journal_path`, le journal se place à côté du fichier d'état."""
    f = tmp_path / "s.json"
    save_state(f, {"1": etat()})
    manage_once(FakeMt5(positions=()), policy=ARMEE, params=P,
                state_path=f, account=compte_demo())
    assert (tmp_path / "trades.ndjson").exists()


# ── Frais réels : commission et swap ────────────────────────────────────
#
# MT5 rend ces frais en devise, sur CHAQUE deal de la position. Les ignorer
# gonfle l'espérance journalisée — le chiffre même qui autorise le passage
# en argent réel. Défaut relevé en revue de conception, corrigé ici.

class TestFraisReels:
    def _deal(self, **kw):
        d = dict(time=1_800_000_000, price=1.11, commission=0.0, swap=0.0,
                 fee=0.0, position_id=1, symbol="EURUSD", entry=1)
        d.update(kw)
        return type("Deal", (), d)()

    def _mt5(self, deals):
        return type("MT5", (), {
            "history_deals_get": staticmethod(lambda *a, **k: deals)})()

    def test_frais_sommes_sur_tous_les_deals(self):
        """La commission est prélevée à l'entrée ET à la sortie."""
        from titanium.execution.position_manager import _cloture_depuis_historique
        mt5 = self._mt5([
            self._deal(time=1, commission=-2.5),
            self._deal(time=2, commission=-2.5, swap=-1.0),
        ])
        _, _, frais, _net = _cloture_depuis_historique(
            mt5, "1", expected_symbol="EURUSD")
        assert frais == pytest.approx(-6.0)

    def test_sortie_seule_ne_prouve_pas_un_net_complet(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        diagnostic = {}
        _cloture_depuis_historique(
            self._mt5([self._deal(entry=1, volume=0.1)]),
            "1", expected_symbol="EURUSD", diagnostic=diagnostic,
        )
        assert diagnostic["accounting_complete"] is False

    def test_entree_et_sorties_equilibrees_prouvent_le_net(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        diagnostic = {}
        _cloture_depuis_historique(
            self._mt5([
                self._deal(time=1, entry=0, volume=0.3),
                self._deal(time=2, entry=1, volume=0.1),
                self._deal(time=3, entry=1, volume=0.2),
            ]),
            "1", expected_symbol="EURUSD", diagnostic=diagnostic,
        )
        assert diagnostic["accounting_complete"] is True

    def test_volumes_desequilibres_ne_prouvent_pas_le_net(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        diagnostic = {}
        _cloture_depuis_historique(
            self._mt5([
                self._deal(time=1, entry=0, volume=0.3),
                self._deal(time=2, entry=1, volume=0.1),
            ]),
            "1", expected_symbol="EURUSD", diagnostic=diagnostic,
        )
        assert diagnostic["accounting_complete"] is False

    def test_volume_autre_position_ne_complete_pas_le_net(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        diagnostic = {}
        _cloture_depuis_historique(
            self._mt5([
                self._deal(time=1, entry=0, volume=0.2, position_id=2),
                self._deal(time=2, entry=0, volume=0.1, position_id=1),
                self._deal(time=3, entry=1, volume=0.2, position_id=1),
            ]),
            "1", expected_symbol="EURUSD", diagnostic=diagnostic,
        )
        assert diagnostic["accounting_complete"] is False

    def test_prix_vient_du_deal_de_sortie(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        mt5 = self._mt5([self._deal(time=1, price=1.10),
                         self._deal(time=2, price=1.13)])
        prix, _, _, _ = _cloture_depuis_historique(
            mt5, "1", expected_symbol="EURUSD")
        assert prix == pytest.approx(1.13)

    def test_champ_manquant_ne_leve_pas(self):
        """Certains courtiers n'exposent pas `fee`."""
        from titanium.execution.position_manager import _cloture_depuis_historique
        d = type("Deal", (), {"time": 1, "price": 1.11, "commission": -2.0,
                               "position_id": 1, "symbol": "EURUSD",
                               "entry": 1})()
        _, _, frais, _n = _cloture_depuis_historique(
            self._mt5([d]), "1", expected_symbol="EURUSD")
        assert frais == pytest.approx(-2.0)

    def test_historique_vide(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        assert _cloture_depuis_historique(
            self._mt5([]), "1", expected_symbol="EURUSD",
        ) == (None, "", 0.0, 0.0)

    def test_symbole_attendu_est_obligatoire(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        with pytest.raises(TypeError):
            _cloture_depuis_historique(self._mt5([]), "1")

    def test_deal_sans_identite_est_rejete(self):
        from titanium.execution.position_manager import _cloture_depuis_historique
        deal_sans_identite = type("Deal", (), {
            "time": 1, "price": 1.11, "entry": 1, "profit": 10.0,
        })()
        assert _cloture_depuis_historique(
            self._mt5([deal_sans_identite]), "1", expected_symbol="EURUSD",
        ) == (None, "", 0.0, 0.0)

    def test_deals_autre_position_ou_symbole_sont_ignores(self):
        """Une fermeture groupee ne doit jamais recopier le prix du GER40."""
        from titanium.execution.position_manager import _cloture_depuis_historique
        deals = [
            self._deal(time=1, price=1.1150, entry=1, position_id=1,
                       symbol="EURUSD", profit=12.0),
            self._deal(time=2, price=26333.85, entry=1, position_id=2,
                       symbol="GER40", profit=8.0),
            self._deal(time=3, price=999.0, entry=1, position_id=1,
                       symbol="GER40", profit=99.0),
        ]

        prix, _, frais, net = _cloture_depuis_historique(
            self._mt5(deals), "1", expected_symbol="EURUSD")

        assert prix == pytest.approx(1.1150)
        assert frais == pytest.approx(0.0)
        assert net == pytest.approx(12.0)

    def test_pnl_est_net_de_frais(self, tmp_path):
        """Contrat de `ClosedTrade` : pnl_r est NET, comme au backtest."""
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import (
            PHASE_TRAILING, TrackedState, journaliser_cloture,
        )
        j = tmp_path / "t.ndjson"
        st = TrackedState(r=0.01, phase=PHASE_TRAILING, symbol="EURUSD",
                          side=1, entry=1.1000, context_key="EURUSD|long|c|3p",
                          risque_devise=100.0)
        journaliser_cloture(st, "1", prix_sortie=1.1200, ts_exit="",
                            journal_path=j, cost_r=0.12, net_devise=188.0)
        t = TradeJournal(j).read_all()[0]
        assert t.pnl_r == pytest.approx(2.0 - 0.12)   # brut 2 R, net 1.88
        assert t.cost_r == pytest.approx(0.12)

    def test_cout_negatif_compte_comme_charge(self, tmp_path):
        """MT5 rend la commission négative ; c'est une charge, pas un gain."""
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import (
            PHASE_TRAILING, TrackedState, journaliser_cloture,
        )
        j = tmp_path / "t.ndjson"
        st = TrackedState(r=0.01, phase=PHASE_TRAILING, symbol="EURUSD",
                          side=1, entry=1.1000, context_key="EURUSD|long|c|3p",
                          risque_devise=100.0)
        journaliser_cloture(st, "1", prix_sortie=1.1200, ts_exit="",
                            journal_path=j, cost_r=-0.12, net_devise=188.0)
        assert TradeJournal(j).read_all()[0].pnl_r == pytest.approx(1.88)

    def test_risque_devise_survit_au_disque(self, tmp_path):
        from titanium.execution.position_manager import (
            TrackedState, load_state, save_state,
        )
        p = tmp_path / "pos.json"
        save_state(p, {"1": TrackedState(
            r=0.01, risque_devise=57.5, spread_r=0.21,
        )})
        relu = load_state(p)["1"]
        assert relu.risque_devise == pytest.approx(57.5)
        assert relu.spread_r == pytest.approx(0.21)
        assert relu.spread_exact is False

    def test_etat_ancien_sans_risque_devise(self, tmp_path):
        """Une mise à jour du code, positions ouvertes, ne doit rien casser."""
        import json
        from titanium.execution.position_manager import load_state
        p = tmp_path / "pos.json"
        p.write_text(json.dumps({"1": {"r": 0.01, "phase": "init"}}),
                     encoding="utf-8")
        relu = load_state(p)["1"]
        assert relu.risque_devise == 0.0
        assert relu.spread_r is None


class TestVoieExacte:
    """PnL calculé sur le NET en devise, pas reconstruit par les prix.

    C'est la donnée qui décidera d'ouvrir le mode réel. Une espérance
    reconstruite ignore commission et swap : elle flatte, systématiquement.
    """

    def _etat(self, **kw):
        from titanium.execution.position_manager import PHASE_TRAILING, TrackedState
        d = dict(r=0.01, phase=PHASE_TRAILING, symbol="EURUSD", side=1,
                 entry=1.1000, context_key="EURUSD|long|continuation|3p",
                 risque_devise=50.0)
        d.update(kw)
        return TrackedState(**d)

    def test_net_divise_par_le_risque(self, tmp_path):
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        journaliser_cloture(self._etat(), "1", prix_sortie=1.12, ts_exit="",
                            journal_path=j, net_devise=75.0, exact_net=True)
        t = TradeJournal(j).read_all()[0]
        assert t.pnl_r == pytest.approx(1.5)      # 75 / 50
        assert t.exact_net is True
        assert t.cost_r is None
        assert t.exact_cost is False

    def test_le_signe_vient_du_net_pas_du_sens(self, tmp_path):
        """MT5 signe déjà `profit` selon le sens. Multiplier par `side`
        inverserait tous les shorts."""
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        journaliser_cloture(self._etat(side=-1), "1", prix_sortie=1.08,
                            ts_exit="", journal_path=j, net_devise=60.0)
        assert TradeJournal(j).read_all()[0].pnl_r == pytest.approx(1.2)

    def test_les_frais_ne_sont_pas_comptes_deux_fois(self, tmp_path):
        """Sur la voie exacte, ils sont DÉJÀ dans le net."""
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        journaliser_cloture(self._etat(spread_exact=True), "1", prix_sortie=1.12, ts_exit="",
                            journal_path=j, net_devise=75.0, cost_r=0.3,
                            exact_net=True)
        t = TradeJournal(j).read_all()[0]
        assert t.pnl_r == pytest.approx(1.5)
        assert t.exact_net is True
        assert t.cost_r == pytest.approx(0.3)
        assert t.exact_cost is True

    def test_spread_estime_ne_se_declare_pas_exact(self, tmp_path):
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        journaliser_cloture(
            self._etat(spread_r=0.2, spread_exact=False), "1",
            prix_sortie=1.12, ts_exit="", journal_path=j,
            net_devise=75.0, cost_r=0.3, exact_net=True)
        trade = TradeJournal(j).read_all()[0]
        assert trade.exact_net is True
        assert trade.exact_cost is False

    def test_cout_exact_ne_contourne_pas_un_net_non_prouve(self, tmp_path):
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        journaliser_cloture(
            self._etat(spread_exact=True), "1",
            prix_sortie=1.12, ts_exit="", journal_path=j,
            net_devise=75.0, cost_r=0.3, exact_net=False,
        )
        trade = TradeJournal(j).read_all()[0]
        assert trade.exact_net is False
        assert trade.exact_cost is False

    def test_sans_net_comptable_est_refuse(self, tmp_path):
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        diagnostic = {}
        assert not journaliser_cloture(
            self._etat(), "1", prix_sortie=1.12, ts_exit="",
            journal_path=j, diagnostic=diagnostic)
        assert diagnostic["reason"] == "PNL_NET_INCONNU"

    def test_le_signe_vient_du_net_meme_si_le_prix_suggere_autre_chose(self, tmp_path):
        from titanium.edge import TradeJournal
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        journaliser_cloture(self._etat(side=-1), "1", prix_sortie=1.12,
                            ts_exit="", journal_path=j, net_devise=-100.0)
        assert TradeJournal(j).read_all()[0].pnl_r == pytest.approx(-2.0)

    def test_sans_risque_engage_pas_de_voie_exacte(self, tmp_path):
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        diagnostic = {}
        assert not journaliser_cloture(
            self._etat(risque_devise=0.0), "1", prix_sortie=1.12,
            ts_exit="", journal_path=j, net_devise=75.0,
            diagnostic=diagnostic)
        assert diagnostic["reason"] == "RISQUE_DEVISE_INCONNU"

    def test_deal_de_sortie_prefere_au_dernier(self):
        """Une position peut être clôturée en plusieurs fois ; un deal de
        correction postérieur porterait un prix qui n'est pas la sortie."""
        from titanium.execution.position_manager import _cloture_depuis_historique
        deals = [
            type("D", (), {"time": 1, "price": 1.10, "entry": 0,
                           "commission": -1.0, "swap": 0.0, "profit": 0.0,
                           "position_id": 1, "symbol": "EURUSD"})(),
            type("D", (), {"time": 2, "price": 1.13, "entry": 1,
                           "commission": -1.0, "swap": 0.0, "profit": 30.0,
                           "position_id": 1, "symbol": "EURUSD"})(),
            type("D", (), {"time": 3, "price": 9.99, "entry": 2,
                           "commission": 0.0, "swap": 0.0, "profit": 0.0,
                           "position_id": 1, "symbol": "EURUSD"})(),
        ]
        mt5 = type("M", (), {
            "history_deals_get": staticmethod(lambda *a, **k: deals)})()
        prix, _, frais, net = _cloture_depuis_historique(
            mt5, "1", expected_symbol="EURUSD")
        assert prix == pytest.approx(9.99), "INOUT est aussi une sortie MT5"
        assert frais == pytest.approx(-2.0)
        assert net == pytest.approx(28.0)         # 30 brut − 2 de frais

    @pytest.mark.parametrize("entry", [2, 3])
    def test_inout_et_close_by_sont_des_clotures(self, entry):
        from titanium.execution.position_manager import _cloture_depuis_historique

        deal = type("D", (), {
            "time": 2, "price": 1.13, "entry": entry,
            "commission": -1.0, "swap": 0.0, "fee": 0.0,
            "profit": 30.0, "position_id": 1, "symbol": "EURUSD",
        })()
        mt5 = type("M", (), {
            "history_deals_get": staticmethod(lambda *a, **k: [deal])})()

        prix, _, _, net = _cloture_depuis_historique(
            mt5, "1", expected_symbol="EURUSD")
        assert prix == pytest.approx(1.13)
        assert net == pytest.approx(29.0)


class TestInterblocageJournal:
    """Un refus DEFINITIF de journalisation gele le bot indefiniment.

    Objection de revue (Claude, 08/08/2026) sur le lot V14-JOURNAL-FAILCLOSED.

    Conserver l'etat quand la journalisation echoue est juste : le perdre
    rendrait l'ecart MT5/journal irreparable. Mais `journaliser_cloture`
    rend False pour DEUX raisons de nature opposee :

      transitoire  disque plein, fichier verrouille -> le reessai marchera
      DEFINITIF    bornes de plausibilite du R -> il ne marchera JAMAIS

    Dans le second cas l'etat est conserve a chaque passage, `JOURNAL_GAP`
    reste actif, `gestion_saine` reste False, et la boucle refuse toute
    nouvelle entree POUR TOUJOURS. Un seul trade aberrant suffit.

    Le scenario n'est pas theorique : les bornes existent parce que
    +101 280 739 R avaient ete journalises le 07/08/2026. Elles ont raison
    de rejeter ; c'est la retention perpetuelle qui pose probleme.
    """

    def _etat_impossible(self):
        from titanium.execution.position_manager import TrackedState
        # r = 1e-9 sur une entree a 1.10 : ratio 9e-10, sous R_MIN_RELATIF.
        return TrackedState(r=1e-9, symbol="EURUSD", side=1, entry=1.10,
                            sl_initial=1.09, risque_devise=25.0)

    def test_le_refus_est_permanent(self, tmp_path):
        """Cinq tentatives, cinq refus : le reessai ne servira jamais."""
        from titanium.execution.position_manager import journaliser_cloture
        j = tmp_path / "t.ndjson"
        st = self._etat_impossible()
        for _ in range(5):
            assert journaliser_cloture(st, "999", prix_sortie=1.12,
                                       ts_exit="", journal_path=j) is False
        assert not j.exists() or j.stat().st_size == 0

    def test_un_refus_transitoire_se_distingue_d_un_refus_definitif(self):
        """C'est la distinction qui manque.

        L'appelant doit pouvoir savoir s'il attend un reessai utile ou s'il
        garde un etat qui ne partira jamais. Sans elle, les deux cas
        produisent le meme `JOURNAL_GAP` et le meme blocage perpetuel.
        """
        from titanium.execution import position_manager as pm
        assert hasattr(pm, "MOTIF_REFUS_DEFINITIF"), (
            "journaliser_cloture doit distinguer un refus definitif "
            "(bornes de plausibilite) d'un echec transitoire (I/O), sinon "
            "un seul trade aberrant gele la boucle pour toujours")
