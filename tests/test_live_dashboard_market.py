from __future__ import annotations

from fastapi.testclient import TestClient

from titanium.web import live_app

CHART = {
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "candles": [
        {"t": "2026-09-12T10:00:00+00:00", "o": 3500.0, "h": 3510.0,
         "l": 3495.0, "c": 3508.0, "v": 42.0},
    ],
    "zones": [{"kind": "sr", "bas": 3490.0, "haut": 3500.0, "label": "support"}],
    "plan": {"side": 1, "entry": 3508.0, "sl": 3490.0, "tp": 3562.0, "rr": 3.0},
    "verdict": "ENTER",
    "code": "OK",
    "family": "continuation",
    "support_passed": 4,
    "quorum": 4,
    "pillars": [{"name": "data", "passed": True, "reason": "fresh"}],
    "context": "metals|continuation",
    "asset_class": "metals",
    "indicators": {"atr": 12.5},
    "price": 3508.0,
}


class MarketEngine:
    symbole = "XAUUSD"
    timeframe = "M15"

    async def chart(self, symbole=None, timeframe=None):
        data = dict(CHART)
        data["symbol"] = symbole or self.symbole
        data["timeframe"] = timeframe or self.timeframe
        return {"data": data, "age_s": 0.2, "stale": False, "erreur": ""}

    async def bloc(self, nom):
        assert nom == "univers"
        return {"data": ["XAUUSD", "US500"], "age_s": 1.0, "stale": False,
                "erreur": ""}

    async def analyses(self, symbole=None):
        return {
            "symbol": symbole,
            "runs": [{"ticker": symbole, "decision": "BUY", "at": "maintenant"}],
            "analystes": {"recents": []},
            "stale": False,
            "age_s": 0.1,
            "erreurs": [],
        }


class PageEngine:
    symbole = "XAUUSD"
    timeframe = "M15"

    def symboles_repli(self):
        return ["XAUUSD", "US500"]


def test_page_expose_un_vrai_selecteur_et_les_calculs(monkeypatch):
    monkeypatch.setattr(live_app, "get_engine", lambda: PageEngine())
    response = TestClient(live_app.app).get("/")
    assert response.status_code == 200
    assert '<select id="symbole"' in response.text
    assert '<input id="symbole"' not in response.text
    assert "Calculs des moteurs" in response.text
    assert "Analyses réelles de la boucle" in response.text
    assert "enveloppe.data" in response.text
    assert "addCandlestickSeries" in response.text
    assert "serie.update(candleData)" in response.text


def test_routes_marche_conservent_donnees_et_fraicheur(monkeypatch):
    monkeypatch.setattr(live_app, "get_engine", lambda: MarketEngine())
    client = TestClient(live_app.app)

    chart = client.get("/api/chart?symbole=US500&timeframe=H1")
    assert chart.status_code == 200
    assert chart.json()["data"]["symbol"] == "US500"
    assert chart.json()["data"]["candles"][0]["c"] == 3508.0
    assert chart.json()["stale"] is False

    univers = client.get("/api/univers")
    assert univers.status_code == 200
    assert univers.json()["data"] == ["XAUUSD", "US500"]

    analyses = client.get("/api/analyses?symbole=XAUUSD")
    assert analyses.status_code == 200
    assert analyses.json()["runs"][0]["decision"] == "BUY"
