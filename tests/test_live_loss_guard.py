import json
from datetime import datetime, timedelta, timezone

from titanium.execution.live_loss_guard import evaluate_live_loss_guard

NOW = datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc)


def write_trades(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def trade(pnl_r, closed_at, *, account="123", ticket="1"):
    return {
        "context": "US30|short|continuation|3p",
        "pnl_r": pnl_r,
        "closed_at": closed_at.isoformat(),
        "ticket": f"live:{ticket}",
        "source": "live",
        "account": account,
        "exact_net": True,
        "horloge": "utc",
    }


def test_missing_journal_blocks_arming(tmp_path):
    verdict = evaluate_live_loss_guard(tmp_path / "missing.ndjson", account="123", now=NOW)

    assert verdict.action == "WAIT"
    assert verdict.reason == "LIVE_LOSS_JOURNAL_MISSING"


def test_daily_loss_of_two_r_blocks_new_entries(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        trade(-0.75, NOW - timedelta(hours=3), ticket="1"),
        trade(-1.0, NOW - timedelta(hours=2), ticket="2"),
        trade(-0.3, NOW - timedelta(hours=1), ticket="3"),
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "BLOCK"
    assert verdict.reason == "DAILY_LOSS_LIMIT"
    assert verdict.daily_trades == 3
    assert verdict.daily_net_r == -2.05


def test_rolling_loss_blocks_after_daily_boundary(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        trade(-1.5, NOW - timedelta(days=offset), ticket=str(offset))
        for offset in (1, 2, 3, 4)
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "BLOCK"
    assert verdict.reason == "ROLLING_7D_LOSS_LIMIT"
    assert verdict.rolling_net_r == -6.0


def test_other_accounts_old_rows_and_duplicate_tickets_are_excluded(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(path, [
        trade(-9.0, NOW - timedelta(hours=1), account="999", ticket="other"),
        trade(-9.0, NOW - timedelta(days=8), ticket="old"),
        trade(-1.0, NOW - timedelta(hours=2), ticket="same"),
        trade(0.5, NOW - timedelta(hours=1), ticket="same"),
    ])

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "ALLOW"
    assert verdict.daily_trades == 1
    assert verdict.daily_net_r == 0.5


def test_legacy_rows_outside_window_do_not_poison_current_guard(tmp_path):
    path = tmp_path / "trades.ndjson"
    write_trades(
        path,
        [
            {
                "source": "live",
                "account": "123",
                "closed_at": (NOW - timedelta(days=30)).isoformat(),
                "ticket": "live:legacy",
            },
            trade(0.2, NOW - timedelta(hours=1), ticket="current"),
        ],
    )

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "ALLOW"
    assert verdict.rolling_trades == 1


def test_malformed_or_untrusted_recent_line_fails_closed(tmp_path):
    path = tmp_path / "trades.ndjson"
    path.write_text("not-json\n", encoding="utf-8")

    verdict = evaluate_live_loss_guard(path, account="123", now=NOW)

    assert verdict.action == "WAIT"
    assert verdict.reason == "LIVE_LOSS_JOURNAL_INVALID"
