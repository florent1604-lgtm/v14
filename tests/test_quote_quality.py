import json
from datetime import datetime, timezone

import pytest

from titanium.analysis.quote_quality import audit_quote_files
from tools.audit_quotes_sorties import main

TS = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)


def row(**kwargs):
    return {"symbole": "BTCUSD", "horloge": "utc", "ts_ms": TS, "bid": 100, "ask": 101,
            **kwargs}


def archive(tmp_path, rows):
    target = tmp_path / "2026-09-01.ndjson"
    target.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return target


def test_clean_data_still_does_not_authorize_optimization(tmp_path):
    path = archive(tmp_path, [row(), row(ts_ms=TS + 100)])
    result = audit_quote_files([path], symbol="BTCUSD")
    assert result["verdict"] == "STRUCTURALLY_VALID"
    assert result["valid_quotes"] == 2
    assert result["ready_for_exit_optimization"] is False
    assert "baseline_reproduction" in result["not_validated"]
    assert len(result["files"][0]["sha256"]) == 64


@pytest.mark.parametrize("change", [{"bid": float("nan")}, {"ask": float("inf")},
    {"bid": True}, {"bid": 0}, {"ask": 99}, {"ts_ms": TS + 0.5},
    {"horloge": "server"}, {"symbole": "ETHUSD"}, {"ts_ms": TS + 86400000}])
def test_invalid_quotes_are_not_silently_dropped(tmp_path, change):
    result = audit_quote_files([archive(tmp_path, [row(**change)])], symbol="BTCUSD")
    assert result["verdict"] == "INVALID"
    assert result["errors"] == {"INVALID_RECORD": 1}


def test_order_preserved_and_regression_reported(tmp_path):
    result = audit_quote_files([archive(tmp_path, [row(ts_ms=TS+5), row()])], symbol="BTCUSD")
    assert result["errors"] == {"OUT_OF_ORDER": 1}


def test_gap_detection_crosses_files(tmp_path):
    p = archive(tmp_path, [row()])
    q = tmp_path / "2026-09-02.ndjson"
    q.write_text(json.dumps(row(ts_ms=TS+86400000)) + "\n")
    result = audit_quote_files([p, q], symbol="BTCUSD")
    assert result["verdict"] == "GAPPED"
    assert result["gaps"] == 1
    assert result["max_gap_ms"] == 86400000


def test_identical_quotes_counted_not_invented_error(tmp_path):
    result = audit_quote_files([archive(tmp_path, [row(), row()])], symbol="BTCUSD")
    assert result["consecutive_identical_quotes"] == 1
    assert result["errors"] == {}


def test_truncated_final_line_blocks(tmp_path):
    p = archive(tmp_path, [row()])
    with p.open("ab") as handle:
        handle.write(b'{"bid":')
    result = audit_quote_files([p], symbol="BTCUSD")
    assert result["errors"] == {"INCOMPLETE_LINE": 1}


def test_empty_and_missing_inputs(tmp_path):
    assert audit_quote_files([], symbol="BTCUSD")["errors"] == {"NO_FILES": 1}
    assert audit_quote_files([archive(tmp_path, [])], symbol="BTCUSD")["errors"] == {"EMPTY_FILE": 1}
    assert audit_quote_files([tmp_path / "missing"], symbol="BTCUSD")["errors"] == {"UNREADABLE_FILE": 1}


def test_oversized_line_is_bounded_and_next_record_read(tmp_path):
    p = archive(tmp_path, [])
    p.write_bytes(b"x" * 200000 + b"\n" + (json.dumps(row()) + "\n").encode())
    result = audit_quote_files([p], symbol="BTCUSD")
    assert result["errors"] == {"OVERSIZED_LINE": 1}
    assert result["valid_quotes"] == 1


def test_snapshot_change_invalidates_report(tmp_path, monkeypatch):
    p = archive(tmp_path, [row()])
    original = type(p).stat
    calls = []
    def stat(path, *args, **kwargs):
        calls.append(path)
        if path == p and len(calls) == 2:
            with p.open("ab") as handle:
                handle.write(b"\n")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(p), "stat", stat)
    result = audit_quote_files([p], symbol="BTCUSD")
    assert result["errors"] == {"FILE_CHANGED_DURING_READ": 1}


def test_cli_does_not_replace_report_or_write_into_archives(tmp_path):
    quotes = tmp_path / "quotes"
    quotes.mkdir()
    report = tmp_path / "report.json"
    args = ["--symboles", "BTCUSD", "--quotes", str(quotes), "--json", str(report)]
    assert main(args) == 1
    previous = report.read_bytes()
    with pytest.raises(FileExistsError):
        main(args)
    assert report.read_bytes() == previous
    with pytest.raises(SystemExit):
        main([*args[:-1], str(quotes / "report.json")])


@pytest.mark.parametrize("gap", [0, -1, True, 1.5])
def test_bad_gap_rejected(gap):
    with pytest.raises(ValueError):
        audit_quote_files([], symbol="BTCUSD", max_gap_ms=gap)
