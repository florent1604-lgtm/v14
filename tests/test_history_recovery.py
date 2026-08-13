from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from titanium.execution.history_recovery import recover_unobserved_closures
from titanium.edge import TradeJournal

BASE = int(datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).timestamp())


def deal(position, *, entry, magic=0, profit=0.0, time=BASE, volume=1.0):
    return SimpleNamespace(
        position_id=position,
        entry=entry,
        magic=magic,
        reason=4 if entry else 3,
        profit=profit,
        commission=0.0,
        swap=0.0,
        fee=0.0,
        comment="titanium-v14" if magic else "",
        time=time,
        time_msc=time * 1000,
        symbol="EURAUD",
        price=1.1 if entry == 0 else 1.09,
        volume=volume,
    )


class Mt5:
    def __init__(self, rows):
        self.rows = rows

    def history_deals_get(self, *_args):
        return self.rows


def test_cloture_eclair_est_conservee_hors_edge_et_idempotente(tmp_path):
    mt5 = Mt5([
        deal(89198681, entry=0, magic=14_000, time=BASE),
        deal(89198681, entry=1, profit=-22.48, time=BASE + 1),
    ])
    journal = tmp_path / "trades.ndjson"
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)

    first = recover_unobserved_closures(
        mt5, magic=14_000, journal_path=journal, now=now,
    )
    second = recover_unobserved_closures(
        mt5, magic=14_000, journal_path=journal, now=now,
    )

    assert first == {
        "recovered": 1,
        "scanned": 1,
        "mt5_closed": 1,
        "journal_edge": 0,
        "missing_in_edge": 1,
        "missing_in_edge_rate": 1.0,
        "reason": "",
    }
    assert second == {
        "recovered": 0,
        "scanned": 1,
        "mt5_closed": 1,
        "journal_edge": 0,
        "missing_in_edge": 1,
        "missing_in_edge_rate": 1.0,
        "reason": "",
    }
    assert not journal.exists()
    rows = (tmp_path / "journal_rejets.ndjson").read_text(
        encoding="utf-8",
    ).splitlines()
    assert len(rows) == 1
    recovered = json.loads(rows[0])
    assert recovered["ticket"] == "live:89198681"
    assert recovered["net_currency"] == -22.48
    assert recovered["edge_eligible"] is False
    assert recovered["recovered_from_mt5_history"] is True


