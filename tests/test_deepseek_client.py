from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_from_env_requires_key_without_exposing_environment(monkeypatch):
    from titanium.deepseek_client import DeepSeekClient, DeepSeekConfigurationError

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(DeepSeekConfigurationError, match="DEEPSEEK_API_KEY absente"):
        DeepSeekClient.from_env()


def test_client_uses_official_endpoint_model_and_parses_json():
    from titanium.deepseek_client import DeepSeekClient

    captured = {}

    def transport(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"action":"WAIT"}'))],
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=7,
                prompt_cache_hit_tokens=96,
                prompt_cache_miss_tokens=24,
            ),
        )

    client = DeepSeekClient("secret-value", transport=transport)
    completion = client.complete_json("volatile", system="stable")

    assert completion.payload == {"action": "WAIT"}
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["api_key"] == "secret-value"
    assert captured["messages"] == [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "volatile"},
    ]
    assert completion.usage.to_dict() == {
        "input_tokens": 120,
        "output_tokens": 7,
        "cache_hit_tokens": 96,
        "cache_miss_tokens": 24,
    }


def test_usage_accepts_openai_cached_token_shape():
    from titanium.deepseek_client import DeepSeekUsage

    usage = SimpleNamespace(
        prompt_tokens=80,
        completion_tokens=5,
        prompt_tokens_details=SimpleNamespace(cached_tokens=64),
    )
    assert DeepSeekUsage.from_response(usage).to_dict() == {
        "input_tokens": 80,
        "output_tokens": 5,
        "cache_hit_tokens": 64,
        "cache_miss_tokens": 16,
    }


def test_provider_error_never_contains_key_or_response_body():
    from titanium.deepseek_client import DeepSeekClient, DeepSeekUnavailable

    key = "sk-sensitive-deepseek-value"

    class ProviderError(RuntimeError):
        status_code = 429

    def transport(**_kwargs):
        raise ProviderError(f"quota refused for {key}: private provider body")

    client = DeepSeekClient(key, transport=transport)
    with pytest.raises(DeepSeekUnavailable) as caught:
        client.complete_json("prompt")

    message = str(caught.value)
    assert message == "DeepSeek HTTP 429"
    assert key not in message
    assert "private provider body" not in message


def test_invalid_json_fails_closed():
    from titanium.deepseek_client import DeepSeekClient, DeepSeekUnavailable

    def transport(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))],
            usage=None,
        )

    with pytest.raises(DeepSeekUnavailable, match="JSON invalide"):
        DeepSeekClient("secret", transport=transport).complete_json("prompt")
