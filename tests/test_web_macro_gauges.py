"""Jauges macro du tableau de bord : route, rendu, et ce qu'elles affichent.

Une jauge est une affirmation visuelle. Ces tests verifient donc trois choses :
la route existe et repond, les pourcentages viennent du bloc normalise (jamais
d'un texte interprete), et l'ecran dit QUEL verdict il montre — celui de la
boucle, ou un calcul local faute de mieux.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from titanium.web import live_engine, state
from titanium.web.dashboard_app import app as app_dashboard
from titanium.web.live_app import app as app_live

pytestmark = pytest.mark.unit


@pytest.fixture()
def client(monkeypatch):
    """Un lecteur neuf par test : sinon un bloc en cache d'un test teinte l'autre."""
    monkeypatch.setattr(live_engine, "_moteur", None)
    return TestClient(app_dashboard)


def ecrire_battement(tmp_path, *, macro: dict, age_s: float = 0.0):
    """Publie un battement comme la boucle le ferait, puis rend son chemin."""
    dossier = tmp_path / "results"
    dossier.mkdir(exist_ok=True)
    battu = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    (dossier / "loop_heartbeat.json").write_text(json.dumps({
        "at": battu.isoformat(), "intervalle": 60, "armed": True, "equity": 0.0,
        "stats": {}, "macro": macro,
    }), encoding="utf-8")


def bloc_macro(**surcharges) -> dict:
    base = {
        "state": "CLEAR", "label": "Calme", "severity": "ok", "allows_new_risk": True,
        "conservative": False, "score_pct": 0, "freshness_pct": 90, "imminence_pct": 0,
        "next_event": None, "countdown_s": None, "since_last_s": None,
        "data_age_s": 5.0, "reasons": ["aucune publication HIGH+ dans l'horizon"],
        "enabled": True, "source": "file",
        "service": {"enabled": True, "running": True, "provider": "file",
                    "thread": "macro-feed", "error": ""},
    }
    base.update(surcharges)
    return base


def test_la_route_macro_existe_dans_les_deux_applications():
    for application in (app_dashboard, app_live):
        chemins = {route.path for route in application.routes}
        assert "/ui/macro" in chemins, f"route absente de {application.title}"


def test_les_jauges_affichent_les_pourcentages_du_bloc(tmp_path, monkeypatch, client):
    monkeypatch.setattr(state, "RACINE", tmp_path)
    ecrire_battement(tmp_path, macro=bloc_macro(score_pct=48, freshness_pct=37,
                                               imminence_pct=12))

    reponse = client.get("/ui/macro")
    assert reponse.status_code == 200
    html = reponse.text
    for libelle in ("Risque macro", "Fraîcheur du calendrier", "Imminence publi."):
        assert libelle in html
    for valeur in ("48 %", "37 %", "12 %"):
        assert valeur in html, f"jauge absente du rendu : {valeur}"
    assert "width:48%" in html.replace(" ", "")


def test_un_gel_macro_affiche_un_rouge_et_nomme_la_publication(tmp_path, monkeypatch, client):
    monkeypatch.setattr(state, "RACINE", tmp_path)
    ecrire_battement(tmp_path, macro=bloc_macro(
        state="BLACKOUT", label="Gel", severity="crit", allows_new_risk=False,
        conservative=True, score_pct=100, freshness_pct=88, imminence_pct=100,
        next_event={"title": "FOMC", "at": "2026-09-17T18:00:00+00:00", "seconds": 240.0},
        countdown_s=240.0, since_last_s=None,
        reasons=["gel autour de « FOMC » (USD, HIGH) dans 240 s — aucun risque neuf"],
    ))

    html = client.get("/ui/macro").text
    assert "rouge-bg" in html, "un gel doit etre rouge"
    assert "aucun risque neuf" in html
    assert "FOMC" in html, "la publication qui a cause le gel doit etre nommee"
    assert "240" in html
    assert "Verdict de la BOUCLE" in html


def test_sans_battement_l_ecran_dit_que_le_verdict_est_local(tmp_path, monkeypatch, client):
    """Le tableau de bord ne doit jamais faire passer un calcul local pour celui
    qui decide : la mention est explicite, pas deduite d'une couleur."""
    monkeypatch.setattr(state, "RACINE", tmp_path)

    html = client.get("/ui/macro").text
    assert "Verdict LOCAL" in html
    assert "Verdict de la BOUCLE" not in html


def test_un_verdict_de_boucle_perime_est_signale(tmp_path, monkeypatch, client):
    monkeypatch.setattr(state, "RACINE", tmp_path)
    ecrire_battement(tmp_path, macro=bloc_macro(), age_s=900.0)

    html = client.get("/ui/macro").text
    assert "périmé" in html
    assert "la boucle ne le publie plus" in html


def test_un_flux_illisible_affiche_du_rouge_sans_lever(tmp_path, monkeypatch, client):
    """Une faute de configuration ne doit pas noircir la page, mais se voir."""
    monkeypatch.setattr(state, "RACINE", tmp_path)
    monkeypatch.setattr(
        "titanium.macro.load_policy",
        lambda: (_ for _ in ()).throw(ValueError("cles macro inconnues: ttl")),
    )

    reponse = client.get("/ui/macro")
    assert reponse.status_code == 200
    assert "Flux macro illisible" in reponse.text
    assert "aucun risque neuf n'est autorisé" in reponse.text


def test_le_bloc_macro_est_servi_comme_les_autres(tmp_path, monkeypatch):
    """TTL court : une jauge ne peut pas mentir pendant une heure."""
    monkeypatch.setattr(live_engine, "_moteur", None)
    monkeypatch.setattr(state, "RACINE", tmp_path)
    assert live_engine.TTL_MACRO <= 10.0
    moteur = live_engine.get_engine()
    assert "macro" in moteur.sante()["blocs"]

    import asyncio

    enveloppe = asyncio.run(moteur.bloc("macro"))
    assert set(enveloppe) >= {"data", "age_s", "stale", "erreur", "jamais_lu"}
    assert enveloppe["data"]["disponible"] is True
    assert enveloppe["data"]["enabled"] is False, "sans config, le flux reste eteint"
