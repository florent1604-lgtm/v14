"""Client DeepSeek borné pour le cortex V14.

Le module ne charge jamais ``.env``. Le processus appelant fournit la clé via
son environnement, localement ou depuis un Secret Kubernetes.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_SYSTEM_PROMPT = (
    "Tu es le cortex de Titanium V14 sur MT5 DEMO. "
    "Tu analyses uniquement les candidats scelles fournis et reponds en JSON."
)


class DeepSeekConfigurationError(RuntimeError):
    """Configuration requise absente ou incohérente."""


class DeepSeekUnavailable(RuntimeError):
    """Appel fournisseur impossible ou réponse inutilisable."""


def _field(value: Any, name: str, default: Any = 0) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    direct = getattr(value, name, None)
    if direct is not None:
        return direct
    extra = getattr(value, "model_extra", None)
    return extra.get(name, default) if isinstance(extra, dict) else default


@dataclass(frozen=True)
class DeepSeekUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    @classmethod
    def from_response(cls, usage: Any) -> DeepSeekUsage:
        input_tokens = max(0, int(_field(usage, "prompt_tokens", 0) or 0))
        output_tokens = max(0, int(_field(usage, "completion_tokens", 0) or 0))
        hit = int(_field(usage, "prompt_cache_hit_tokens", 0) or 0)
        miss_value = _field(usage, "prompt_cache_miss_tokens", None)
        if hit <= 0:
            details = _field(usage, "prompt_tokens_details", None)
            hit = int(_field(details, "cached_tokens", 0) or 0)
        hit = max(0, min(input_tokens, hit))
        miss = input_tokens - hit if miss_value is None else int(miss_value or 0)
        return cls(input_tokens, output_tokens, hit, max(0, miss))

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
        }


@dataclass(frozen=True)
class DeepSeekCompletion:
    payload: dict[str, Any]
    usage: DeepSeekUsage
    duration_ms: int


Transport = Callable[..., Any]


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        timeout_s: float = 90.0,
        transport: Transport | None = None,
    ) -> None:
        if not str(api_key).strip():
            raise DeepSeekConfigurationError("DEEPSEEK_API_KEY absente")
        self._api_key = str(api_key)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = max(1.0, float(timeout_s))
        self._transport = transport or self._openai_transport

    @classmethod
    def from_env(cls, **kwargs: Any) -> DeepSeekClient:
        return cls(os.environ.get("DEEPSEEK_API_KEY", ""), **kwargs)

    @staticmethod
    def _openai_transport(**kwargs: Any) -> Any:
        from openai import OpenAI

        client = OpenAI(
            api_key=kwargs["api_key"],
            base_url=kwargs["base_url"],
            timeout=kwargs["timeout_s"],
            max_retries=1,
        )
        return client.chat.completions.create(
            model=kwargs["model"],
            messages=kwargs["messages"],
            response_format={"type": "json_object"},
            temperature=0,
            stream=False,
        )

    @staticmethod
    def _parse_json(content: Any) -> dict[str, Any]:
        text = str(content or "").strip()
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DeepSeekUnavailable("DeepSeek JSON invalide") from exc
        if not isinstance(parsed, dict):
            raise DeepSeekUnavailable("DeepSeek JSON invalide")
        return parsed

    def complete_json(
        self,
        prompt: str,
        *,
        system: str = DEFAULT_SYSTEM_PROMPT,
    ) -> DeepSeekCompletion:
        messages = [
            {"role": "system", "content": str(system)},
            {"role": "user", "content": str(prompt)},
        ]
        started = time.perf_counter()
        try:
            response = self._transport(
                api_key=self._api_key,
                base_url=self.base_url,
                model=self.model,
                messages=messages,
                timeout_s=self.timeout_s,
            )
            content = response.choices[0].message.content
            payload = self._parse_json(content)
        except DeepSeekUnavailable:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            suffix = f" HTTP {int(status)}" if isinstance(status, int) else " indisponible"
            raise DeepSeekUnavailable(f"DeepSeek{suffix}") from exc
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        return DeepSeekCompletion(
            payload=payload,
            usage=DeepSeekUsage.from_response(getattr(response, "usage", None)),
            duration_ms=duration_ms,
        )
