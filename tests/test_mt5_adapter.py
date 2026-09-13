from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient


def _configured(monkeypatch):
    monkeypatch.setenv("TITANIUM_EXEC_MODE", "DEMO")
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://host.internal:8769")
    monkeypatch.setenv("MT5_ADAPTER_TOKEN", "adapter-token")
    monkeypatch.setenv("MT5_BRIDGE_TOKEN", "bridge-token")


def test_adapter_never_imports_mt5():
    from titanium.web import mt5_adapter

    tree = ast.parse(Path(mt5_adapter.__file__).read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("MetaTrader5" in name or "mt5_executor" in name for name in imports)


def test_readiness_requires_demo_and_all_bridge_settings(monkeypatch):
    from titanium.web.mt5_adapter import app

    client = TestClient(app)
    monkeypatch.delenv("TITANIUM_EXEC_MODE", raising=False)
    assert client.get("/readyz").status_code == 503
    _configured(monkeypatch)
    assert client.get("/readyz").json() == {"ready": True, "mode": "DEMO"}


def test_execute_rejects_unauthenticated_unsealed_and_real(monkeypatch):
    from titanium.web.mt5_adapter import app

    _configured(monkeypatch)
    client = TestClient(app)
    demo = {"sealed": True, "account_mode": "DEMO", "decision_ref": "abc"}
    assert client.post("/v1/execute", json=demo).status_code == 401
    headers = {"Authorization": "Bearer adapter-token"}
    assert client.post(
        "/v1/execute", headers=headers, json={**demo, "sealed": False}
    ).status_code == 422
    assert client.post(
        "/v1/execute", headers=headers, json={**demo, "account_mode": "REAL"}
    ).status_code == 403


def test_execute_forwards_only_safe_envelope(monkeypatch):
    from titanium.web import mt5_adapter

    _configured(monkeypatch)
    captured = {}

    def forward(url, token, payload, timeout_s):
        captured.update(url=url, token=token, payload=payload, timeout_s=timeout_s)
        return {"accepted": True, "status": "QUEUED"}

    monkeypatch.setattr(mt5_adapter, "_forward", forward)
    response = TestClient(mt5_adapter.app).post(
        "/v1/execute",
        headers={"Authorization": "Bearer adapter-token"},
        json={
            "sealed": True,
            "account_mode": "DEMO",
            "decision_ref": "abc",
            "payload": {"symbol": "BTCUSD"},
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "status": "QUEUED"}
    assert captured["url"] == "http://host.internal:8769/v1/execute"
    assert captured["token"] == "bridge-token"
    assert captured["payload"]["account_mode"] == "DEMO"

