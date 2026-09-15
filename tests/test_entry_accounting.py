import json
from types import SimpleNamespace

import pytest

from titanium.analysis.entry_accounting import entry_balance


@pytest.mark.parametrize(
    ("sent", "simulated", "refused", "gap", "status"),
    [
        (1, 0, 3, 0, "BALANCED"),
        (0, 1, 3, 0, "BALANCED"),
        (0, 0, 3, 1, "INCOMPLETE"),
        (2, 0, 3, -1, "OVERCOUNTED"),
    ],
)
def test_raw_evaluations_are_not_fills(sent, simulated, refused, gap, status):
    result = entry_balance(
        {
            "enter": 4,
            "envoyes": sent,
            "simules": simulated,
            "tunnel": {
                "post_enter_refusal": {"COUT_SPREAD": refused},
                "portability_refusal": {"COUT_SPREAD": 1000},
            },
        }
    )
    assert result["status"] == status and result["unaccounted"] == gap
    assert result["fills"] is None


@pytest.mark.parametrize("value", [None, -1, True, 1.5, "1"])
def test_missing_or_invalid_counter_is_not_zero(value):
    assert entry_balance({"enter": value, "envoyes": 0, "simules": 0})["status"] == "UNAVAILABLE"


def test_all_discarded_timeframes_are_accounted_without_changing_selection(monkeypatch, tmp_path):
    from tools import live_demo as live

    monkeypatch.setattr(live, "REFUS_LIVE", tmp_path / "refus.ndjson")
    candidates = [
        {
            "sym": sym,
            "timeframe": tf,
            "support": 2,
            "rank": 1,
            "cost": 0.1,
            "out": SimpleNamespace(side=side),
        }
        for sym, tf, side in [
            ("BTC", "M1", 1),
            ("BTC", "M5", 1),
            ("ETH", "M1", -1),
            ("ETH", "M5", 1),
        ]
    ]
    kept, conflicts = live._resoudre_candidats_multitimeframe(candidates)
    stats = {"enter": 4, "envoyes": 1, "simules": 0}
    live._journaliser_selection_multitimeframe(candidates, kept, conflicts, stats)
    assert kept == [candidates[1]] and conflicts == ["ETH"]
    result = entry_balance(stats)
    assert result["status"] == "BALANCED" and result["coalesced"] == 1
    journal = [json.loads(line) for line in live.REFUS_LIVE.read_text().splitlines()]
    assert len(journal) == 3 and all(r["timeframe"] for r in journal)


def test_irm_uses_measured_orders_despite_missing_refusals(monkeypatch, tmp_path):
    from tools import irm

    (tmp_path / "loop_heartbeat.json").write_text(
        json.dumps(
            {
                "stats": {
                    "enter": 704,
                    "envoyes": 0,
                    "simules": 0,
                    "tunnel": {"gate_verdict": {"ENTER": 704}, "post_enter_refusal": {"X": 18}},
                }
            }
        )
    )
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    result = irm._organes()
    assert result["chaine"][-1]["sort"] == 0
    assert result["entry_accounting"]["unaccounted"] == 686
