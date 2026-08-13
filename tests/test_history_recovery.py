from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from titanium.execution.history_recovery import recover_unobserved_closures


def deal(position, *, entry, magic=0, profit=0.0, time=1):
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
    )


class Mt5:
    def __init__(self, rows):
        self.rows = rows

    def history_deals_get(self, *_args):
        return self.rows


def test_cloture_eclair_est_conservee_hors_edge_et_idempotente(tmp_path):
    mt5 = Mt5([
        deal(89198681, entry=0, magic=14_000, time=1),
        deal(89198681, entry=1, profit=-22.48, time=2),
    ])
    journal = tmp_path / "trades.ndjson"
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)

    first = recover_unobserved_closures(
        mt5, magic=14_000, journal_path=journal, now=now,
    )
    second = recover_unobserved_closures(
        mt5, magic=14_000, journal_path=journal, now=now,
    )

    assert first == {"recovered": 1, "scanned": 1, "reason": ""}
    assert second == {"recovered": 0, "scanned": 1, "reason": ""}
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
        deal(42, entry=0, magic=14_000, time=1),
        deal(42, entry=1, profit=-5, time=2),
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
