import json
from datetime import datetime, timezone

import pytest

from titanium import fundamental_intelligence as fi
from titanium.organism.trading_knowledge import compact_observations, knowledge_for


@pytest.mark.parametrize("cls", ["crypto", "fx", "metaux", "energie", "indices", "actions", "unknown"])
def test_playbook_is_bounded_and_independent(cls):
    first = knowledge_for("UNKNOWN", cls)
    assert len(json.dumps(first)) < 2600
    assert first["strategies"]
    first["strategies"].clear()
    assert knowledge_for("UNKNOWN", cls)["strategies"]


def test_observations_preserve_edge_micro_and_jepa_without_nan():
    compact = compact_observations({"edge_samples": 40, "micro_venues": 3,
                                    "jepa_impulse_r": .25, "ltf_rsi_14": float("nan"),
                                    "unrelated": "a" * 100000})
    assert compact == {"edge_samples": 40., "micro_venues": 3., "jepa_impulse_r": .25}


def test_ecb_cross_uses_two_euro_rates_same_day(monkeypatch):
    urls = []
    def get(url):
        urls.append(url)
        return b"CURRENCY,TIME_PERIOD,OBS_VALUE\nUSD,2026-09-04,1.1\nJPY,2026-09-04,165\nJPY,2026-09-03,164\n"
    monkeypatch.setattr(fi, "_get", get)
    result = fi._ecb_fx("USDJPY")
    assert ".JPY+USD.EUR." in urls[0]
    assert len(result) == 1
    assert result[0].text == "USDJPY reference_daily=150"
    assert result[0].observed_at == "2026-09-04"


def test_ecb_does_not_query_crypto_or_unknown(monkeypatch):
    monkeypatch.setattr(fi, "_get", lambda _: pytest.fail("no FX series for crypto"))
    assert fi._ecb_fx("BTCUSD") == []


def test_crypto_retains_provider_observation_time(monkeypatch):
    urls = []
    def get(url):
        urls.append(url)
        return b'{"bitcoin":{"usd":80000,"last_updated_at":1788650000}}'
    monkeypatch.setattr(fi, "_get", get)
    result = fi._crypto("BTCUSD")
    assert "include_last_updated_at=true" in urls[0]
    assert result[0].observed_at


def test_freshness_does_not_confuse_fetch_and_observation():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    assert fi.evidence_freshness(fi.Evidence("CoinGecko", "x", "2020-01-01",
                                            now.isoformat()), now=now) == "STALE"
    assert fi.evidence_freshness(fi.Evidence("CoinGecko", "x"), now=now) == "UNKNOWN_TIME"
    assert fi.evidence_freshness(fi.Evidence("CoinGecko", "x", "2099-01-01"), now=now) == "FUTURE_TIME"
    assert fi.evidence_freshness(fi.Evidence("FRED:rates", "x", "2026-09-01"), now=now) == "CURRENT_CONTEXT"


def test_source_cache_shares_fetch_and_reports_failure_without_secret(monkeypatch):
    monkeypatch.setattr(fi, "_CACHE", {})
    monkeypatch.setattr(fi, "_SOURCE_STATUS", {})
    calls = []
    def fetch():
        calls.append(1)
        return [fi.Evidence("FederalReserve", "policy", "2026-09-04")]
    assert fi._source_cached("Fed", 300, fetch)[0].retrieved_at
    fi._source_cached("Fed", 300, fetch)
    assert len(calls) == 1
    assert fi.source_health()["Fed"]["state"] == "OK"
    def fail():
        raise RuntimeError("secret must not be emitted")
    assert fi._source_cached("test", 1, fail) == []
    assert "secret" not in json.dumps(fi.source_health())
