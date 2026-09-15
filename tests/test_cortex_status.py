from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from titanium.web import cortex_status

ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_snapshot_separe_edge_et_indisponibilite_hermes(tmp_path, monkeypatch):
    now = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(minutes=2)).isoformat()
    results = tmp_path / "results"
    _write(results / "live_memory.ndjson", [
        {"at": recent, "symbol": "BTCUSD", "context": "ctx-a",
         "action": "ALLOW", "reason": "contexte exact: edge V4 positif"},
        {"at": recent, "symbol": "ETHUSD", "context": "ctx-b",
         "action": "BLOCK", "reason": "contexte exact: edge insuffisant"},
        {"at": recent, "symbol": "ETHUSD", "context": "ctx-b",
         "action": "BLOCK", "reason": "contexte exact: edge insuffisant"},
    ])
    _write(results / "refus_live.ndjson", [
        {"at": recent, "code": "INTELLIGENCE_GATE",
         "detail": "memoire BLOCK: contexte exact: edge insuffisant"},
        {"at": recent, "code": "INTELLIGENCE_GATE",
         "detail": "noyau CORTEX_POLICY_MODEL_INVALID: autorisation absente"},
    ])
    _write(results / "avis_rendus.ndjson", [
        {"rendu_a": recent, "symbol": "BTCUSD", "action": "WAIT",
         "source": "hermes-unavailable",
         "resume": "HTTP 400: provider refused: credit balance is too low"},
    ])
    monkeypatch.setattr(cortex_status, "_port_open", lambda port: port != 8766)

    result = cortex_status.snapshot(root=tmp_path, now=now)

    assert result["status"] == "provider_refused"
    assert result["label"] == "Refus du fournisseur Hermes"
    assert result["memory"]["checks"] == 3
    assert result["memory"]["allow_rate"] == pytest.approx(1 / 3, abs=0.0001)
    assert result["memory"]["unique_contexts"] == 2
    assert result["memory"]["unique_block"] == 1
    assert result["refusals"]["categories"] == {
        "memory_block": 1, "policy_model_invalid": 1,
    }
    assert result["communication"]["hub"]["running"] is True
    assert result["communication"]["hermes_mcp"]["running"] is False


def test_snapshot_masque_les_secrets_et_ignore_les_mesures_perimees(
    tmp_path, monkeypatch,
):
    now = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=3)).isoformat()
    recent = (now - timedelta(minutes=1)).isoformat()
    results = tmp_path / "results"
    _write(results / "avis_rendus.ndjson", [
        {"rendu_a": old, "source": cortex_status.HERMES_SOURCE,
         "action": "ALLOW", "resume": "ancien"},
        {"rendu_a": recent, "source": "hermes-unavailable", "action": "WAIT",
         "resume": "API_KEY=top-secret-value-123456 panne fournisseur"},
    ])
    monkeypatch.setattr(cortex_status, "_port_open", lambda _port: False)

    result = cortex_status.snapshot(root=tmp_path, now=now)

    assert result["status"] == "unavailable"
    assert "top-secret" not in json.dumps(result)
    assert "[masque]" in result["last_result"]["summary"]


def test_tail_json_ne_parse_pas_la_premiere_ligne_tronquee(tmp_path):
    path = tmp_path / "events.ndjson"
    _write(path, [
        {"id": "tres-long-premier-evenement", "payload": "x" * 200},
        {"id": "conserve"},
    ])

    rows = cortex_status._tail_json(path, maximum_bytes=80)

    assert rows == [{"id": "conserve"}]


def test_tail_json_conserve_une_ligne_complete_a_la_borne(tmp_path):
    path = tmp_path / "events.ndjson"
    path.write_bytes(b'{"id":1}\n{"id":2}\n')
    assert cortex_status._tail_json(path, maximum_bytes=len(b'{"id":2}\n')) == [
        {"id": 2},
    ]


def test_snapshot_ignore_les_evenements_futurs(tmp_path, monkeypatch):
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    future = (now + timedelta(seconds=1)).isoformat()
    results = tmp_path / "results"
    _write(results / "avis_rendus.ndjson", [
        {"rendu_a": future, "source": cortex_status.HERMES_SOURCE, "action": "ALLOW"},
    ])
    _write(results / "live_memory.ndjson", [{"at": future, "action": "ALLOW"}])
    _write(results / "refus_live.ndjson", [{"at": future, "code": "INTELLIGENCE_GATE"}])
    monkeypatch.setattr(cortex_status, "_port_open", lambda _port: False)
    result = cortex_status.snapshot(root=tmp_path, now=now)
    assert result["status"] == "unknown"
    assert result["memory"]["checks"] == 0
    assert result["refusals"]["intelligence_gate"] == 0


def test_cortex_reconnait_le_refus_quota_normalise():
    status, _, _ = cortex_status._cortex_health([
        {"source": "hermes-unavailable", "resume": "provider usage/quota refusal"},
    ])
    assert status == "provider_refused"


def test_poste_expose_le_cortex_sans_route_d_execution():
    html = (ROOT / "tools" / "ui" / "poste.html").read_text(encoding="utf-8")
    script = (ROOT / "tools" / "ui" / "poste.js").read_text(encoding="utf-8")
    server = (ROOT / "tools" / "dashboard.py").read_text(encoding="utf-8")

    assert 'id="cortex"' in html
    assert "127.0.0.1:8097/#chat" in html
    assert "function cortex(d)" in script
    for forbidden in ("/api/order", "/api/shell", "/api/hermes/call"):
        assert forbidden not in server


def test_poste_dialogue_avec_hermes_via_le_hub_sans_appel_llm():
    html = (ROOT / "tools" / "ui" / "poste.html").read_text(encoding="utf-8")
    script = (ROOT / "tools" / "ui" / "poste.js").read_text(encoding="utf-8")
    server = (ROOT / "tools" / "dashboard.py").read_text(encoding="utf-8")

    assert 'id="cortex-form"' in html
    assert "http://127.0.0.1:8097/api/chat" in script
    assert "from: 'florent', to: 'hermes'" in script
    assert "setInterval(chargerDialogue, 10000)" in script
    assert "connect-src 'self' http://127.0.0.1:8097" in server
    for forbidden in ("subprocess", "order_send", "/api/hermes/call"):
        assert forbidden not in script
