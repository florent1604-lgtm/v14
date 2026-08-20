import json
from datetime import datetime, timezone

import pytest

from titanium.fundamentals import (
    FundamentalRecord,
    infer_exposures,
    score_fundamentals,
)
from tools.fundamentals_shadow import load_records, main

UTC = timezone.utc
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def release(record_id, factors, *, hours_ago=1, available_hours_ago=1, **kwargs):
    return FundamentalRecord(
        record_id=record_id,
        kind="release",
        category=kwargs.pop("category", "macro"),
        event_at=datetime(2026, 8, 20, 12 - hours_ago, tzinfo=UTC),
        available_at=datetime(2026, 8, 20, 12 - available_hours_ago, tzinfo=UTC),
        factors=factors,
        **kwargs,
    )


def test_fx_score_is_bilateral_not_usd_only():
    records = [
        release("ecb", {"EUR": 0.7}),
        release("fed", {"USD": 0.4}),
    ]
    eurusd = score_fundamentals("EURUSD", records, as_of=NOW)
    usdjpy = score_fundamentals("USDJPY", records, as_of=NOW)

    assert eurusd.status == "directional"
    assert eurusd.direction_score == pytest.approx(0.15)
    assert usdjpy.direction_score == pytest.approx(0.4)


def test_unknown_is_not_silently_converted_to_neutral():
    snapshot = score_fundamentals("EURUSD", [], as_of=NOW)
    payload = snapshot.to_dict()

    assert snapshot.status == "unknown"
    assert snapshot.direction_score is None
    assert payload["would_block"] is False
    assert payload["would_reduce"] is False


def test_future_or_not_yet_available_release_is_excluded():
    future = FundamentalRecord(
        record_id="future",
        kind="release",
        category="inflation",
        event_at="2026-08-20T13:00:00Z",
        available_at="2026-08-20T13:00:03Z",
        factors={"USD": 1.0},
    )
    delayed = FundamentalRecord(
        record_id="delayed",
        kind="release",
        category="employment",
        event_at="2026-08-20T11:00:00Z",
        available_at="2026-08-20T12:00:01Z",
        factors={"USD": 1.0},
    )

    snapshot = score_fundamentals("USDJPY", [future, delayed], as_of=NOW)
    assert snapshot.status == "unknown"
    assert snapshot.matched_releases == 0


def test_scheduled_event_reports_risk_without_inventing_direction():
    event = FundamentalRecord(
        record_id="fomc",
        kind="scheduled_event",
        category="central_bank",
        event_at="2026-08-20T12:15:00Z",
        available_at="2026-08-19T08:00:00Z",
        factors={},
        event_risk=1.0,
        risk_before_minutes=30,
        risk_after_minutes=45,
    )

    snapshot = score_fundamentals("USTECH", [event], as_of=NOW)
    assert snapshot.status == "event_risk"
    assert snapshot.direction_score is None
    assert snapshot.event_risk == 1.0
    assert "FENETRE_EVENEMENT_ACTIVE" in snapshot.reasons


def test_aliases_share_fundamental_exposures():
    assert infer_exposures("USTECH") == infer_exposures("NAS100.fs")
    assert infer_exposures("US500") == infer_exposures("S&P.fs")
    assert infer_exposures("USOIL") == infer_exposures("WTI.fs")
    assert infer_exposures("XAUUSD") == {"GOLD": 1.0, "USD": -1.0}


def test_old_release_decays_and_loses_confidence():
    recent = release("recent", {"USD": 1.0}, half_life_hours=1.0)
    old = release(
        "old",
        {"USD": -1.0},
        hours_ago=11,
        available_hours_ago=11,
        half_life_hours=1.0,
    )
    snapshot = score_fundamentals("USDJPY", [recent, old], as_of=NOW)

    assert snapshot.direction_score > 0.99
    assert 0 < snapshot.confidence < 1


def test_duplicate_record_id_is_rejected():
    record = release("same", {"USD": 0.5})
    with pytest.raises(ValueError, match="record_id duplique"):
        score_fundamentals("USDJPY", [record, record], as_of=NOW)


def test_scheduled_event_cannot_carry_direction():
    with pytest.raises(ValueError, match="ne porte pas de direction"):
        FundamentalRecord(
            record_id="bad",
            kind="scheduled_event",
            category="central_bank",
            event_at=NOW,
            available_at=NOW,
            factors={"USD": 1.0},
        )


def test_loader_and_cli_write_shadow_snapshot(tmp_path):
    source = tmp_path / "observations.ndjson"
    output = tmp_path / "snapshot.json"
    raw = {
        "schema_version": 1,
        "record_id": "us-cpi",
        "kind": "release",
        "category": "inflation",
        "event_at": "2026-08-20T11:00:00Z",
        "available_at": "2026-08-20T11:00:02Z",
        "factors": {"USD": 0.8, "US_TECH": -0.4},
        "confidence": 0.9,
        "source": "fixture",
    }
    source.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    assert len(load_records(source)) == 1
    assert (
        main(
            [
                "--observations",
                str(source),
                "--output",
                str(output),
                "--symbols",
                "EURUSD",
                "USTECH",
                "--as-of",
                NOW.isoformat(),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "SHADOW_ONLY"
    assert payload["snapshots"][0]["would_block"] is False
    assert payload["snapshots"][1]["would_reduce"] is False
