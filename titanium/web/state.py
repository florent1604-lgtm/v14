"""Collecte de l'état de V14 — source unique pour l'interface et la CLI.

Chaque fonction relit l'état RÉEL à l'appel, ne met rien en cache et **ne lève
jamais** : une sonde qui plante ne doit pas noircir tout le tableau de bord. Un
bloc en erreur porte sa propre clé ``error`` et les autres continuent de vivre.

Rien ici ne déclenche d'ordre, ne modifie de stop, ni n'appelle de LLM. Le seul
point coûteux est `scan()`, qui lit MT5 et calcule les features — il est donc
explicite et jamais automatique.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent


def _config() -> dict:
    from tradingagents.default_config import DEFAULT_CONFIG
    return DEFAULT_CONFIG


def _safe(fn, defaut=None):
    """Exécute une sonde ; toute erreur devient un bloc ``{"error": ...}``."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", **(defaut or {})}


# ═══════════════════════════════ blocs d'état ════════════════════════════════

def meta() -> dict:
    c = _config()
    return {
        "now": datetime.now(timezone.utc).isoformat(),
        "provider": c["llm_provider"],
        "deep_model": c["deep_think_llm"],
        "quick_model": c["quick_think_llm"],
        "results_dir": str(c["results_dir"]),
    }


def wall() -> dict:
    """Les trois verrous d'exécution, revérifiés à chaque appel."""
    from titanium.execution.mt5_executor import (
        ExecutionPolicy,
        ExecutionRefused,
        assert_can_trade,
    )

    p = ExecutionPolicy.from_config(_config())
    out = {
        "armed": p.enabled,
        "expected_login": p.expected_demo_login,
        "real_allowed": p.allow_real_account,
        "min_lot_overrisk": p.allow_min_lot_overrisk,
        "magic": p.magic,
        "open": False,
        "code": "",
        "detail": "",
    }
    try:
        from titanium.data.mt5_vendor import account_snapshot
        assert_can_trade(p, account_snapshot())
        out["open"] = True
        out["code"] = "OUVERT"
    except ExecutionRefused as exc:
        out["code"] = exc.code
        out["detail"] = exc.detail
    except Exception as exc:  # noqa: BLE001
        out["code"] = "INDISPONIBLE"
        out["detail"] = f"{type(exc).__name__}: {exc}"
    return out


def account() -> dict:
    from titanium.data.mt5_vendor import account_snapshot

    a = account_snapshot()
    return {
        "connected": True, "login": a.login, "server": a.server,
        "currency": a.currency, "balance": a.balance, "equity": a.equity,
        "margin_free": a.margin_free, "is_demo": a.is_demo,
        "trade_mode": a.trade_mode,
        "mode_label": "DÉMO" if a.is_demo else ("CONCOURS" if a.trade_mode == 1 else "RÉEL"),
    }


def vendors() -> dict:
    """Chaînes de vendeurs configurées, et lesquelles sont réellement servies."""
    from tradingagents.dataflows.interface import VENDOR_METHODS

    c = _config()
    return {
        "chains": {k: [v.strip() for v in str(val).split(",")]
                   for k, val in c["data_vendors"].items()},
        "registry": {m: list(v) for m, v in VENDOR_METHODS.items()
                     if m in ("get_stock_data", "get_indicators",
                              "get_fundamentals", "get_news")},
    }


def bricks() -> list[dict]:
    """Briques Titanium : présence du module + nombre de tests qui la couvre."""
    from tools.dashboard import BRIQUES  # source unique de la liste

    par_fichier = (tests() or {}).get("par_fichier", {})
    out = []
    for nom, module, fichier in BRIQUES:
        existe = (RACINE / module).exists()
        out.append({
            "name": nom, "module": module, "exists": existe,
            "tests": par_fichier.get(fichier, 0), "test_file": fichier,
        })
    return out


