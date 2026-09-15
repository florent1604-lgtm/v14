from __future__ import annotations

import re

from prometheus_client import generate_latest

from titanium.observability import prometheus_metrics as metrics


def test_contrat_prometheus_v14(monkeypatch):
    monkeypatch.setattr(metrics, "_heartbeat", lambda: {"stats": {"evalues": 7}})
    monkeypatch.setattr(metrics, "_central_counts", lambda: (3, 2))
    monkeypatch.setattr(metrics, "_engine_processes", lambda: [])
    monkeypatch.setattr(metrics, "_LAST_MESSAGES", 0)
    monkeypatch.setattr(metrics, "_LAST_ALERTS", 0)

    metrics.observe_request(200, 0.125)
    metrics.record_error("TestError")
    metrics._refresh_runtime()
    exposition = generate_latest(metrics.REGISTRY).decode("utf-8")

    assert 'bot_requests_total{status="200"}' in exposition
    assert 'bot_request_duration_seconds_bucket{le="0.5"}' in exposition
    valeur = re.search(r"^bot_messages_processed_total ([0-9.]+)$", exposition, re.MULTILINE)
    assert valeur and float(valeur.group(1)) >= 7.0
    assert "bot_queue_size 3.0" in exposition
    assert 'bot_errors_total{type="TestError"}' in exposition
    assert 'bot_errors_total{type="organism_alert"}' in exposition
    assert "bot_uptime_seconds 0.0" in exposition
    assert "process_resident_memory_bytes 0.0" in exposition
    assert "process_cpu_seconds_total 0.0" in exposition
    assert "process_start_time_seconds 0.0" in exposition
