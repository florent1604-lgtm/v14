"""Adaptateur interne K3s vers le pont MT5 DEMO natif Windows.

Ce processus Linux ne possède ni bibliothèque MT5 ni identifiant courtier. Il
valide une enveloppe minimale avant de la transmettre au pont Windows, qui doit
refaire l'ensemble des contrôles d'exécution.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Titanium V14 MT5 DEMO adapter", docs_url=None, redoc_url=None)


class DemoEnvelope(BaseModel):
    sealed: Literal[True]
    account_mode: str
    decision_ref: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)


def _settings() -> tuple[str, str, str, str]:
    return (
        os.getenv("TITANIUM_EXEC_MODE", "").strip().upper(),
        os.getenv("MT5_BRIDGE_URL", "").strip().rstrip("/"),
        os.getenv("MT5_ADAPTER_TOKEN", "").strip(),
        os.getenv("MT5_BRIDGE_TOKEN", "").strip(),
    )


def _ready() -> bool:
    mode, url, adapter_token, bridge_token = _settings()
    return (
        mode == "DEMO"
        and url.startswith(("http://", "https://"))
        and bool(adapter_token)
        and bool(bridge_token)
    )


def _forward(url: str, token: str, payload: dict[str, Any], timeout_s: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("pont MT5 indisponible") from exc
    if not isinstance(body, dict):
        raise RuntimeError("réponse du pont MT5 invalide")
    return body


@app.get("/healthz")
async def healthz() -> dict:
    return {"alive": True}


@app.get("/readyz")
async def readyz() -> dict:
    if not _ready():
        raise HTTPException(status_code=503, detail="configuration DEMO incomplète")
    return {"ready": True, "mode": "DEMO"}


@app.post("/v1/execute", status_code=202)
async def execute(
    envelope: DemoEnvelope,
    authorization: str = Header(default=""),
) -> dict:
    mode, url, adapter_token, bridge_token = _settings()
    supplied = authorization.removeprefix("Bearer ").strip()
    if not adapter_token or not hmac.compare_digest(supplied, adapter_token):
        raise HTTPException(status_code=401, detail="authentification requise")
    if mode != "DEMO" or envelope.account_mode.upper() != "DEMO":
        raise HTTPException(status_code=403, detail="mode réel interdit")
    if not _ready():
        raise HTTPException(status_code=503, detail="pont MT5 indisponible")
    safe_envelope = envelope.model_dump()
    safe_envelope["account_mode"] = "DEMO"
    try:
        return await asyncio.to_thread(
            _forward,
            f"{url}/v1/execute",
            bridge_token,
            safe_envelope,
            10.0,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