def tests() -> dict:
    """Dernier relevé de suite (écrit par `dashboard.py --tests`)."""
    f = RACINE / "results" / "tests.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def runs(limit: int = 20) -> list[dict]:
    """Analyses multi-agents déjà produites, les plus récentes d'abord."""
    logs = Path(_config()["results_dir"])
    out = []
    if not logs.exists():
        return out
    fichiers = sorted(logs.rglob("full_states_log_*.json"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    for f in fichiers:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        debat = d.get("investment_debate_state") or {}
        risque = d.get("risk_debate_state") or {}
        out.append({
            "ticker": d.get("company_of_interest", f.parent.parent.name),
            "date": d.get("trade_date", ""),
            "at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "decision": (d.get("final_trade_decision") or "").strip(),
            "has_debate": bool(debat.get("judge_decision")),
            "has_risk": bool(risque.get("judge_decision")),
            "reports": {
                "market": len(d.get("market_report") or ""),
                "sentiment": len(d.get("sentiment_report") or ""),
                "news": len(d.get("news_report") or ""),
                "fundamentals": len(d.get("fundamentals_report") or ""),
            },
            "bull": (debat.get("bull_history") or "").strip(),
            "bear": (debat.get("bear_history") or "").strip(),
            "judge": (debat.get("judge_decision") or "").strip(),
            "risk_judge": (risque.get("judge_decision") or "").strip(),
        })
    return out


def edge() -> dict:
    """Verdicts d'edge par contexte, plus le journal qui les nourrit."""
    from titanium.edge import MIN_SAMPLES, EdgeBook, TradeJournal

    chemin = Path(_config()["results_dir"]).parent / "trades.ndjson"
    journal = TradeJournal(chemin)
    livre = EdgeBook(journal=journal)
    verdicts = [v.to_dict() for v in livre.summary()]
    return {
        "journal": str(chemin),
        "journal_exists": chemin.exists(),
        "min_samples": MIN_SAMPLES,
        "contexts": verdicts,
        "total_trades": sum(v["samples"] for v in verdicts),
        "journal_rejected": journal.rejected_lines,
    }


def positions() -> dict:
    """Positions ouvertes appartenant à V14 (magic), et leur état de gestion."""
    from titanium.execution.mt5_executor import ExecutionPolicy
    from titanium.execution.position_manager import ManageParams, load_state

    p = ExecutionPolicy.from_config(_config())
    params = ManageParams.from_config(_config())
    chemin = Path(_config()["results_dir"]).parent / "positions.json"
    suivi = load_state(chemin)

    lignes = []
    attentes = []
    try:
        import MetaTrader5 as mt5  # noqa: N813

        from titanium.data.mt5_vendor import mt5_lock, mt5_session

        with mt5_lock:
            with mt5_session():
                for pos in (mt5.positions_get() or ()):
                    if int(getattr(pos, "magic", 0) or 0) != p.magic:
                        continue
                    st = suivi.get(str(pos.ticket))
                    entree, courant = float(pos.price_open), float(pos.price_current)
                    side = 1 if int(pos.type) == 0 else -1
                    fav = ((courant - entree) / st.r * side) if (st and st.r) else None
                    lignes.append({
                        "ticket": str(pos.ticket), "symbol": pos.symbol,
                        "side": side, "volume": float(pos.volume),
                        "entry": entree, "current": courant,
                        "sl": float(pos.sl) if pos.sl else None,
                        "tp": float(pos.tp) if pos.tp else None,
                        "profit": float(getattr(pos, "profit", 0.0)),
                        "phase": st.phase if st else "non suivi",
                        "fav_r": round(fav, 3) if fav is not None else None,
                        "peak_r": round(st.peak_fav_r, 3) if st else None,
                    })
                for ordre in (mt5.orders_get() or ()):
                    if int(getattr(ordre, "magic", 0) or 0) != p.magic:
                        continue
                    ordre_type = int(getattr(ordre, "type", -1) or -1)
                    buy_limit = int(mt5.ORDER_TYPE_BUY_LIMIT)
                    sell_limit = int(mt5.ORDER_TYPE_SELL_LIMIT)
                    if ordre_type not in (buy_limit, sell_limit):
                        continue
                    side = 1 if ordre_type == buy_limit else -1
                    attentes.append({
                        "ticket": str(ordre.ticket), "symbol": ordre.symbol,
                        "side": side,
                        "volume": float(getattr(ordre, "volume_initial", 0.0) or 0.0),
                        "price": float(getattr(ordre, "price_open", 0.0) or 0.0),
                        "sl": float(ordre.sl) if getattr(ordre, "sl", 0.0) else None,
                        "tp": float(ordre.tp) if getattr(ordre, "tp", 0.0) else None,
                        "expires": int(getattr(ordre, "time_expiration", 0) or 0),
                        "kind": "BUY_LIMIT" if side > 0 else "SELL_LIMIT",
                    })
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "positions": [],
                "pending": [], "params": params.__dict__}

    return {"positions": lignes, "pending": attentes, "params": params.__dict__,
            "state_path": str(chemin)}


def bridge() -> dict:
    """État du pont MQL5 : ce que le terminal voit actuellement."""
    from titanium.bridge.mt5_zones import NOM_FICHIER, dossier_mql5_files

    d = dossier_mql5_files()
    if d is None:
        return {"available": False, "reason": "dossier MQL5/Files introuvable"}
    f = d / NOM_FICHIER
    indicateur = d.parent / "Indicators" / "titanium_zones.mq5"
    out = {
        "available": True,
        "files_dir": str(d),
        "indicator_installed": indicateur.exists(),
        "indicator_compiled": (indicateur.with_suffix(".ex5")).exists(),
        "payload_exists": f.exists(),
    }
    if f.exists():
        try:
            # Le fichier porte UN BLOC PAR SYMBOLE depuis le 07/08/2026 :
            # `json.loads` sur le tout lève `Extra data`. On lit le premier
            # bloc, qui suffit à décrire l'état du pont.
            from titanium.bridge.mt5_zones import SEPARATEUR
            brut = f.read_text(encoding="utf-8")
            blocs = [b for b in brut.split(SEPARATEUR) if b.strip()]
            p = json.loads(blocs[0]) if blocs else {}
            p["n_symboles"] = len(blocs)
            out.update(symbol=p.get("symbol"), verdict=p.get("verdict"),
                       pillars_line=p.get("pillars_line"),
                       zones=[z.get("kind") for z in (p.get("zones") or [])],
                       rr=(p.get("plan") or {}).get("rr"),
                       written_at=p.get("written_at"))
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
    return out


#: Au-delà de ce multiple de l'intervalle sans battement, la boucle est réputée
#: figée. 3× laisse passer un tour lent (balayage MT5 chargé) sans crier au loup.
TOLERANCE_BATTEMENT = 3.0


def _const_boucle(nom: str, defaut):
    """Lit une constante de `tools/live_demo.py` sans l'importer.

    L'import exécuterait le module — donc son parsing d'arguments et ses
    effets de bord. On lit le source, ce qui est sans risque et suffit pour
    des littéraux.
    """
    import ast
    try:
        src = (RACINE / "tools" / "live_demo.py").read_text(encoding="utf-8")
        for n in ast.parse(src).body:
            if isinstance(n, ast.Assign) and n.targets:
                cible = n.targets[0]
                if getattr(cible, "id", "") == nom:
                    return ast.literal_eval(n.value)
    except Exception:  # noqa: BLE001
        pass
    return defaut


def loop() -> dict:
    """La boucle d'amorçage tourne-t-elle, et qu'a-t-elle fait ?

    Lit le battement de cœur écrit par `tools/live_demo.py`. Un scan de
    processus serait plus direct, mais `wmic` n'existe plus sous Windows 11 :
    la sonde rendait « arrêtée » alors que la boucle tournait — le tableau de
    bord mentait. Le battement ne dépend d'aucun outil système, et il distingue
    en plus une boucle **figée** d'une boucle **arrêtée**.
    """
    from titanium.execution.mt5_executor import ExecutionPolicy
    from titanium.execution.position_manager import ManageParams

    p = ExecutionPolicy.from_config(_config())
    m = ManageParams.from_config(_config())
    out = {
        "running": False, "stale": False, "armed": p.enabled,
        # Lus à la source. Codés en dur, ils mentaient dès qu'on changeait
        # la boucle — un tableau de bord qui affiche 3 alors que la boucle en
        # autorise 8 est pire qu'un tableau vide : il inspire confiance.
        "max_positions": _const_boucle("MAX_POSITIONS", 3),
        "max_per_symbol": _const_boucle("MAX_PAR_SYMBOLE", 1),
        "max_risque_cumule_pct": _const_boucle("MAX_RISQUE_CUMULE_PCT", 0.0),
        "breakeven_r": m.breakeven_r, "trail_start_r": m.trail_start_r,
        "trail_dist_r": m.trail_dist_r,
        "last_beat": None, "age_s": None, "stats": {},
        # Incidents de lecture de `positions.json`. Vide = rien à signaler.
        # Sans ce relais, l'incident s'arrêterait au battement : le tableau
        # de bord ne recopie que des clés nommées, et « rendre bruyant » un
        # défaut qui n'atteint aucun écran ne le rend pas bruyant du tout.
        "etat_incidents": [], "etat_incidents_total": 0,
    }

    f = RACINE / "results" / "loop_heartbeat.json"
    if not f.exists():
        out["reason"] = "aucun battement — la boucle n'a jamais tourné"
        return out

    try:
        b = json.loads(f.read_text(encoding="utf-8"))
        battu = datetime.fromisoformat(b["at"])
        age = (datetime.now(timezone.utc) - battu).total_seconds()
        limite = float(b.get("intervalle", 60)) * TOLERANCE_BATTEMENT
        out.update(
            last_beat=b["at"], age_s=round(age, 1),
            stats=b.get("stats", {}), equity=b.get("equity"),
            portables=b.get("portables"),
            # `armed` du battement reflète l'état RÉEL au dernier tour ; celui
            # de la config dit seulement ce qui est déclaré.
            armed=bool(b.get("armed", p.enabled)),
            running=age <= limite,
            stale=age > limite,
            etat_incidents=list(b.get("etat_incidents") or []),
            etat_incidents_total=int(b.get("etat_incidents_total", 0) or 0),
        )
        if out["stale"]:
            out["reason"] = (f"dernier battement il y a {age:.0f} s "
                             f"(limite {limite:.0f} s) — boucle figée ou arrêtée")
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"battement illisible : {type(exc).__name__}"
    return out


def discriminants(limite: int = 12) -> dict:
    """Classement des indicateurs — ou pourquoi il n'y en a pas encore."""
    from titanium.analysis.discriminants import (
        MIN_PAR_GROUPE,
        analyser,
        depuis_journal,
    )

    # Le panel d'indicateurs vit dans `excursions.ndjson`, pas dans le journal
    # d'edge : `ClosedTrade` ne modélise que ce dont la mesure d'edge a besoin
    # (contexte, résultat, coût), tandis que l'analyse discriminante réclame la
    # perception complète à l'entrée. Deux fichiers, deux usages.
    base = Path(_config()["results_dir"]).parent
    chemin = base / "excursions.ndjson"
    ech = depuis_journal(chemin)
    if not ech:
        # Le backtest, lui, écrit son panel directement dans le journal d'edge.
        ech = depuis_journal(base / "trades.ndjson")
    if not ech:
        return {"suffisant": False, "n_trades": 0, "min_par_groupe": MIN_PAR_GROUPE,
                "message": ("aucun trade journalisé avec panel d'indicateurs — "
                            "le classement demande des résultats, pas des opinions"),
                "top": []}
    # Peu de permutations ici : la page doit répondre vite. L'analyse de
    # référence se lance hors ligne, avec le réglage complet.
    r = analyser(ech, n_permutations=300)
    return {"suffisant": r.suffisant, "n_trades": r.n_trades,
            "n_gagnants": r.n_gagnants, "n_perdants": r.n_perdants,
            "min_par_groupe": MIN_PAR_GROUPE, "message": r.message,
            "top": [d.to_dict() for d in r.discriminants[:limite]]}


def analystes() -> dict:
    """Avis des analystes LLM — flux découplé, peut être vide sans anomalie."""
    rendus = RACINE / "results" / "avis_rendus.ndjson"
    demandes = RACINE / "results" / "avis_demandes.ndjson"

    def _lignes(f):
        if not f.exists():
            return []
        out = []
        for l in f.read_text(encoding="utf-8").splitlines():
            l = l.strip()
            if not l:
                continue
            try:
                out.append(json.loads(l))
            except json.JSONDecodeError:
                continue
        return out

    avis = _lignes(rendus)
    recents = avis[-12:][::-1]
    return {
        "actif": rendus.exists() or demandes.exists(),
        "n_demandes": len(_lignes(demandes)),
        "n_avis": len(avis),
        "en_attente": max(0, len(_lignes(demandes)) - len(avis)),
        "recents": [{
            "symbol": a.get("symbol", ""), "rating": a.get("rating", ""),
            "conviction": a.get("conviction", 0.5),
            "accord": a.get("accord"), "bar_time": a.get("bar_time", ""),
            "rendu_a": a.get("rendu_a", ""), "source": a.get("source", ""),
        } for a in recents],
    }


def prod_fantome() -> dict:
    """Ce que le mode PROD aurait fait — observé, jamais exécuté."""
    from titanium.shadow import resume
    r = resume(RACINE / "results" / "shadow_prod.ndjson")
    r["actif"] = r.get("lignes", 0) > 0
    return r


def fenetres() -> dict:
    """Graphiques que V14 demande à MT5 d'ouvrir."""
    from titanium.bridge.mt5_charts import MAX_FENETRES, NOM_FICHIER_CHARTS
    from titanium.bridge.mt5_zones import dossier_mql5_files

    d = dossier_mql5_files()
    if d is None:
        return {"disponible": False, "symboles": [], "maxi": MAX_FENETRES}
    f = d / NOM_FICHIER_CHARTS
    syms = []
    if f.exists():
        syms = [l.strip() for l in f.read_text(encoding="ascii",
                                               errors="ignore").splitlines()
                if l.strip() and not l.startswith("#")]
    return {"disponible": True, "dossier": str(d), "symboles": syms,
            "maxi": MAX_FENETRES,
            "ecrit_a": (datetime.fromtimestamp(f.stat().st_mtime,
                                               tz=timezone.utc).isoformat()
                        if f.exists() else None)}


def risque() -> dict:
    """Risque cumulé des positions ouvertes, face à son budget.

    Compter les positions ne borne pas l'exposition : c'est ce chiffre-ci
    qui la borne, et c'est donc lui qu'il faut regarder.
    """
    from titanium.confiance import (
        MAX_RISK_PCT, RISQUE_MAX_PCT, RISQUE_MIN_PCT, RISQUE_PIVOT_PCT,
    )
    budget = _const_boucle("MAX_RISQUE_CUMULE_PCT", 0.0)
    engage = 0.0
    n = 0
    try:
        from titanium.data.mt5_vendor import mt5_session
        import MetaTrader5 as mt5  # noqa: N813
        with mt5_session():
            info = mt5.account_info()
            eq = float(info.equity) if info else 0.0
            for p in (mt5.positions_get() or []):
                n += 1
                sp = mt5.symbol_info(p.symbol)
                if sp is None or not sp.trade_tick_size or eq <= 0:
                    continue
                if not p.sl:
                    engage += MAX_RISK_PCT
                    continue
                d = abs(p.price_open - p.sl)
                engage += ((d / sp.trade_tick_size) * sp.trade_tick_value
                           * p.volume) / eq * 100.0
    except Exception:  # noqa: BLE001
        return {"disponible": False, "budget_pct": budget}
    return {
        "disponible": True, "engage_pct": round(engage, 2),
        "budget_pct": budget, "positions": n,
        "occupation": round(engage / budget * 100, 1) if budget else 0.0,
        "echelle": {"plancher": RISQUE_MIN_PCT, "pivot": RISQUE_PIVOT_PCT,
                    "plafond_modulation": RISQUE_MAX_PCT,
                    "plafond_dur": MAX_RISK_PCT},
    }


def state() -> dict:
    """État complet. Chaque bloc est isolé : un échec n'en emporte pas d'autres."""
    return {
        "meta": _safe(meta, {}),
        "wall": _safe(wall, {}),
        "account": _safe(account, {"connected": False}),
        "vendors": _safe(vendors, {}),
        "bricks": _safe(bricks, []) or [],
        "tests": _safe(tests, {}),
        "runs": _safe(runs, []) or [],
        "edge": _safe(edge, {}),
        "positions": _safe(positions, {"positions": []}),
        "bridge": _safe(bridge, {"available": False}),
        "loop": _safe(loop, {"running": False}),
        "discriminants": _safe(discriminants, {"suffisant": False, "top": []}),
        "analystes": _safe(analystes, {"actif": False, "recents": []}),
        "prod_fantome": _safe(prod_fantome, {"actif": False, "lignes": 0}),
        "fenetres": _safe(fenetres, {"disponible": False, "symboles": []}),
        "risque": _safe(risque, {"disponible": False}),
    }


# ═════════════════════════════════ balayage ══════════════════════════════════

SYMBOLES_DEFAUT = ["EURUSD", "XAUUSD", "US500", "GER40", "UK100", "USTECH",
                   "GBPUSD", "USDJPY", "BTCUSD", "NAS100"]

#: Ordre des piliers tel que la porte les rend — sert d'en-tête au tableau.
PILIERS = ["data", "trend_sr", "fair_value", "liquidity", "ote_ob", "candle"]


def scan(symboles: list[str] | None = None, *, prod: bool = False,
         ltf: str = "M15", htf: str = "H4", bars: int = 400) -> dict:
    """Fait passer chaque symbole dans la chaîne complète, SANS LLM ni exécution.

    C'est le seul appel coûteux de ce module (lecture MT5 + calcul des features).
    Il reste déterministe et gratuit : la délibération n'est pas invoquée.
    """
    from titanium.data.mt5_vendor import get_rates
    from titanium.edge import EdgeBook, TradeJournal, context_from_feats
    from titanium.features.builder import build_feats, risk_context_from
    from titanium.gates import confluence_gate
    from titanium.orchestrator import OrchestratorConfig, run_once

    syms = symboles or SYMBOLES_DEFAUT
    cfg = OrchestratorConfig(require_edge=prod, deliberate=False, execute=False)

    equity, devise = 0.0, ""
    try:
        a = account()
        equity, devise = a["equity"], a["currency"]
    except Exception:  # noqa: BLE001
        pass

    livre = EdgeBook(journal=TradeJournal(
        Path(_config()["results_dir"]).parent / "trades.ndjson"))

    lignes = []
    for sym in syms:
        ligne = {"symbol": sym}
        try:
            feats = build_feats(get_rates(sym, ltf, bars), get_rates(sym, htf, bars))
        except Exception as exc:  # noqa: BLE001
            ligne.update(error=f"{type(exc).__name__}", verdict="—",
                         reason="données indisponibles")
            lignes.append(ligne)
            continue

        d = confluence_gate.evaluate(feats, require_edge=prod)
        ctx = risk_context_from(feats, equity=equity or 1000.0, risk_pct=1.0)
        out = run_once(sym, feats, ctx, config=cfg)
        trace = feats.get("_trace") or {}
        edge_v = livre.verdict_for(context_from_feats(sym, feats))

        ligne.update({
            "verdict": out.gate_verdict or "—",
            "code": out.gate_code,
            "reason": out.reason,
            "stopped_at": out.stopped_at,
            "side": feats.get("setup_side"),
            "family": feats.get("setup_family"),
            "trend": feats.get("trend"),
            "pillars": [{"name": g.name, "passed": g.passed, "reason": g.reason}
                        for g in d.gates],
            "risk_verdict": out.risk_verdict,
            "risk_money": out.risk_money,
            "stop_distance": out.stop_distance,
            "conviction": out.conviction,
            "price": trace.get("price"),
            "atr": trace.get("atr"),
            "notes": {"sr": trace.get("sr"), "fair_value": trace.get("fair_value"),
                      "ote": trace.get("ote"),
                      "candles": trace.get("candle_patterns", []),
                      "pending": trace.get("candle_pending", [])},
            "edge": {"ok": edge_v.edge_ok, "samples": edge_v.samples,
                     "expectancy_r": edge_v.expectancy_r, "reason": edge_v.reason},
            "trace": out.trace,
        })
        lignes.append(ligne)

    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": "prod" if prod else "explore",
        "ltf": ltf, "htf": htf,
        "equity": equity, "currency": devise,
        "rows": lignes,
        "entries": sum(1 for r in lignes if r.get("verdict") == "ENTER"),
    }


