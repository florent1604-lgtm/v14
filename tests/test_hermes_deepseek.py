from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_circuit(monkeypatch):
    from titanium import hermes_cortex as hc

    hc._reset_circuits()
    monkeypatch.setitem(hc._DERNIER_APPEL, "at", 0.0)
    monkeypatch.setattr(hc, "HERMES_INTERVALLE_MIN_S", 0.0)


def test_deepseek_provider_returns_payload_and_journals_cache(tmp_path, monkeypatch):
    from titanium import hermes_cortex as hc
    from titanium.deepseek_client import DeepSeekUsage

    class FakeClient:
        def complete_json(self, prompt, *, system):
            assert prompt == "candidate"
            assert "DEMO" in system
            return SimpleNamespace(
                payload={"verdicts": []},
                usage=DeepSeekUsage(100, 8, 80, 20),
                duration_ms=123,
            )

    monkeypatch.setattr(hc, "HERMES_PROVIDER", "deepseek-api")
    monkeypatch.setattr(hc, "DEEPSEEK_USAGE_LOG", tmp_path / "usage.ndjson")
    monkeypatch.setattr(
        "titanium.deepseek_client.DeepSeekClient.from_env",
        classmethod(lambda _cls, **_kwargs: FakeClient()),
    )

    assert hc._ask("candidate") == {"verdicts": []}
    row = json.loads((tmp_path / "usage.ndjson").read_text(encoding="utf-8"))
    assert row == {
        "provider": "deepseek-api",
        "model": "deepseek-v4-flash",
        "duration_ms": 123,
        "input_tokens": 100,
        "output_tokens": 8,
        "cache_hit_tokens": 80,
        "cache_miss_tokens": 20,
    }


def test_deepseek_failure_opens_existing_circuit_without_secret(monkeypatch):
    from titanium import hermes_cortex as hc
    from titanium.deepseek_client import DeepSeekUnavailable

    key = "sk-secret-must-not-leak"

    class FakeClient:
        def complete_json(self, _prompt, *, system):
            raise DeepSeekUnavailable(f"DeepSeek HTTP 429 {key}")

    monkeypatch.setattr(hc, "HERMES_PROVIDER", "deepseek-api")
    monkeypatch.setattr(
        "titanium.deepseek_client.DeepSeekClient.from_env",
        classmethod(lambda _cls, **_kwargs: FakeClient()),
    )

    with pytest.raises(hc.HermesCortexUnavailable) as caught:
        hc._ask("candidate")

    assert key not in str(caught.value)
    assert hc.circuit_status()["available"] is False


def test_circuit_status_exposes_official_deepseek_identity(monkeypatch):
    from titanium import hermes_cortex as hc

    monkeypatch.setattr(hc, "HERMES_PROVIDER", "deepseek-api")
    monkeypatch.setattr(hc, "HERMES_MODEL", "deepseek-v4-flash")
    status = hc.circuit_status()
    assert status["provider"] == "deepseek-api"
    assert status["model"] == "deepseek-v4-flash"

