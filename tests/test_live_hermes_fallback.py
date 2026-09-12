from __future__ import annotations

import tools.live_demo as live_demo
from titanium.avis import Demande
from titanium.live_memory import MemoryVerdict
from titanium.organism.memory import CentralMemory


def test_mode_sans_cortex_utilise_une_conviction_neutre_sans_consulter_hermes(
    monkeypatch,
):
    monkeypatch.setattr(live_demo, "ACTIVER_CORTEX", False)
    monkeypatch.setattr(
        live_demo,
        "_demander_avis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Hermes ne doit pas etre consulte")
        ),
    )

    allowed, conviction, reason = live_demo._autorisation_et_conviction(
        "BTCUSD", {}, object(), object(), object(),
    )

    assert allowed is True
    assert conviction == 0.5
    assert reason == "CORTEX_DESACTIVE"


def test_live_ne_fait_aucun_appel_hermes_synchrone(tmp_path, monkeypatch):
    class EdgeMemory:
        @staticmethod
        def verdict(_symbol, _context):
            return MemoryVerdict("ALLOW", "edge positif", 100, 0.2, 1.4)

        @staticmethod
        def record(*_args):
            return None

    memory = CentralMemory(tmp_path / "core.sqlite3", tmp_path / "alerts.ndjson")
    monkeypatch.setattr(live_demo, "_MEMOIRE_LIVE", EdgeMemory())
    monkeypatch.setattr(live_demo, "NOYAU_CENTRAL", memory)
    monkeypatch.setattr(
        live_demo, "_contexte_exact",
        lambda *_args: "BTCUSD|long|continuation|4p",
    )
    monkeypatch.setattr(
        "titanium.hermes_cortex.analyse_entries",
        lambda _payloads: (_ for _ in ()).throw(
            AssertionError("appel Hermes interdit dans la boucle live")
        ),
        raising=False,
    )
    identity = Demande(
        "BTCUSD", 1, verdict="ENTER", code="OK", piliers=4,
        famille="continuation", bar_time="2026-09-05T18:20:00+00:00",
        engine_context="BTCUSD|long|continuation|4p",
    ).sceller()

    ok, reason = live_demo._garde_intelligente(
        "BTCUSD", 1, {"_trace": {"timeframe": "M1", "higher_timeframe": "H1"}}, identity,
    )

    assert ok is False
    assert "CORTEX_POLICY_MISSING" in reason