# ═════════════════════════ graphique par actif ═══════════════════════════════

#: Graphiques récemment servis. La boucle sature MT5 en continu ; sans
#: cache, `/api/chart` a été mesuré à plus de 120 s — le tableau de bord
#: devenait inutilisable. 45 s est bien plus court qu'une barre M15 : on ne
#: montre jamais un prix périmé au-delà d'un tiers de bougie.
_CACHE_CHART: dict = {}
_TTL_CHART = 45.0


def chart(symbole: str, *, timeframe: str = "M15", barres: int = 180) -> dict:
    """Chandeliers + ce que le bot a vu sur cet actif, en un seul appel.

    Un aller-retour unique : deux requêtes séparées renverraient des instants
    différents, et les zones ne colleraient plus aux bougies.
    """
    from titanium.bridge.mt5_zones import Plan, zones_depuis_features
    from titanium.data.mt5_vendor import get_rates, mt5_session
    from titanium.edge import asset_class_of, context_from_decision
    from titanium.features.builder import build_feats
    from titanium.gates import confluence_gate as cg

    sym = str(symbole or "").upper().strip()
    if not sym:
        return {"error": "symbole manquant"}

    barres = max(40, min(int(barres), 600))

    import time  # noqa: PLC0415 — utilisé pour le cache et les réessais
    cle = (sym, timeframe.upper(), barres)
    vu = _CACHE_CHART.get(cle)
    if vu and time.time() - vu[0] < _TTL_CHART:
        return vu[1]

    from titanium.data.mt5_vendor import get_rates_cache

    # Un seul essai suffit rarement quand la boucle sature MT5 :
    # `symbol_info` rend None une seconde puis répond normalement. Sans
    # réessai, un symbole parfaitement valide s'affiche comme inconnu.
    derniere = None
    for essai in range(3):
        try:
            with mt5_session():
                ltf = get_rates(sym, timeframe, barres)
                htf = get_rates_cache(sym, "H4", 400)
            break
        except Exception as exc:  # noqa: BLE001
            derniere = exc
            if essai < 2:
                time.sleep(0.6 * (essai + 1))
    else:
        return {"symbol": sym, "timeframe": timeframe, "candles": [],
                "error": f"{sym} illisible — {type(derniere).__name__}. "
                         "MT5 est saturé par le balayage ; réessaie."}

    feats = build_feats(ltf, htf, with_indicators=True)
    d = cg.evaluate(feats, require_edge=False)
    trace = feats.get("_trace") or {}
    prix = float(trace.get("price") or 0.0)

    # Le plan est reconstruit avec le R:R en vigueur dans la boucle, pour que
    # le graphique montre ce que le bot ferait, pas une convention d'affichage.
    rr = _const_boucle("RR_AFFICHE", 3.0)
    stop = 0.0
    try:
        from titanium.features.builder import risk_context_from
        stop = float((risk_context_from(feats, equity=1000.0, risk_pct=1.0)
                      or {}).get("stop_distance") or 0.0)
    except Exception:  # noqa: BLE001
        pass
    if not stop:
        stop = float(trace.get("atr_ltf") or 0.0) * 1.5

    sens = d.side or 0
    plan = Plan(side=sens)
    if prix and stop and sens:
        plan = Plan(side=sens, entry=prix, sl=prix - sens * stop,
                    tp=prix + sens * stop * rr)

    charge = zones_depuis_features(
        sym, feats, timeframe=timeframe, verdict=d.verdict, code=d.code,
        pillars=[{"name": g.name, "passed": g.passed} for g in d.gates],
        plan=plan)

    bougies = [{
        "t": str(idx), "o": float(r["open"]), "h": float(r["high"]),
        "l": float(r["low"]), "c": float(r["close"]),
        "v": float(r.get("v", 0) or 0),
    } for idx, r in ltf.tail(barres).iterrows()]

    sortie = {
        "symbol": sym, "timeframe": timeframe,
        "candles": bougies,
        "zones": [z.__dict__ if hasattr(z, "__dict__") else z
                  for z in (charge.zones or [])],
        "plan": {"side": plan.side, "entry": plan.entry, "sl": plan.sl,
                 "tp": plan.tp, "rr": rr},
        "verdict": d.verdict, "code": d.code, "side": d.side,
        "family": d.setup_family,
        "support_passed": d.support_passed, "quorum": d.quorum,
        "pillars": [{"name": g.name, "passed": g.passed, "reason": g.reason}
                    for g in d.gates],
        "context": context_from_decision(sym, d).key(),
        "asset_class": asset_class_of(sym),
        "indicators": dict(trace.get("indicators") or {}),
        "price": prix,
    }
    _CACHE_CHART[cle] = (time.time(), sortie)
    return sortie


