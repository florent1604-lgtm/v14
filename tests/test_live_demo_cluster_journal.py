"""Journal additif des grappes pour chaque candidat ENTER."""

from __future__ import annotations

import json
from types import SimpleNamespace

import tools.live_demo as live


class _Grappes:
    membres = {"g7": ["EURUSD", "GBPUSD"]}

    @staticmethod
    def grappe_de(_symbol):
        return "g7"


def _candidate():
    return {
        "sym": "EURUSD",
        "feats": {"_trace": {"bar_time": "2026-08-11T19:00:00Z",
                              "timeframe": "H1"}},
        "out": SimpleNamespace(side=1, risk_money=25.0),
        "dec": SimpleNamespace(setup_family="continuation"),
        "support": 3,
        "rank": 0.72,
    }


def test_cluster_journal_is_append_only_and_deduplicated(monkeypatch, tmp_path):
    import titanium.correlation as correlation

    output = tmp_path / "candidats_grappe.ndjson"
    monkeypatch.setattr(live, "CANDIDATS_GRAPPE", output)
    monkeypatch.setattr(live, "_GRAPPES", _Grappes())
    monkeypatch.setattr(live, "_CANDIDATS_GRAPPE_VUS", set())
    monkeypatch.setattr(correlation, "risque_par_grappe",
                        lambda *_args, **_kwargs: {"g7": 1.25})

    assert live._journaliser_grappes([_candidate()], 5_000.0) == 1
    assert live._journaliser_grappes([_candidate()], 5_000.0) == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["candidate_key"] == "EURUSD:H1:2026-08-11T19:00:00Z"
    assert rows[0]["cluster"] == "g7"
    assert rows[0]["cluster_risk_engaged_pct"] == 1.25
    assert rows[0]["proposed_risk_pct"] == 0.5
    assert rows[0]["setup_family"] == "continuation"
