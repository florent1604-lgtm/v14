"""Serveur du tableau de bord temps réel — lecture seule.

FastAPI + HTMX + SSE au-dessus de :mod:`titanium.web.live_engine`. Aucune
route ne mute quoi que ce soit : pas d'ordre, pas d'armement, pas d'écriture
de configuration. Les deux routes mutantes du cahier des charges initial
(clôture de position, annulation d'ordre) ont été retirées de cette version :
``TRADE_ACTION_REMOVE`` n'existe nulle part dans le dépôt, et
``assert_can_trade`` est appelé une fois par cycle dans ``manage_once``, pas
dans l'envoi — l'appeler depuis HTTP contournerait le mur au lieu de le
respecter. Une clôture manuelle passera par un fichier d'intention relu par
la boucle armée, jamais par un chemin HTTP vers le courtier.

Les bibliothèques front sont vendorisées dans ``tools/ui/htmx/vendor`` :
aucune dépendance distante, le tableau de bord survit à une coupure réseau.

Lancement : ``python tools/dashboard_live.py`` (127.0.0.1:8096).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from titanium.web.live_engine import get_engine

RACINE = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).resolve().parent / "templates"
VENDOR = RACINE / "tools" / "ui" / "htmx" / "vendor"

app = FastAPI(title="Titanium V14 — tableau de bord", docs_url=None, redoc_url=None)
gabarits = Jinja2Templates(directory=str(TEMPLATES))

if VENDOR.is_dir():
    app.mount("/vendor", StaticFiles(directory=str(VENDOR)), name="vendor")


# ───────────────────────────────── pages ─────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    moteur = get_engine()
    return gabarits.TemplateResponse(
        request, "index.html",
        {"snap": await moteur.instantane(), "symbole": moteur.symbole,
         "timeframe": moteur.timeframe},
    )


# ──────────────────────────── fragments HTMX ─────────────────────────────

@app.get("/ui/positions", response_class=HTMLResponse)
async def ui_positions(request: Request):
    bloc = await get_engine().bloc("positions")
    return gabarits.TemplateResponse(request, "components/positions.html", {"b": bloc})


@app.get("/ui/compte", response_class=HTMLResponse)
async def ui_compte(request: Request):
    moteur = get_engine()
    return gabarits.TemplateResponse(
        request, "components/compte.html",
        {"compte": await moteur.bloc("account"), "mur": await moteur.bloc("wall")},
    )


@app.get("/ui/boucle", response_class=HTMLResponse)
async def ui_boucle(request: Request):
    moteur = get_engine()
    return gabarits.TemplateResponse(
        request, "components/boucle.html",
        {"boucle": await moteur.bloc("loop"), "risque": await moteur.bloc("risque")},
    )


@app.get("/ui/journal", response_class=HTMLResponse)
async def ui_journal(request: Request, niveau: str = "ALL"):
    return gabarits.TemplateResponse(
        request, "components/journal.html",
        {"lignes": get_engine().journal(niveau), "niveau": niveau},
    )


# ────────────────────────────── données JSON ─────────────────────────────

@app.get("/api/chart")
async def api_chart(symbole: str | None = None, timeframe: str | None = None):
    return JSONResponse(await get_engine().chart(symbole, timeframe))


@app.get("/api/sante")
async def api_sante():
    return JSONResponse(get_engine().sante())


# ────────────────────────────────── SSE ──────────────────────────────────

@app.get("/stream")
async def stream(request: Request):
    """Flux unique partagé. Le rythme est celui du TTL le plus court, pas
    celui du navigateur : dix onglets ouverts ne coûtent pas dix lectures MT5,
    puisque le moteur sert le même cache à tout le monde.
    """

    async def evenements():
        while True:
            if await request.is_disconnected():
                break
            moteur = get_engine()
            try:
                snap = await moteur.instantane()
                yield f"event: etat\ndata: {json.dumps(snap, default=str)}\n\n"
                graphique = await moteur.bloc("chart")
                yield f"event: chart\ndata: {json.dumps(graphique, default=str)}\n\n"
            except Exception as exc:  # noqa: BLE001 — un flux mort ne tue pas la page
                yield f"event: erreur\ndata: {json.dumps({'erreur': str(exc)})}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(
        evenements(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
