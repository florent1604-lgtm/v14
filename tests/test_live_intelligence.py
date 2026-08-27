from __future__ import annotations

import hashlib
import json

from titanium import fundamental_intelligence as fi
from titanium.live_memory import ReplayEdgeMemory


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


def test_replay_memory_blocks_three_recent_losses(tmp_path):
    _artifact(tmp_path, [0.4, 0.2, -0.1])
    trades = tmp_path / "results" / "trades.ndjson"
    trades.write_text("".join(
        json.dumps({"context": "TEST|long|reversal|3p", "source": "live",
                    "pnl_r": -1.0}) + "\n" for _ in range(3)), encoding="utf-8")
    memory = ReplayEdgeMemory(tmp_path, min_context=3, min_symbol=3)
    assert memory.verdict("TEST", "TEST|long|reversal|3p").action == "BLOCK"


def test_fundamental_analysis_waits_when_evidence_is_insufficient(monkeypatch):
    monkeypatch.setattr(fi, "collect", lambda _symbol: [fi.Evidence("one", "fact")])
    assert fi.analyse("EURUSD", 1, "setup")["action"] == "WAIT"


def test_demo_engine_never_moves_existing_stops():
    from tools import live_demo

    assert live_demo.MODIFIER_STOPS_EXISTANTS is False