#: Catalogue mis en cache. La boucle interroge MT5 pour 148 symboles toutes
#: les 60 s ; un appel concurrent se met dans la file et a été mesuré à 86 s.
#: Le catalogue ne bouge quasiment jamais — le relire à chaque affichage
#: rendrait le tableau de bord inutilisable pour une information figée.
_CACHE_UNIVERS: dict = {"at": 0.0, "liste": []}
_TTL_UNIVERS = 600.0


def univers_disponible() -> list:
    """Symboles proposables dans le sélecteur, positions en tête."""
    import time

    from titanium.data.mt5_vendor import mt5_session

    maintenant = time.time()
    frais = maintenant - _CACHE_UNIVERS["at"] < _TTL_UNIVERS
    try:
        import MetaTrader5 as mt5  # noqa: N813
        with mt5_session():
            # Les positions, elles, sont relues à chaque fois : elles changent
            # et l'utilisateur veut les voir en tête du sélecteur.
            ouverts = [p.symbol for p in (mt5.positions_get() or [])]
            if not frais or not _CACHE_UNIVERS["liste"]:
                _CACHE_UNIVERS["liste"] = sorted(
                    s.name for s in (mt5.symbols_get() or []) if s.visible)
                _CACHE_UNIVERS["at"] = maintenant
    except Exception:  # noqa: BLE001
        return list(_CACHE_UNIVERS["liste"])
    return list(dict.fromkeys(ouverts + _CACHE_UNIVERS["liste"]))


