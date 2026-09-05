from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from titanium.analysis import discriminants as analyse_discriminants
from titanium.web import state


def test_meta_affiche_hermes_comme_cortex_principal(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_config", lambda: {
        "llm_provider": "ollama",
        "deep_think_llm": "qwen2.5:7b",
        "quick_think_llm": "qwen2.5:3b",
        "results_dir": tmp_path,
    })

    result = state.meta()

    assert result["provider"] == "claude-code"
    assert result["deep_model"] == "claude-opus-5"
    assert result["cortex_primary"] == "hermes-cortex/claude-opus-5"
    assert result["cortex_mode"] == "async_advisory"
    assert result["fallback_provider"] == "ollama"
    assert result["fallback_model"] == "qwen2.5:7b"


def test_discriminants_ne_bloque_pas_le_dashboard(tmp_path, monkeypatch):
    source = tmp_path / "excursions.ndjson"
    source.write_text("preuve\n", encoding="utf-8")
    monkeypatch.setattr(state, "_config", lambda: {"results_dir": tmp_path / "runs"})
    monkeypatch.setattr(analyse_discriminants, "depuis_journal", lambda _p: [object()])

    libere = threading.Event()

    def analyser(_ech, *, n_permutations):
        assert n_permutations == 300
        assert libere.wait(timeout=2)
        indicateur = SimpleNamespace(to_dict=lambda: {"nom": "rsi"})
        return SimpleNamespace(
            suffisant=True,
            n_trades=1,
            n_gagnants=1,
            n_perdants=0,
            message="ok",
            discriminants=[indicateur],
        )

    monkeypatch.setattr(analyse_discriminants, "analyser", analyser)
    monkeypatch.setattr(state, "_DISCRIMINANTS_CACHE", {})
    monkeypatch.setattr(state, "_DISCRIMINANTS_BUILDING", False)

    debut = time.perf_counter()
    premier = state.discriminants()
    assert time.perf_counter() - debut < 0.5
    assert premier["pending"] is True

    libere.set()
    limite = time.monotonic() + 2
    while state._DISCRIMINANTS_BUILDING and time.monotonic() < limite:
        time.sleep(0.01)

    second = state.discriminants()
    assert second["pending"] is False
    assert second["top"] == [{"nom": "rsi"}]