def test_ticket_protege_reste_au_gestionnaire_avec_son_contexte(tmp_path):
    mt5 = Mt5([
        deal(42, entry=0, magic=14_000, time=BASE),
        deal(42, entry=1, profit=-5, time=BASE + 1),
    ])
    report = recover_unobserved_closures(
        mt5,
        magic=14_000,
        journal_path=tmp_path / "trades.ndjson",
        protected_position_ids={"42"},
        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    assert report["recovered"] == 0
    assert not (tmp_path / "journal_rejets.ndjson").exists()


def test_historique_indisponible_ne_leve_pas(tmp_path):
    mt5 = SimpleNamespace(history_deals_get=lambda *_args: None)
    report = recover_unobserved_closures(
        mt5,
        magic=14_000,
        journal_path=tmp_path / "trades.ndjson",
    )
    assert report["reason"] == "HISTORY_UNAVAILABLE"


def test_couverture_edge_est_calculee_sur_les_clotures_mt5(tmp_path):
    mt5 = Mt5([
        deal(41, entry=0, magic=14_000, time=BASE),
        deal(41, entry=1, profit=2, time=BASE + 1),
        deal(42, entry=0, magic=14_000, time=BASE + 2),
        deal(42, entry=1, profit=-1, time=BASE + 3),
    ])
    journal = tmp_path / "trades.ndjson"
    journal.write_text(
        json.dumps({"ticket": "live:41"}) + "\n",
        encoding="utf-8",
    )

    report = recover_unobserved_closures(
        mt5,
        magic=14_000,
        journal_path=journal,
        protected_position_ids={"42"},
        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    assert report["mt5_closed"] == 2
    assert report["journal_edge"] == 1
    assert report["missing_in_edge"] == 1
    assert report["missing_in_edge_rate"] == 0.5


def test_cloture_recente_heure_serveur_est_incluse_et_ecrite_en_utc(
    monkeypatch, tmp_path,
):
    import titanium.execution.history_recovery as recovery

    offset = 3 * 3600
    monkeypatch.setattr(recovery, "decalage_serveur", lambda *_args: offset)
    mt5 = Mt5([
        deal(77, entry=0, magic=14_000, time=BASE + offset),
        deal(77, entry=1, profit=4, time=BASE + offset + 1),
    ])
    now = datetime.fromtimestamp(BASE + 60, timezone.utc)

    report = recover_unobserved_closures(
        mt5,
        magic=14_000,
        journal_path=tmp_path / "trades.ndjson",
        now=now,
    )

    assert report["mt5_closed"] == 1
    row = json.loads(
        (tmp_path / "journal_rejets.ndjson").read_text(encoding="utf-8"),
    )
    assert row["ts_open"] == datetime.fromtimestamp(
        BASE, timezone.utc,
    ).isoformat()
    assert row["ts_exit"] == datetime.fromtimestamp(
        BASE + 1, timezone.utc,
    ).isoformat()
    assert datetime.fromisoformat(row["ts_exit"]) <= now + timedelta(minutes=1)


def test_fenetre_utc_exclut_les_deals_juste_hors_borne_apres_conversion(
    monkeypatch, tmp_path,
):
    import titanium.execution.history_recovery as recovery

    offset = 2 * 3600
    monkeypatch.setattr(recovery, "decalage_serveur", lambda *_args: offset)
    now = datetime.fromtimestamp(BASE + 60, timezone.utc)
    mt5 = Mt5([
        deal(77, entry=0, magic=14_000, time=BASE + offset),
        deal(77, entry=1, profit=4, time=BASE + offset + 1),
        deal(78, entry=0, magic=14_000, time=BASE + offset + 120),
        deal(78, entry=1, profit=4, time=BASE + offset + 121),
    ])

    report = recover_unobserved_closures(
        mt5,
        magic=14_000,
        journal_path=tmp_path / "trades.ndjson",
        now=now,
    )

    assert report["mt5_closed"] == 1
    row = json.loads(
        (tmp_path / "journal_rejets.ndjson").read_text(encoding="utf-8"),
    )
    assert row["ticket"] == "live:77"
    assert row["ts_exit"] == datetime.fromtimestamp(
        BASE + 1, timezone.utc,
    ).isoformat()


def _quarantine_row(ticket: int) -> dict:
    return {
        "ticket": f"live:{ticket}",
        "symbol": "EURAUD",
        "reason": "SORTIE_INTROUVABLE_APRES_2_ESSAIS",
        "source": "live",
        "quarantine_recoverable": True,
        "tracked_state": {
            "r": 0.01,
            "phase": "init",
            "peak_fav_r": 0.2,
            "symbol": "EURAUD",
            "side": 1,
            "entry": 1.1,
            "sl_initial": 1.09,
            "tp_initial": 1.12,
            "context_key": "EURAUD|long|continuation|3p",
            "indicators": {"ltf_rsi_14": 52.0},
            "ts_open": "2026-08-12T11:59:00+00:00",
            "mae_r": -0.1,
            "risque_devise": 10.0,
            "spread_r": 0.05,
            "spread_exact": True,
            "mode": "explore",
            "quorum": 3,
            "support_pillars": 3,
            "asset_class": "fx",
            "account": "demo",
            "timeframe": "H1",
            "candle_source": "",
            "limit_order_ticket": 0,
            "limit_planned_price": 0.0,
            "limit_market_reference_price": 0.0,
            "limit_target_saving_r": None,
            "limit_realized_saving_r": None,
            "limit_slippage_r": None,
            "history_missing_since": "2026-08-12T12:00:00+00:00",
            "history_missing_attempts": 2,
        },
    }


def test_quarantaine_est_resolue_append_only_quand_in_et_out_apparaissent(
    tmp_path,
):
    ticket = 79
    journal = tmp_path / "trades.ndjson"
    rejected = tmp_path / "journal_rejets.ndjson"
    rejected.write_text(
        json.dumps(_quarantine_row(ticket)) + "\n",
        encoding="utf-8",
    )
    mt5 = Mt5([
        deal(ticket, entry=0, magic=14_000, time=BASE, profit=0),
        deal(ticket, entry=1, time=BASE + 1, profit=-5),
    ])
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)

    first = recover_unobserved_closures(
        mt5, magic=14_000, journal_path=journal, now=now,
    )
    second = recover_unobserved_closures(
        mt5, magic=14_000, journal_path=journal, now=now,
    )

    trades = TradeJournal(journal).read_all()
    resolutions = [
        json.loads(line)
        for line in rejected.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "SORTIE_INTROUVABLE_RESOLUE"
    ]
    assert len(trades) == 1
    assert trades[0].ticket == f"live:{ticket}"
    assert trades[0].pnl_r == -0.5
    assert first["resolved_quarantines"] == 1
    assert second["resolved_quarantines"] == 0
    assert len(resolutions) == 1
    assert resolutions[0]["deal_in_proven"] is True
    assert resolutions[0]["accounting_complete"] is True
    assert resolutions[0]["history_resolution_age_seconds"] > 0


def test_quarantaine_ne_se_resout_pas_sans_deal_in(tmp_path):
    ticket = 80
    journal = tmp_path / "trades.ndjson"
    rejected = tmp_path / "journal_rejets.ndjson"
    rejected.write_text(
        json.dumps(_quarantine_row(ticket)) + "\n",
        encoding="utf-8",
    )
    mt5 = Mt5([deal(ticket, entry=1, magic=14_000, time=BASE + 1, profit=-5)])

    report = recover_unobserved_closures(
        mt5,
        magic=14_000,
        journal_path=journal,
        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    assert report["resolved_quarantines"] == 0
    assert TradeJournal(journal).read_all() == []


def test_quarantaine_ne_se_resout_pas_sur_sortie_partielle(tmp_path):
    ticket = 81
    journal = tmp_path / "trades.ndjson"
    rejected = tmp_path / "journal_rejets.ndjson"
    rejected.write_text(
        json.dumps(_quarantine_row(ticket)) + "\n",
        encoding="utf-8",
    )
    mt5 = Mt5([
        deal(ticket, entry=0, magic=14_000, time=BASE, volume=1.0),
        deal(ticket, entry=1, time=BASE + 1, profit=-2, volume=0.4),
    ])

    report = recover_unobserved_closures(
        mt5,
        magic=14_000,
        journal_path=journal,
        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )

    assert report["resolved_quarantines"] == 0
    assert TradeJournal(journal).read_all() == []
