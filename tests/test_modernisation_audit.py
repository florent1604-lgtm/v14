import json

import pytest

from titanium.analysis.modernisation import (
    audit,
    daily_bootstrap,
    pillar_comparison,
    read_prefix,
    summary,
    unique,
)


def test_audit_normalizes_order_position_links_and_does_not_drop_losers():
    trades = [
        {
            "source": "live",
            "ticket": f"live:{i}",
            "closed_at": "2026-09-01T00:00:00+00:00",
            "context": "BTC|long|range|2p",
            "asset_class": "crypto",
            "support_pillars": 2,
            "pnl_r": pnl,
            "cost_r": 0.1,
        }
        for i, pnl in [(1, -1), (2, 1)]
    ]
    excursions = [
        {
            "ticket": r["ticket"],
            "pnl_r": r["pnl_r"],
            "mfe_r": 0.2 if i == 0 else 1,
            "censored": i == 0,
            "indicators": {},
        }
        for i, r in enumerate(trades)
    ]
    lifecycle = [
        {"event": "placed", "order_ticket": 5, "spread_r": 0.1},
        {"event": "filled", "order_ticket": 5, "position_ticket": 1},
        {"event": "closed", "order_ticket": 5, "position_ticket": 1},
    ]
    result = audit(trades, excursions, lifecycle, [])
    assert result["joins"]["limit_closed_with_placed"] == 1
    assert result["regime_vs_mfe"]["n_trades"] == 2
    assert result["regime_uncensored_sensitivity"]["n_trades"] == 1
    assert result["promotion"].startswith("NONE")


def test_empty_live_journal_is_not_a_valid_zero_result():
    with pytest.raises(ValueError, match="no live trades"):
        audit([], [], [], [])


def test_prefix_reproduces_despite_new_appends(tmp_path):
    path = tmp_path / "records.ndjson"
    path.write_bytes(b'{"ticket":"live:1"}\n{"partial":')
    rows, proof = read_prefix(path)
    assert len(rows) == 1 and proof["partial_tail_bytes"] > 0
    with path.open("ab") as stream:
        stream.write(b"1}\n")
    assert read_prefix(path, proof) == (rows, proof)
    path.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="snapshot changed"):
        read_prefix(path, proof)


def test_bad_middle_line_is_not_silently_dropped(tmp_path):
    path = tmp_path / "records.ndjson"
    path.write_text("broken\n{}\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_prefix(path)


def test_cost_proxy_is_not_an_exact_cost():
    rows = [{"pnl_r": -1, "cost_r": 0.1, "exact_cost": False}, {"pnl_r": 1, "cost_r": None}]
    result = summary(rows)
    assert result["mean_r"] == 0 and result["cost_exact_n"] == 0
    assert result["gross_proxy_mean_r"] == -0.9 and result["cost_known_n"] == 1
    with pytest.raises(ValueError):
        summary([{"pnl_r": float("nan")}])


def test_ticket_normalization_does_not_hide_conflicts():
    assert list(unique([{"ticket": "live:123", "pnl_r": 1}])) == ["123"]
    with pytest.raises(ValueError, match="conflicting"):
        unique([{"ticket": "live:123", "pnl_r": 1}, {"ticket": 123, "pnl_r": -1}])


def test_unmatched_pillars_do_not_establish_an_effect():
    rows = [
        {
            "closed_at": "2026-09-01T00:00:00+00:00",
            "context": "A|long",
            "asset_class": "fx",
            "support_pillars": 2,
            "pnl_r": 1,
        }
    ]
    assert pillar_comparison(rows, by_symbol=True)["strata"] == 0
    assert daily_bootstrap(rows)["ci95"] is None
