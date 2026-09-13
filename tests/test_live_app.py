"""Contrat HTTP du nouveau dashboard, sans connexion réelle au courtier."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from titanium.web import live_app

pytestmark = pytest.mark.unit

INTERDITS = {
    "order_send",
    "order_check",
    "execute_recorded",
    "ExecutionPolicy",
    "assert_can_trade",
    "TRADE_ACTION_DEAL",
    "TRADE_ACTION_SLTP",
    "TRADE_ACTION_REMOVE",
}


def test_application_http_sans_symbole_execution():
    chemin = Path(live_app.__file__)
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    noms = {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
    attributs = {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
    assert not ((noms | attributs) & INTERDITS)


def test_routes_front_completes():
    chemins = {route.path for route in live_app.app.routes}
    assert {
        "/",
        "/stream",
        "/ui/compte",
        "/ui/boucle",
        "/ui/pilotage",
        "/ui/positions",
        "/ui/journal",
        "/api/chart",
        "/api/intentions",
    } <= chemins


def test_assets_locaux_presents():
    vendor = Path(live_app.__file__).parent / "static" / "vendor"
    assert (vendor / "htmx.min.js").stat().st_size > 10_000
    assert (vendor / "sse.js").stat().st_size > 1_000
    assert (vendor / "lightweight-charts.standalone.production.js").stat().st_size > 50_000


def test_depot_http_normalise_action_et_refuse_doublon(tmp_path, monkeypatch):
    class MoteurFactice:
        def noter(self, niveau, message):
            return None

    monkeypatch.setattr(live_app, "get_engine", lambda: MoteurFactice())
    monkeypatch.setattr(live_app, "FILE_INTENTIONS", tmp_path / "intentions.ndjson")
    monkeypatch.setattr(live_app, "JOURNAL_INTENTIONS", tmp_path / "journal.ndjson")
    client = TestClient(live_app.app)
    demande = {
        "action": "CLOSE_POSITION",
        "ticket": "123",
        "symbol": "US500",
        "motif": "test isolé",
    }

    premiere = client.post("/api/intentions", json=demande)
    doublon = client.post("/api/intentions", json=demande)

    assert premiere.status_code == 202
    assert premiere.json()["etat"] == "EN_ATTENTE"
    assert doublon.status_code == 409
