from datetime import datetime, timedelta, timezone

import pytest

import titanium.hermes_cortex as cortex
from titanium.position_sentiment import append_record, build_review, confirm_fear, pending_reviews

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("observed", [None, "invalid", NOW.isoformat()[:-6],
                                     (NOW - timedelta(seconds=241)).isoformat(),
                                     (NOW + timedelta(seconds=1)).isoformat()])
def test_fear_rejects_missing_stale_or_inconsistent_snapshot(observed):
    verdict = {
        "request_ref": "new", "state": "FEAR", "confidence": 0.9,
        "model_version": cortex.HERMES_MODEL_VERSION,
        "rendered_at": NOW.isoformat(), "observed_at": observed,
    }
    result = confirm_fear(verdict, last_ref="old", previous_streak=1, now=NOW)
    assert not result.should_exit
    assert result.streak == 0


def test_fear_rejects_future_rendering():
    verdict = {
        "request_ref": "new", "state": "FEAR", "confidence": 0.9,
        "model_version": cortex.HERMES_MODEL_VERSION,
        "rendered_at": (NOW + timedelta(seconds=1)).isoformat(),
        "observed_at": NOW.isoformat(),
    }
    assert not confirm_fear(verdict, last_ref="old", previous_streak=1, now=NOW).should_exit


def test_position_analysis_preserves_snapshot_age(monkeypatch):
    observed = (NOW - timedelta(seconds=500)).isoformat()
    monkeypatch.setattr(cortex, "_evidence_by_symbol", lambda _: {})
    captured = {}

    def answer(prepared, *_):
        captured.update(prepared[0])
        return {"new": {"state": "FEAR", "confidence": 0.9}}

    monkeypatch.setattr(cortex, "_ask_par_lots", answer)
    result = cortex.analyse_positions([{
        "request_ref": "new", "ticket": "1", "symbol": "EURUSD", "side": 1,
        "observed_at": observed,
    }])[0]
    assert result.get("observed_at") == observed
    assert captured.get("observed_at") == observed
    assert not confirm_fear(
        result, last_ref="old", previous_streak=1,
        now=datetime.fromisoformat(result["rendered_at"]),
    ).should_exit


def test_pending_reviews_rejects_future_snapshot(tmp_path):
    requests = tmp_path / "requests.ndjson"
    row = build_review(ticket="1", symbol="EURUSD", side=1, entry=1.1, current=1.1,
                       sl=1.0, tp=1.2, r_unit=0.1, fav_r=0, peak_fav_r=0, mae_r=0,
                       observed_at=(NOW + timedelta(seconds=1)).isoformat())
    append_record(requests, row)
    assert pending_reviews(requests, tmp_path / "verdicts.ndjson", now=NOW) == []


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -1, 2])
def test_position_analysis_rejects_invalid_confidence(monkeypatch, confidence):
    monkeypatch.setattr(cortex, "_evidence_by_symbol", lambda _: {})
    monkeypatch.setattr(cortex, "_ask", lambda *_, **_kwargs: {
        "verdicts": [{"request_ref": "new", "state": "FEAR", "confidence": confidence}],
    })
    with pytest.raises(cortex.HermesCortexUnavailable, match="confiance Hermes"):
        cortex.analyse_positions([{
            "request_ref": "new", "ticket": "1", "symbol": "EURUSD", "side": 1,
            "observed_at": NOW.isoformat(),
        }])
