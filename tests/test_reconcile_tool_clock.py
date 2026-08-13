from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tools import reconcile_mt5_journal as tool


def test_history_bounds_use_the_same_server_offset_as_deal_conversion():
    since = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    until = datetime(2026, 8, 13, 13, 0, tzinfo=timezone.utc)

    start, end = tool._history_bounds(since, until, 3 * 3600)

    assert start == since + timedelta(hours=3)
    assert end == until + timedelta(hours=3, minutes=1)


def test_filter_positions_uses_converted_utc_timestamps():
    since = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    until = datetime(2026, 8, 13, 13, 0, tzinfo=timezone.utc)
    positions = [
        SimpleNamespace(position_id="before", closed_at="2026-08-13T09:59:59+00:00"),
        SimpleNamespace(position_id="inside", closed_at="2026-08-13T12:59:59+00:00"),
        SimpleNamespace(position_id="after", closed_at="2026-08-13T13:01:01+00:00"),
    ]

    filtered = tool._positions_in_window(positions, since, until)

    assert [row.position_id for row in filtered] == ["inside"]

