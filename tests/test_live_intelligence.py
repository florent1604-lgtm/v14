from __future__ import annotations

import hashlib
import json

import pytest

from titanium import fundamental_intelligence as fi
from titanium.avis import Demande
from titanium.execution.live_loss_guard import LiveLossVerdict
from titanium.live_memory import MemoryVerdict, ReplayEdgeMemory
from titanium.organism.memory import CentralMemory


def _canonical(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _artifact(root, values):
    raw_dir = root / "results" / "rejeu_univers_brut" / "TEST"
    summary_path = root / "results" / "rejeu_univers" / "TEST.json"
    raw_dir.mkdir(parents=True)
    summary_path.parent.mkdir(parents=True)
    raw = b"".join(
        json.dumps({"split": "verification", "net_r": value,
                    "context": "TEST|long|reversal|3p"}).encode() + b"\n"
        for value in values
    )
    summary = json.dumps({"verification": {"n": len(values)}}).encode()
    (raw_dir / "trades.ndjson").write_bytes(raw)
    summary_path.write_bytes(summary)
    manifest = {
        "artifact_type": "v14.offline_replay.trades", "schema_version": 2,
        "symbol": "TEST",
        "trades": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        "summary": {"name": summary_path.name, "bytes": len(summary),
                    "sha256": hashlib.sha256(summary).hexdigest()},
    }
    manifest["manifest_sha256"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    (raw_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_replay_memory_allows_only_positive_sealed_edge(tmp_path):
    _artifact(tmp_path, [0.4, 0.2, -0.1])
    memory = ReplayEdgeMemory(tmp_path, min_context=3, min_symbol=3)
    verdict = memory.verdict("TEST", "TEST|long|reversal|3p")
    assert verdict.action == "ALLOW"
    assert verdict.expectancy_r > 0.05


def _trois_pertes(root):
    trades = root / "results" / "trades.ndjson"
    trades.write_text("".join(
        json.dumps({"context": "TEST|long|reversal|3p", "source": "live",
                    "pnl_r": -1.0}) + "\n" for _ in range(3)), encoding="utf-8")


def test_replay_memory_blocks_three_recent_losses_without_measured_edge(tmp_path):
    """La serie perdante suspend le contexte quand rien ne la contredit."""
    _artifact(tmp_path, [0.1, -0.4, -0.2])
    _trois_pertes(tmp_path)
    memory = ReplayEdgeMemory(tmp_path, min_context=3, min_symbol=3)
    assert memory.verdict("TEST", "TEST|long|reversal|3p").action == "BLOCK"


def test_three_losses_do_not_override_a_positive_measured_edge(tmp_path):
    """Une serie perdante ne prime pas sur une esperance mesuree positive.

    `_recent_live` n'a pas de fenetre de recence : les trois pertes peuvent
    dater de plusieurs semaines. Les laisser primer suspendait, au 08/09/2026,
    21 contextes a esperance positive sur les 24 suspendus.
    """
    _artifact(tmp_path, [0.4, 0.2, -0.1])
    _trois_pertes(tmp_path)
    memory = ReplayEdgeMemory(tmp_path, min_context=3, min_symbol=3)
    assert memory.verdict("TEST", "TEST|long|reversal|3p").action == "ALLOW"


def test_three_losses_still_block_when_the_sample_is_too_short(tmp_path):
    """Echantillon insuffisant : aucune esperance ne peut contredire la serie."""
    _artifact(tmp_path, [0.4, 0.2, -0.1])
    _trois_pertes(tmp_path)
    memory = ReplayEdgeMemory(tmp_path, min_context=60, min_symbol=100)
    assert memory.verdict("TEST", "TEST|long|reversal|3p").action == "BLOCK"


def test_fundamental_analysis_waits_when_evidence_is_insufficient(monkeypatch):
    monkeypatch.setattr(fi, "collect", lambda _symbol: [fi.Evidence("one", "fact")])
    assert fi.analyse("EURUSD", 1, "setup")["action"] == "WAIT"


def test_json_object_accepte_un_objet_complet_et_refuse_la_troncature():
    assert fi._json_object('préface {"action":"ALLOW"} fin') == {
        "action": "ALLOW",
    }
    assert fi._json_object('```json\n{"action":"WAIT"}\n```') == {
        "action": "WAIT",
    }
    with pytest.raises(json.JSONDecodeError):
        fi._json_object('{"action":"ALLOW"')


def test_fundamental_batch_impose_le_schema_et_rattache_chaque_reference(
    monkeypatch,
):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            answer = {
                "verdicts": [
                    {"decision_ref": "ref-b", "action": "BLOCK",
                     "confidence": 0.9, "summary": "choc"},
                    {"decision_ref": "ref-a", "action": "ALLOW",
                     "confidence": 0.7, "summary": "neutre"},
                ],
            }
            return json.dumps({"response": json.dumps(answer)}).encode()

    def fake_urlopen(request, **_kwargs):
        captured.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(
        fi, "collect",
        lambda _symbol: [fi.Evidence("source-a", "a"), fi.Evidence("source-b", "b")],
    )
    monkeypatch.setattr(fi.urllib.request, "urlopen", fake_urlopen)
    requests = [
        {"symbol": "EURUSD", "side": 1, "mechanical_summary": "setup",
         "decision_ref": "ref-a"},
        {"symbol": "BTCUSD", "side": -1, "mechanical_summary": "setup",
         "decision_ref": "ref-b"},
    ]
    results = fi.analyse_batch(requests)

    assert [row["action"] for row in results] == ["ALLOW", "BLOCK"]
    assert captured["model"] == "qwen3.5:2b"
    assert captured["think"] is False
    assert isinstance(captured["format"], dict)
    assert captured["options"]["num_predict"] > 60
    verdict_schema = captured["format"]["properties"]["verdicts"]
    assert verdict_schema["minItems"] == 2
    assert verdict_schema["maxItems"] == 2
    enum = verdict_schema["items"][
        "properties"
    ]["decision_ref"]["enum"]
    assert enum == ["ref-a", "ref-b"]


def test_fundamental_batch_accepte_le_verdict_unitaire_aplati(monkeypatch):
    decision_ref = "flattened-ref"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            answer = {
                "decision_ref": decision_ref,
                "action": "ALLOW",
                "confidence": 0.8,
                "summary": "contexte coherent",
            }
            return json.dumps({"response": json.dumps(answer)}).encode()

    monkeypatch.setattr(
        fi, "collect",
        lambda _symbol: [fi.Evidence("source-a", "a"), fi.Evidence("source-b", "b")],
    )
    monkeypatch.setattr(fi.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    result = fi.analyse_batch([{
        "symbol": "EURUSD", "side": 1, "mechanical_summary": "setup",
        "decision_ref": decision_ref,
    }])[0]

    assert result["action"] == "ALLOW"
    assert result["confidence"] == 0.8


def test_qwen_local_traite_les_entrees_et_positions_une_par_une():
    from tools import analystes

    assert analystes.ENTRY_BATCH_SIZE == 2
    assert fi.POSITION_BATCH_SIZE == 1


def test_fundamental_batch_tronque_reste_wait_et_journalise(monkeypatch):
    failures = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return json.dumps({
                "response": '{"verdicts":[',
                "done_reason": "length",
                "eval_count": 60,
            }).encode()

    monkeypatch.setattr(
        fi, "collect",
        lambda _symbol: [fi.Evidence("source-a", "a"), fi.Evidence("source-b", "b")],
    )
    monkeypatch.setattr(fi.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        fi, "_record_glm_failure",
        lambda context, raw, outer, exc: failures.append(
            (context, raw, outer.get("done_reason"), type(exc).__name__)
        ),
    )

    result = fi.analyse_batch([
        {"symbol": "EURUSD", "side": 1, "mechanical_summary": "setup",
         "decision_ref": "truncated-ref"},
    ])[0]

    assert result["action"] == "WAIT"
    assert "JSONDecodeError" in result["summary"]
    assert failures == [(
        "entry-batch:truncated-ref", '{"verdicts":[', "length", "JSONDecodeError",
    )]


def test_demo_engine_retablit_be_sans_trailing_et_active_sorties_adaptatives():
    from tools import live_demo

    assert live_demo.MODIFIER_STOPS_EXISTANTS is True
    assert live_demo.ACTIVER_TRAILING is False
    assert live_demo.GERER_SORTIES_ADAPTATIVES is True


def test_fred_is_optional_and_asset_aware(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert fi._fred("XAUUSD") == []

    monkeypatch.setenv("FRED_API_KEY", "test-key")
    monkeypatch.setattr("tradingagents.dataflows.fred.get_macro_data",
                        lambda indicator, *_args: (
                            f"## FRED: {indicator}\n**Latest:** 4.25 (2026-08-01)"))
    sources = fi._fred("XAUUSD")
    assert {item.source for item in sources} == {
        "FRED:10y_treasury", "FRED:dollar_index",
        "FRED:inflation_expectations",
    }


def test_eia_is_optional_and_uses_v2_series_route(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    assert fi._eia("USOIL") == []

    monkeypatch.setenv("EIA_API_KEY", "test-key")
    seen = {}

    def fake_get(url):
        seen["url"] = url
        return json.dumps({"response": {"data": [
            {"period": "2026-08-26", "value": "81.2", "units": "$/b"},
        ]}}).encode()

    monkeypatch.setattr(fi, "_get", fake_get)
    result = fi._eia("USOIL")
    assert result[0].source == "EIA:PET.RWTC.D"
    assert "/v2/seriesid/PET.RWTC.D?" in seen["url"]
    assert "test-key" in seen["url"]


def test_evidence_is_balanced_across_sources():
    evidence = [fi.Evidence("rss", str(i)) for i in range(5)] + [
        fi.Evidence("FRED", "macro"), fi.Evidence("EIA", "energy")]
    chosen = fi._balanced(evidence, limit=4)
    assert [item.source for item in chosen[:3]] == ["rss", "FRED", "EIA"]


def test_raw_proposal_cannot_authorize_without_fresh_hermes_policy(tmp_path, monkeypatch):
    from tools import live_demo

    class EdgeMemory:
        @staticmethod
        def verdict(_symbol, _context):
            return MemoryVerdict("ALLOW", "edge positif", 100, 0.2, 1.4)

        @staticmethod
        def record(*_args):
            return None

    memory = CentralMemory(tmp_path / "core.sqlite3", tmp_path / "alerts.ndjson")
    monkeypatch.setattr(live_demo, "NOYAU_CENTRAL", memory)
    monkeypatch.setattr(live_demo, "_MEMOIRE_LIVE", EdgeMemory())
    monkeypatch.setattr(live_demo, "_contexte_exact",
                        lambda *_args: "XAUUSD|long|continuation|3p")
    identity = Demande(
        "XAUUSD", 1, verdict="ENTER", code="OK", piliers=3,
        famille="continuation", bar_time="2026-08-27T12:00:00+00:00",
        engine_context="XAUUSD|long|continuation|3p",
    ).sceller()
    memory.record_proposal(identity, {
        **identity.to_dict(), "evidence_digest": "e" * 64,
        "action": "ALLOW", "confidence": 0.7, "summary": "accord",
        "sources": ["FRED"], "rendered_at": "2026-08-27T12:01:00+00:00",
    })
    feats = {"_trace": {"timeframe": "M15", "higher_timeframe": "H1"}}
    assert live_demo._garde_intelligente("XAUUSD", 1, feats, identity)[0] is False

    next_bar = Demande(
        "XAUUSD", 1, verdict="ENTER", code="OK", piliers=3,
        famille="continuation", bar_time="2026-08-27T12:15:00+00:00",
        engine_context="XAUUSD|long|continuation|3p",
    ).sceller()
    ok, reason = live_demo._garde_intelligente("XAUUSD", 1, feats, next_bar)
    assert ok is False
    assert "CORTEX_POLICY_MISSING" in reason


def test_engine_gate_accepts_fresh_context_policy_without_waiting(tmp_path, monkeypatch):
    from titanium.organism.contracts import (
        CORTEX_DECISION_MODEL_VERSION,
        CORTEX_DECISION_PRODUCER,
    )
    from titanium.organism.cortex import build_cortex_policy
    from tools import live_demo

    class EdgeMemory:
        @staticmethod
        def verdict(_symbol, _context):
            return MemoryVerdict("ALLOW", "edge positif", 100, 0.2, 1.4)

        @staticmethod
        def record(*_args):
            return None

    context = "XAUUSD|long|continuation|3p"
    memory = CentralMemory(tmp_path / "core.sqlite3", tmp_path / "alerts.ndjson")
    monkeypatch.setattr(live_demo, "NOYAU_CENTRAL", memory)
    monkeypatch.setattr(live_demo, "_MEMOIRE_LIVE", EdgeMemory())
    monkeypatch.setattr(live_demo, "_contexte_exact", lambda *_args: context)
    previous = Demande(
        "XAUUSD", 1, verdict="ENTER", code="OK", piliers=3,
        famille="continuation", bar_time="2026-08-27T12:00:00+00:00",
        engine_context=context,
    ).sceller()
    current = Demande(
        "XAUUSD", 1, verdict="ENTER", code="OK", piliers=3,
        famille="continuation", bar_time="2026-08-27T12:15:00+00:00",
        engine_context=context,
    ).sceller()
    memory.record_policy(build_cortex_policy(
        previous,
        context_key=context + "|tf=M15>H1",
        action="ALLOW",
        confidence=0.7,
        summary="politique de regime",
        evidence_digest="e" * 64,
        decision_model_version=CORTEX_DECISION_MODEL_VERSION,
        producer=CORTEX_DECISION_PRODUCER,
    ))
    feats = {"_trace": {"timeframe": "M15", "higher_timeframe": "H1"}}
    ok, reason = live_demo._garde_intelligente("XAUUSD", 1, feats, current)
    assert ok is True
    assert "CORTEX_POLICY_EXACT" in reason


def test_engine_deposits_sealed_request_in_central_memory(tmp_path, monkeypatch):
    from tools import live_demo

    memory = CentralMemory(tmp_path / "core.sqlite3", tmp_path / "alerts.ndjson")
    requests = tmp_path / "requests.ndjson"
    monkeypatch.setattr(live_demo, "NOYAU_CENTRAL", memory)
    monkeypatch.setattr(live_demo, "AVIS_DEMANDES", requests)
    monkeypatch.setattr(live_demo, "_contexte_exact",
                        lambda *_args: "XAUUSD|long|continuation|3p")
    monkeypatch.setattr(live_demo, "_sante_resumee", lambda: "sain")
    out = type("Out", (), {"side": 1, "stop_distance": 10.0})()
    decision = type("Decision", (), {
        "verdict": "ENTER", "code": "OK", "gates": [],
        "setup_family": "continuation",
    })()
    cfg = type("Cfg", (), {"rr_ratio": 2.0})()
    feats = {"_trace": {
        "bar_time": "2026-08-27T12:00:00+00:00",
        "timeframe": "M15", "higher_timeframe": "H1",
        "price": 2050.0, "indicators": {"rsi": 55.0},
    }}
    identity = live_demo._demander_avis("XAUUSD", feats, out, decision, cfg)
    assert identity is not None
    row = json.loads(requests.read_text(encoding="utf-8"))
    assert row["decision_ref"] == identity.decision_ref
    assert row["context_digest"] == identity.context_digest
    assert memory.health()["events"] == 1


def test_worker_returns_proposal_to_same_central_identity(tmp_path, monkeypatch):
    from tools import analystes

    memory = CentralMemory(tmp_path / "core.sqlite3", tmp_path / "alerts.ndjson")
    monkeypatch.setattr(analystes, "CENTRAL_MEMORY", memory)
    monkeypatch.setattr(
        analystes,
        "_entry_loss_gate",
        lambda: LiveLossVerdict("ALLOW", "WITHIN_LOSS_LIMITS"),
    )
    monkeypatch.setattr(fi, "analyse", lambda *_args, **kwargs: {
        "action": "ALLOW", "confidence": 0.66, "summary": "macro neutre",
        "sources": ["FRED"], "evidence_digest": "e" * 64,
        "model_version": kwargs["model_version"],
        "prompt_version": kwargs["prompt_version"],
    })
    demande = Demande(
        "XAUUSD", 1, verdict="ENTER", code="OK", piliers=3,
        famille="continuation", bar_time="2026-08-27T12:00:00+00:00",
        engine_context="XAUUSD|long|continuation|3p",
    )
    identity = demande.sceller()
    _, avis, _, _ = analystes._traiter(demande)
    proposal, code = memory.proposal_for(identity)
    assert code == "BRAIN_PROPOSAL_EXACT"
    assert proposal["confidence"] == 0.66
    assert avis.decision_ref == identity.decision_ref


def test_worker_batch_publie_wait_si_hermes_est_indisponible(
    tmp_path, monkeypatch,
):
    from datetime import datetime, timedelta, timezone

    from tools import analystes

    memory = CentralMemory(tmp_path / "core.sqlite3", tmp_path / "alerts.ndjson")
    monkeypatch.setattr(analystes, "CENTRAL_MEMORY", memory)
    monkeypatch.setattr(
        analystes,
        "_entry_loss_gate",
        lambda: LiveLossVerdict("ALLOW", "WITHIN_LOSS_LIMITS"),
    )

    import titanium.hermes_cortex as hermes_cortex
    monkeypatch.setattr(
        hermes_cortex,
        "analyse_entries",
        lambda _payloads: (_ for _ in ()).throw(
            hermes_cortex.HermesCortexUnavailable("test panne")
        ),
    )
    demandes = [
        Demande(
            symbol, 1, verdict="ENTER", code="OK", piliers=3,
            famille="continuation",
            bar_time=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            demande_a=datetime.now(timezone.utc).isoformat(),
            engine_context=f"{symbol}|long|continuation|3p|tf=M1>H1",
        )
        for symbol in ("EURUSD", "BTCUSD")
    ]

    results = analystes._traiter_lot(demandes)

    assert [row[1].symbol for row in results] == ["EURUSD", "BTCUSD"]
    assert [row[1].action for row in results] == ["WAIT", "WAIT"]
    assert [row[1].conviction for row in results] == [0.0, 0.0]
    for demande in demandes:
        proposal, code = memory.proposal_for(demande.sceller())
        assert code == "BRAIN_PROPOSAL_EXACT"
        assert proposal["action"] == "WAIT"
        assert proposal["evidence_digest"]
        assert proposal["decision_model_version"] == "none"
        assert proposal["producer"] == "hermes-unavailable"


def test_worker_publishes_loss_block_without_calling_hermes(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tools import analystes

    memory = CentralMemory(tmp_path / "core.sqlite3", tmp_path / "alerts.ndjson")
    monkeypatch.setattr(analystes, "CENTRAL_MEMORY", memory)
    monkeypatch.setattr(
        analystes,
        "_entry_loss_gate",
        lambda: LiveLossVerdict(
            "BLOCK",
            "DAILY_LOSS_LIMIT",
            daily_trades=3,
            daily_net_r=-2.75,
            rolling_trades=8,
            rolling_net_r=-4.0,
        ),
    )

    import titanium.hermes_cortex as hermes_cortex

    monkeypatch.setattr(
        hermes_cortex,
        "analyse_entries",
        lambda _payloads: pytest.fail("Hermes ne doit pas etre appele sous coupe-circuit"),
    )
    demande = Demande(
        "BTCUSD",
        1,
        verdict="ENTER",
        code="OK",
        piliers=3,
        famille="continuation",
        bar_time=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        demande_a=datetime.now(timezone.utc).isoformat(),
        engine_context="BTCUSD|long|continuation|3p|tf=M1>H1",
    )

    [(returned, avis, _, sources)] = analystes._traiter_lot([demande])

    assert returned is demande
    assert avis.action == "BLOCK"
    assert avis.conviction == 1.0
    assert avis.source == "live-loss-guard"
    assert sources == ("live-loss-guard",)
    proposal, code = memory.proposal_for(demande.sceller())
    assert code == "BRAIN_PROPOSAL_EXACT"
    assert proposal["action"] == "BLOCK"
    assert proposal["producer"] == "live-loss-guard"


def test_cortex_context_is_timeframe_specific():
    from tools import live_demo

    feats = {"_trace": {"timeframe": "M1", "higher_timeframe": "H1"}}
    base = live_demo._contexte_exact("BTCUSD", feats, 1)
    assert live_demo._contexte_cortex("BTCUSD", feats, 1) == f"{base}|tf=M1>H1"
