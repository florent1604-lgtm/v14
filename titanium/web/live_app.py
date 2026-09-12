"""Serveur HTTP du tableau de bord temps réel — lecture seule + file d'intentions.

Le serveur ne parle jamais au courtier. Il lit le cache de
``titanium.web.live_engine`` et, pour la clôture manuelle, dépose une ligne
dans la file de ``titanium.web.intentions``. La boucle armée reste seule juge :
c'est elle qui possède le mur, l'idempotence et le registre.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from titanium.web import intentions as it
from titanium.web.live_engine import get_engine

ICI = Path(__file__).resolve().parent
RACINE = ICI.parent.parent
FILE_INTENTIONS = RACINE / "results" / "intentions.ndjson"
JOURNAL_INTENTIONS = RACINE / "results" / "intentions_journal.ndjson"
BATTEMENT_S = 5.0

app = FastAPI(title="Titanium V14 — tableau de bord", docs_url=None, redoc_url=None)
gabarits = Jinja2Templates(directory=str(ICI / "templates"))
app.mount("/vendor", StaticFiles(directory=ICI / "static" / "vendor"), name="vendor")


@app.get("/", response_class=HTMLResponse)
async def page(request: Request):
    moteur = get_engine()
    return gabarits.TemplateResponse(
        request,
        "index.html",
        {
            "symbole": moteur.symbole,
            "timeframe": moteur.timeframe,
            "symboles": moteur.symboles_repli(),
        },
    )


# ──────────────────────── fragments HTMX (lecture) ────────────────────────

@app.get("/ui/compte", response_class=HTMLResponse)
async def ui_compte(request: Request):
    moteur = get_engine()
    return gabarits.TemplateResponse(
        request,
        "components/compte.html",
        {"compte": await moteur.bloc("account"), "mur": await moteur.bloc("wall")},
    )


@app.get("/ui/macro", response_class=HTMLResponse)
async def ui_macro(request: Request):
    """Jauges macro : un état borné, trois pourcentages, une sévérité.

    Le fragment ne calcule rien — il sert le bloc déjà normalisé, comme les
    autres. Une jauge qui interpréterait un texte finirait par afficher ce
    qu'elle croit comprendre.
    """
    return gabarits.TemplateResponse(
        request,
        "components/macro.html",
        {"macro": await get_engine().bloc("macro")},
    )


@app.get("/ui/boucle", response_class=HTMLResponse)
async def ui_boucle(request: Request):
    moteur = get_engine()
    return gabarits.TemplateResponse(
        request,
        "components/boucle.html",
        {"boucle": await moteur.bloc("loop"), "risque": await moteur.bloc("risque")},
    )


@app.get("/ui/pilotage", response_class=HTMLResponse)
async def ui_pilotage(request: Request):
    """Expose le pilotage et les paramètres sans autoriser leur mutation HTTP."""
    moteur = get_engine()
    return gabarits.TemplateResponse(
        request,
        "components/pilotage.html",
        {
            "compte": await moteur.bloc("account"),
            "mur": await moteur.bloc("wall"),
            "boucle": await moteur.bloc("loop"),
            "positions": await moteur.bloc("positions"),
            "meta": await moteur.bloc("meta"),
        },
    )


@app.get("/ui/positions", response_class=HTMLResponse)
async def ui_positions(request: Request):
    etat_intentions = it.etat(FILE_INTENTIONS, JOURNAL_INTENTIONS)
    return gabarits.TemplateResponse(
        request,
        "components/positions.html",
        {
            "b": await get_engine().bloc("positions"),
            "intentions": etat_intentions,
            "tickets_en_attente": {
                str(i.get("ticket", "")) for i in etat_intentions["en_attente"]
            },
        },
    )


@app.get("/ui/journal", response_class=HTMLResponse)
async def ui_journal(request: Request, niveau: str = "ALL", limite: int = 200):
    return gabarits.TemplateResponse(
        request,
        "components/journal.html",
        {"lignes": get_engine().journal(niveau, limite)},
    )


# ─────────────────────────────── flux SSE ────────────────────────────────

@app.get("/stream")
async def stream() -> StreamingResponse:
    """Émet un signal de relecture périodique, jamais une copie du moteur."""

    async def battre():
        while True:
            sante = get_engine().sante()
            yield f"event: tick\ndata: {json.dumps(sante, ensure_ascii=False)}\n\n"
            yield "event: chart\ndata: {}\n\n"
            await asyncio.sleep(BATTEMENT_S)

    return StreamingResponse(
        battre(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────── API JSON ───────────────────────────────

@app.get("/api/instantane")
async def instantane() -> JSONResponse:
    return JSONResponse(await get_engine().instantane())


@app.get("/api/bloc/{nom}")
async def bloc(nom: str) -> JSONResponse:
    try:
        return JSONResponse(await get_engine().bloc(nom))
    except KeyError as exc:
        return JSONResponse({"erreur": str(exc)}, status_code=404)


@app.get("/api/chart")
async def chart(symbole: str | None = None, timeframe: str | None = None) -> JSONResponse:
    return JSONResponse(await get_engine().chart(symbole, timeframe))


@app.get("/api/univers")
async def univers() -> JSONResponse:
    return JSONResponse(await get_engine().bloc("univers"))


@app.get("/api/analyses")
async def analyses(symbole: str | None = None) -> JSONResponse:
    return JSONResponse(await get_engine().analyses(symbole))


@app.get("/api/journal")
async def journal(niveau: str = "ALL", limite: int = 200) -> JSONResponse:
    return JSONResponse({"lignes": get_engine().journal(niveau, limite)})


@app.get("/api/sante")
async def sante() -> JSONResponse:
    return JSONResponse(get_engine().sante())


# ───────────────────────── intentions (dépôt seul) ────────────────────────

@app.get("/api/intentions")
async def lire_intentions() -> JSONResponse:
    return JSONResponse(it.etat(FILE_INTENTIONS, JOURNAL_INTENTIONS))


@app.post("/api/intentions")
async def deposer_intention(request: Request) -> JSONResponse:
    """Dépose une demande de clôture et répond 202, sans exécuter d'ordre."""
    origine = request.headers.get("origin", "")
    origine_locale = str(request.base_url).rstrip("/")
    if origine and origine.rstrip("/") != origine_locale:
        return JSONResponse({"accepte": False, "motif": "origine interdite"}, status_code=403)
    if "application/json" not in request.headers.get("content-type", "").lower():
        return JSONResponse(
            {"accepte": False, "motif": "Content-Type application/json requis"},
            status_code=415,
        )
    try:
        corps = await request.json()
    except ValueError:
        return JSONResponse({"accepte": False, "motif": "JSON invalide"}, status_code=400)
    if not isinstance(corps, dict):
        return JSONResponse({"accepte": False, "motif": "objet JSON attendu"}, status_code=400)

    intention = it.Intention(
        action=str(corps.get("action", "")).strip().lower(),
        ticket=str(corps.get("ticket", "")),
        symbol=str(corps.get("symbol", "")),
        motif=str(corps.get("motif", "")),
    )
    deja = {str(i.ticket) for i in it.en_attente(FILE_INTENTIONS, JOURNAL_INTENTIONS)}
    if intention.ticket in deja:
        return JSONResponse(
            {"accepte": False, "motif": "une demande est déjà en attente pour ce ticket"},
            status_code=409,
        )
    ok, detail = it.deposer(intention, FILE_INTENTIONS)
    if not ok:
        return JSONResponse({"accepte": False, "motif": detail}, status_code=400)
    get_engine().noter("INFO", f"intention déposée : {intention.action} #{intention.ticket}")
    return JSONResponse(
        {
            "accepte": True,
            "nonce": detail,
            "etat": "EN_ATTENTE",
            "message": "Demande déposée. La boucle armée décidera de l'honorer.",
        },
        status_code=202,
    )