def promotion() -> dict:
    """Avancement des cellules vers l'ouverture du mode PROD."""
    from titanium.analysis.promotion import MIN_TRADES_CELLULE, evaluate_cells
    from titanium.analysis.promotion_registry import actives
    from titanium.edge import TradeJournal

    chemin = RACINE / "results" / "trades.ndjson"
    journal = TradeJournal(chemin)
    trades = journal.read_all()
    cellules = evaluate_cells(trades, rejected_lines=journal.rejected_lines)
    promues = actives(RACINE / "results" / "promotions.json")

    return {
        "requis": MIN_TRADES_CELLULE,
        "total_trades": len(trades),
        "journal_rejected": journal.rejected_lines,
        "strate_haute": sum(1 for t in trades if t.support_pillars >= 3),
        "net_exact": sum(1 for t in trades if t.exact_net),
        "cout_exact": sum(1 for t in trades if t.exact_cost),
        "sans_classe": sum(1 for t in trades if not t.asset_class),
        "promues": list(promues),
        "cellules": [{
            "cell": c.cell, "n": c.n,
            "expectancy_r": round(c.expectancy_r, 4),
            "lower_95_r": round(c.lower_95_r, 4)
            if c.lower_95_r != float("-inf") else None,
            "profit_factor": (round(c.profit_factor, 2)
                              if c.profit_factor != float("inf") else None),
            "eligible": c.eligible,
            "bloquants": c.bloquants,
        } for c in sorted(cellules, key=lambda x: -x.n)],
    }
