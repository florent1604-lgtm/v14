"""Bornes de latence du fournisseur et publication sans blocage de tete."""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

import pytest

import tradingagents.default_config as default_config_module
from tradingagents.graph.trading_graph import TradingAgentsGraph, _coerce_timeout


def _bare_graph(config):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = config
    return graph


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [(1, 1.0), (12.5, 12.5), ("90", 90.0)])
def test_timeout_accepts_positive_seconds(value, expected):
    assert _coerce_timeout(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, -1, "0", True, False, None, "abc"])
def test_timeout_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        _coerce_timeout(value)


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_timeout_is_forwarded_to_every_provider(provider):
    kwargs = _bare_graph({"llm_provider": provider, "llm_timeout": "45"})._get_provider_kwargs()
    assert kwargs["timeout"] == 45.0


@pytest.mark.unit
def test_timeout_is_omitted_when_unset():
    kwargs = _bare_graph({"llm_provider": "openai", "llm_timeout": None})._get_provider_kwargs()
    assert "timeout" not in kwargs


@pytest.mark.unit
def test_timeout_env_overlay(monkeypatch):
    for key in default_config_module.env_var_names():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TITANIUM_LLM_TIMEOUT", "75")
    config = importlib.reload(default_config_module).DEFAULT_CONFIG
    assert float(config["llm_timeout"]) == 75.0
    monkeypatch.delenv("TITANIUM_LLM_TIMEOUT", raising=False)
    importlib.reload(default_config_module)


@pytest.mark.unit
def test_analyst_worker_publishes_fast_result_first(monkeypatch, tmp_path):
    import tools.analystes as worker
    from titanium.avis import Avis

    slow = SimpleNamespace(symbol="SLOW", side=1, bar_time="t1", piliers=4,
                           total_piliers=4)
    fast = SimpleNamespace(symbol="FAST", side=1, bar_time="t2", piliers=4,
                           total_piliers=4)
    published = []

    monkeypatch.setattr(worker, "DEMANDES", tmp_path / "demandes.ndjson")
    monkeypatch.setattr(worker, "AVIS", tmp_path / "avis.ndjson")
    monkeypatch.setattr(worker, "purger", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(worker, "quota_epuise", lambda: 0.0)
    monkeypatch.setattr(worker, "demandes_en_attente", lambda *_args: [slow, fast])
    monkeypatch.setattr(worker, "journaliser_cout", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(worker, "enregistrer", lambda avis, _path: published.append(avis.symbol))

    def traiter(demande):
        time.sleep(0.08 if demande.symbol == "SLOW" else 0.01)
        avis = Avis(demande.symbol, 1, 0.5, "Hold", None, "",
                    demande.bar_time, "", "graphe")
        return demande, avis, 0.01, ("market",)

    monkeypatch.setattr(worker, "_traiter", traiter)

    assert worker.passage() == 2
    assert published == ["FAST", "SLOW"]
