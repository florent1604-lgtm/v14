from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from titanium.organism.contracts import MODEL_VERSION
from titanium.position_sentiment import (
    append_record,
    build_review,
    confirm_fear,
    pending_reviews,
)

NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def verdict(ref: str, state: str = "FEAR", confidence: float = 0.9,
            rendered_at: datetime = NOW) -> dict:
    return {
        "request_ref": ref,
        "state": state,
        "confidence": confidence,
        "rendered_at": rendered_at.isoformat(),
        "model_version": MODEL_VERSION,
    }


def review(ticket: str, observed_at: datetime) -> dict:
    return build_review(
        ticket=ticket,
        symbol="EURUSD",
        side=1,
        entry=1.10,
        current=1.101,
        sl=1.09,
        tp=1.12,
        r_unit=0.01,
        fav_r=0.1,
        peak_fav_r=0.3,
        mae_r=-0.2,
        observed_at=observed_at.isoformat(),
    )


def test_deux_verdicts_distincts_sont_requis_pour_sortir():
    first = confirm_fear(verdict("a"), last_ref="", previous_streak=0, now=NOW)
    assert first.streak == 1
    assert first.should_exit is False

    duplicate = confirm_fear(
        verdict("a"), last_ref=first.last_ref,
        previous_streak=first.streak, now=NOW,
    )
    assert duplicate.streak == 1
    assert duplicate.should_exit is False

    second = confirm_fear(
        verdict("b"), last_ref=first.last_ref,
        previous_streak=first.streak, now=NOW,
    )
    assert second.streak == 2
    assert second.should_exit is True


def test_verdict_calme_ou_perime_reinitialise_la_peur():
    calm = confirm_fear(
        verdict("b", state="CALM"), last_ref="a", previous_streak=1, now=NOW,
    )
    assert calm.streak == 0
    assert calm.should_exit is False

    stale = confirm_fear(
        verdict("c", rendered_at=NOW - timedelta(minutes=10)),
        last_ref="a", previous_streak=1, now=NOW,
    )
    assert stale.streak == 0
    assert stale.reason == "VERDICT_PERIME"


def test_la_file_ne_rend_que_le_dernier_snapshot_non_traite(tmp_path):
    requests = tmp_path / "requests.ndjson"
    verdicts = tmp_path / "verdicts.ndjson"
    old = review("1", NOW - timedelta(seconds=90))
    recent = review("1", NOW - timedelta(seconds=30))
    other = review("2", NOW - timedelta(seconds=20))
    assert append_record(requests, old)
    assert append_record(requests, recent)
    assert append_record(requests, other)
    assert append_record(verdicts, {"request_ref": other["request_ref"]})

    pending = pending_reviews(requests, verdicts, now=NOW)
    assert [row["request_ref"] for row in pending] == [recent["request_ref"]]


def test_glm_singleton_sans_reference_est_rattache_sans_ambiguite(monkeypatch):
    import titanium.fundamental_intelligence as fi

    item = review("7", NOW)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            answer = {
                "verdicts": [{
                    "request_ref": f"POSITION {item['request_ref']} EURUSD long",
                    "state": "FEAR",
                    "confidence": 0.88,
                    "reason": "rupture",
                }],
            }
            return json.dumps({"response": json.dumps(answer)}).encode()

    monkeypatch.setattr(
        fi,
        "collect",
        lambda _symbol: [fi.Evidence("source-a", "a"), fi.Evidence("source-b", "b")],
    )
    monkeypatch.setattr(fi.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    result = fi.analyse_positions([item])

    assert result[0]["request_ref"] == item["request_ref"]
    assert result[0]["state"] == "FEAR"
    assert result[0]["confidence"] == 0.88
