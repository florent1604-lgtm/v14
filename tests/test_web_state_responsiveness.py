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

    assert result["provider"] == "ollama-local"
    assert result["deep_model"] == "qwen3.5:2b"
    assert result["cortex_primary"] == "hermes-cortex/qwen3.5:2b"
    assert result["cortex_mode"] == "async_advisory"
    assert result["fallback_provider"] == "ollama"
    assert result["fallback_model"] == "qwen2.5:7b"


def test_scan_traite_la_crypto_comme_un_marche_continu(monkeypatch, tmp_path):
    from titanium import edge, orchestrator
    from titanium.data import mt5_vendor
    from titanium.features import builder
    from titanium.gates import confluence_gate

    vus = []
    monkeypatch.setattr(state, "_config", lambda: {"results_dir": tmp_path / "runs"})
    monkeypatch.setattr(state, "account", lambda: {"equity": 1000.0, "currency": "EUR"})
    monkeypatch.setattr(mt5_vendor, "get_rates", lambda *_args, **_kwargs: object())

    def construire(*_args, **kwargs):
        vus.append(kwargs.get("marche_continu"))
        return {"_trace": {}, "setup_side": 0, "setup_family": "", "trend": 0}

    monkeypatch.setattr(builder, "build_feats", construire)
    monkeypatch.setattr(builder, "risk_context_from", lambda *_a, **_k: {})
    decision = SimpleNamespace(gates=[])
    monkeypatch.setattr(confluence_gate, "evaluate", lambda *_a, **_k: decision)
    sortie = SimpleNamespace(
        gate_verdict="WAIT", gate_code="WAIT_NO_SETUP", reason="WAIT_NO_SETUP",
        stopped_at="gates", risk_verdict="", risk_money=0.0,
        stop_distance=None, conviction=0.0, trace=[],
    )
    monkeypatch.setattr(orchestrator, "run_once", lambda *_a, **_k: sortie)
    monkeypatch.setattr(edge, "context_from_feats", lambda *_a, **_k: "ctx")
    monkeypatch.setattr(
        edge.EdgeBook,
        "verdict_for",
        lambda *_a, **_k: SimpleNamespace(
            edge_ok=None, samples=0, expectancy_r=0.0, reason="inconnu",
        ),
    )

    state.scan(["BTCUSD", "EURUSD"])

    assert vus == [True, False]


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
