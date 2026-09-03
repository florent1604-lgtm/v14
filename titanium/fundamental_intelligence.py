"""Collecte fondamentale multi-source et arbitrage local GLM/Ollama.

Toutes les sources sont publiques et la collecte est hors du chemin MT5. Une
panne de source ou du modele rend WAIT (fail-closed), jamais une autorisation.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from titanium.organism.contracts import (
    MODEL_VERSION,
    PROMPT_VERSION,
    digest,
)


@dataclass(frozen=True)
class Evidence:
    source: str
    text: str
    observed_at: str = ""


_CACHE: dict[str, tuple[float, list[Evidence]]] = {}
_ANALYSIS_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 900


def _get(url: str, timeout: float = 8.0) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Titanium-V14-DEMO/1.0 research contact local",
        "Accept": "application/json, application/xml, text/xml, text/csv",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(300_000)


def _rss(source: str, url: str, limit: int = 5) -> list[Evidence]:
    root = ET.fromstring(_get(url))
    out = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        if title:
            out.append(Evidence(source, title[:240], date))
    return out


def _crypto(symbol: str) -> list[Evidence]:
    ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
           "AAVE": "aave", "ADA": "cardano", "XRP": "ripple",
           "DOGE": "dogecoin", "DOT": "polkadot", "LINK": "chainlink"}
    coin = next((v for k, v in ids.items() if symbol.upper().startswith(k)), None)
    if not coin:
        return []
    url = ("https://api.coingecko.com/api/v3/simple/price?ids=" + coin
           + "&vs_currencies=usd&include_24hr_change=true&include_market_cap=true")
    data = json.loads(_get(url))
    row = data.get(coin, {})
    return [Evidence("CoinGecko", json.dumps(row, sort_keys=True)[:240])] if row else []


def _ecb_fx(symbol: str) -> list[Evidence]:
    pair = "".join(c for c in symbol.upper() if c.isalpha())[:6]
    if len(pair) != 6:
        return []
    base, quote = pair[:3], pair[3:]
    url = ("https://data-api.ecb.europa.eu/service/data/EXR/D."
           f"{quote}.{base}.SP00.A?lastNObservations=2&format=csvdata")
    lines = _get(url).decode("utf-8", errors="replace").splitlines()
    rows = [line for line in lines[1:] if line.strip()]
    return [Evidence("ECB-Data", row[:400]) for row in rows[-2:]]


def _cot(symbol: str) -> list[Evidence]:
    terms = {"XAU": "GOLD", "XAG": "SILVER", "USOIL": "CRUDE OIL",
             "WTI": "CRUDE OIL", "UKOIL": "BRENT", "COFFEE": "COFFEE",
             "COCOA": "COCOA", "CORN": "CORN", "WHEAT": "WHEAT"}
    term = next((v for k, v in terms.items() if k in symbol.upper()), None)
    if not term:
        return []
    where = f"upper(market_and_exchange_names) like '%{term}%'"
    query = urllib.parse.urlencode({"$where": where, "$limit": 3,
                                    "$order": "report_date_as_yyyy_mm_dd DESC"})
    rows = json.loads(_get("https://publicreporting.cftc.gov/resource/72hh-3qpy.json?" + query))
    row = next((candidate for candidate in rows
                if term in str(candidate.get("market_and_exchange_names", "")).upper()), None)
    if row is None:
        return []
    keep = {k: row.get(k) for k in ("market_and_exchange_names",
            "report_date_as_yyyy_mm_dd", "open_interest_all",
            "m_money_positions_long_all", "m_money_positions_short_all",
            "change_in_open_interest_all") if row.get(k) is not None}
    return [Evidence("CFTC-COT", json.dumps(keep, sort_keys=True)[:400],
                     str(row.get("report_date_as_yyyy_mm_dd", "")))]


def _fred(symbol: str) -> list[Evidence]:
    """Indicateurs macro officiels adaptes a la classe d'actif."""
    if not os.getenv("FRED_API_KEY"):
        return []
    from titanium.edge import asset_class_of
    from tradingagents.dataflows import fred

    series_by_class = {
        "fx": ("dollar_index", "10y_treasury", "fed_funds"),
        "metaux": ("10y_treasury", "dollar_index", "inflation_expectations"),
        "energie": ("dollar_index", "10y_treasury"),
        "indices": ("vix", "10y_treasury", "fed_funds"),
        "crypto": ("fed_funds", "vix", "dollar_index"),
    }
    indicators = series_by_class.get(asset_class_of(symbol),
                                     ("10y_treasury", "dollar_index"))
    out: list[Evidence] = []
    for indicator in indicators:
        try:
            report = fred.get_macro_data(indicator, date.today().isoformat(), 120)
            useful = [line.strip() for line in report.splitlines()
                      if line.startswith("## FRED:") or "**Latest:**" in line]
            if useful:
                out.append(Evidence(f"FRED:{indicator}", " ".join(useful)[:500]))
        except Exception:  # noqa: BLE001 - serie optionnelle
            continue
    return out


def _eia(symbol: str) -> list[Evidence]:
    """Prix energie EIA via la route officielle de compatibilite API v2."""
    key = os.getenv("EIA_API_KEY")
    if not key:
        return []
    upper = symbol.upper()
    series = next((series_id for markers, series_id in (
        (("USOIL", "WTI", "WTI.FS"), "PET.RWTC.D"),
        (("UKOIL", "BRENT", "BRENT.FS"), "PET.RBRTE.D"),
        (("NATGAS", "NGAS"), "NG.RNGWHHD.D"),
    ) if any(marker in upper for marker in markers)), None)
    if series is None:
        return []
    query = urllib.parse.urlencode({
        "api_key": key, "length": 3,
        "sort[0][column]": "period", "sort[0][direction]": "desc",
    })
    data = json.loads(_get(f"https://api.eia.gov/v2/seriesid/{series}?{query}"))
    rows = data.get("response", {}).get("data", [])[:3]
    if not rows:
        return []
    compact = [{k: row.get(k) for k in ("period", "value", "units",
                "series-description") if row.get(k) is not None} for row in rows]
    return [Evidence(f"EIA:{series}", json.dumps(compact, sort_keys=True)[:600],
                     str(rows[0].get("period", "")))]


def _balanced(evidence: list[Evidence], limit: int = 8) -> list[Evidence]:
    """Evite qu'un flux RSS monopolise tout le contexte du cerveau local."""
    chosen: list[Evidence] = []
    seen: set[str] = set()
    for item in evidence:
        if item.source not in seen:
            chosen.append(item)
            seen.add(item.source)
            if len(chosen) == limit:
                return chosen
    for item in evidence:
        if item not in chosen:
            chosen.append(item)
            if len(chosen) == limit:
                break
    return chosen


def collect(symbol: str) -> list[Evidence]:
    now = time.time()
    cached = _CACHE.get(symbol)
    if cached and now - cached[0] < _TTL_S:
        return cached[1]
    evidence: list[Evidence] = []
    calls = (
        lambda: _fred(symbol),
        lambda: _eia(symbol),
        lambda: _ecb_fx(symbol),
        lambda: _crypto(symbol),
        lambda: _cot(symbol),
        lambda: _rss("FederalReserve", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
        lambda: _rss("ECB", "https://mid.ecb.europa.eu/rss/mid.xml"),
    )
    def safe_call(call) -> list[Evidence]:
        try:
            return list(call())
        except Exception:  # noqa: BLE001 - une source ne condamne pas les autres
            return []

    # Les sources sont independantes et surtout bornees par le reseau. Les
    # attendre en parallele reduit la collecte au timeout le plus lent au lieu
    # de la somme de sept timeouts, sans changer leur ordre dans l'artefact.
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        for items in pool.map(safe_call, calls):
            evidence.extend(items)
    _CACHE[symbol] = (now, evidence)
    return evidence


def _json_object(text: str) -> dict:
    """Extract the first complete JSON object without accepting truncation.

    GLM4 may wrap an otherwise valid object in prose or a Markdown fence.  A
    greedy regular expression joined several objects and hid the real parse
    error; ``raw_decode`` stops exactly at the first complete object.
    """
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise json.JSONDecodeError("aucun objet JSON complet", cleaned, 0)


def _record_glm_failure(context: str, raw: str, outer: dict, exc: Exception) -> None:
    """Persist a bounded, secret-free diagnostic when local GLM is invalid."""
    try:
        path = Path(__file__).resolve().parent.parent / "results" / "glm_failures.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime_now_utc(),
            "context": context[:160],
            "error": type(exc).__name__,
            "done_reason": str(outer.get("done_reason", ""))[:40],
            "eval_count": int(outer.get("eval_count", 0) or 0),
            "response": str(raw or "")[:1200],
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - -- diagnostics never break the gate
        pass


def _ollama_json(*, prompt: str, schema: dict, model: str,
                 num_predict: int, timeout: float, context: str) -> dict:
    """Call local Ollama with a strict schema and return one JSON object."""
    body = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "prompt": prompt,
        "keep_alive": -1,
        "options": {
            "temperature": 0,
            "num_predict": int(num_predict),
            "num_ctx": 2048,
        },
    }).encode()
    outer: dict = {}
    raw = ""
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            outer = json.loads(response.read())
        raw = str(outer.get("response", ""))
        return _json_object(raw)
    except Exception as exc:
        _record_glm_failure(context, raw, outer, exc)
        raise


def _entry_answer(result: dict, *, evidence: list[Evidence],
                  evidence_digest: str, model_version: str,
                  prompt_version: str) -> dict:
    action = str(result.get("action", "WAIT")).upper()
    if action not in {"ALLOW", "WAIT", "BLOCK"}:
        action = "WAIT"
    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "action": action,
        "confidence": confidence,
        "summary": str(result.get("summary", ""))[:240],
        "sources": sorted({item.source for item in evidence}),
        "evidence_digest": evidence_digest,
        "model_version": model_version,
        "prompt_version": prompt_version,
    }


def analyse_batch(requests: list[dict]) -> list[dict]:
    """Analyse several entry candidates in one GLM generation.

    Collection remains parallel and cached.  Every candidate keeps its sealed
    reference; a missing, duplicated or malformed verdict becomes ``WAIT``.
    """
    if not requests:
        return []

    prepared: list[dict] = []
    answers: dict[str, dict] = {}
    unique_symbols = list(dict.fromkeys(str(row.get("symbol", "")) for row in requests))
    with ThreadPoolExecutor(max_workers=min(4, len(unique_symbols) or 1)) as pool:
        evidence_by_symbol = dict(zip(
            unique_symbols, pool.map(collect, unique_symbols), strict=False,
        ))

    for index, row in enumerate(requests):
        symbol = str(row.get("symbol", ""))
        side = int(row.get("side", 0) or 0)
        model_version = str(row.get("model_version", MODEL_VERSION) or MODEL_VERSION)
        prompt_version = str(row.get("prompt_version", PROMPT_VERSION) or PROMPT_VERSION)
        decision_ref = str(row.get("decision_ref", "")) or digest({
            "index": index,
            "symbol": symbol,
            "side": side,
            "context_digest": str(row.get("context_digest", "")),
            "mechanical_summary": str(row.get("mechanical_summary", "")),
            "model_version": model_version,
            "prompt_version": prompt_version,
        })
        cached = _ANALYSIS_CACHE.get(decision_ref)
        if cached and time.time() - cached[0] < _TTL_S:
            answers[decision_ref] = dict(cached[1])
            continue
        evidence = list(evidence_by_symbol.get(symbol, []))
        evidence_payload = [
            {"source": item.source, "text": item.text,
             "observed_at": item.observed_at}
            for item in _balanced(evidence, limit=4)
        ]
        evidence_digest = digest({"evidence": evidence_payload})
        common = {
            "decision_ref": decision_ref,
            "symbol": symbol,
            "side": side,
            "mechanical_summary": str(row.get("mechanical_summary", ""))[:240],
            "model_version": model_version,
            "prompt_version": prompt_version,
            "evidence": evidence,
            "evidence_payload": evidence_payload,
            "evidence_digest": evidence_digest,
        }
        if len(evidence) < 2:
            answers[decision_ref] = _entry_answer(
                {"action": "WAIT", "confidence": 0.0,
                 "summary": "preuves fondamentales insuffisantes"},
                evidence=evidence,
                evidence_digest=evidence_digest,
                model_version=model_version,
                prompt_version=prompt_version,
            )
        else:
            prepared.append(common)

    if prepared:
        refs = [item["decision_ref"] for item in prepared]
        schema = {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "array",
                    "minItems": len(refs),
                    "maxItems": len(refs),
                    "uniqueItems": True,
                    "items": {
                        "type": "object",
                        "properties": {
                            "decision_ref": {"type": "string", "enum": refs},
                            "action": {
                                "type": "string", "enum": ["ALLOW", "WAIT", "BLOCK"],
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "summary": {"type": "string"},
                        },
                        "required": ["decision_ref", "action", "confidence", "summary"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["verdicts"],
            "additionalProperties": False,
        }
        lines = [
            "Tu es la porte fondamentale d'un bot MT5 DEMO.",
            "Retourne uniquement l'objet JSON conforme au schema.",
            "Un verdict exactement par decision_ref, sans doublon.",
            "ALLOW si au moins deux sources recentes ne contredisent pas explicitement le sens.",
            "L'absence de nouvelle directionnelle est neutre: ALLOW, pas WAIT.",
            "WAIT ou BLOCK seulement pour conflit explicite, donnees perimees ou choc evenementiel.",
            "N'invente aucun fait. Resume en francais, 100 caracteres maximum.",
        ]
        for item in prepared:
            direction = "long" if item["side"] > 0 else "short"
            lines.append(
                f"CANDIDAT {item['decision_ref']} {item['symbol']} {direction} "
                f"MECANIQUE {item['mechanical_summary']}"
            )
            for fact in item["evidence_payload"]:
                lines.append(
                    f"FAIT {fact['source']} {fact['observed_at']} {fact['text'][:120]}"
                )
        try:
            parsed = _ollama_json(
                prompt="\n".join(lines),
                schema=schema,
                model=prepared[0]["model_version"],
                num_predict=min(700, 70 + 70 * len(prepared)),
                timeout=240,
                context=f"entry-batch:{','.join(refs)}",
            )
            raw_verdicts = parsed.get("verdicts", [])
            if not isinstance(raw_verdicts, list):
                raw_verdicts = []
            seen: set[str] = set()
            by_ref: dict[str, dict] = {}
            for row in raw_verdicts:
                if not isinstance(row, dict):
                    continue
                ref = str(row.get("decision_ref", ""))
                if ref in refs and ref not in seen:
                    by_ref[ref] = row
                    seen.add(ref)
            missing = [ref for ref in refs if ref not in by_ref]
            if missing:
                _record_glm_failure(
                    f"entry-batch-missing:{','.join(missing)}",
                    json.dumps(parsed, ensure_ascii=False),
                    {},
                    ValueError("verdict GLM absent ou duplique"),
                )
            for item in prepared:
                raw = by_ref.get(item["decision_ref"], {
                    "action": "WAIT",
                    "confidence": 0.0,
                    "summary": "verdict GLM absent ou non lie",
                })
                answer = _entry_answer(
                    raw,
                    evidence=item["evidence"],
                    evidence_digest=item["evidence_digest"],
                    model_version=item["model_version"],
                    prompt_version=item["prompt_version"],
                )
                answers[item["decision_ref"]] = answer
                _ANALYSIS_CACHE[item["decision_ref"]] = (time.time(), answer)
        except Exception as exc:  # noqa: BLE001 -- every candidate fails closed
            for item in prepared:
                answers[item["decision_ref"]] = _entry_answer(
                    {"action": "WAIT", "confidence": 0.0,
                     "summary": f"GLM local indisponible: {type(exc).__name__}"},
                    evidence=item["evidence"],
                    evidence_digest=item["evidence_digest"],
                    model_version=item["model_version"],
                    prompt_version=item["prompt_version"],
                )

    ordered = []
    for index, row in enumerate(requests):
        ref = str(row.get("decision_ref", "")) or digest({
            "index": index,
            "symbol": str(row.get("symbol", "")),
            "side": int(row.get("side", 0) or 0),
            "context_digest": str(row.get("context_digest", "")),
            "mechanical_summary": str(row.get("mechanical_summary", "")),
            "model_version": str(row.get("model_version", MODEL_VERSION) or MODEL_VERSION),
            "prompt_version": str(row.get("prompt_version", PROMPT_VERSION) or PROMPT_VERSION),
        })
        ordered.append(dict(answers[ref]))
    return ordered


def analyse(symbol: str, side: int, mechanical_summary: str, *,
            decision_ref: str = "", context_digest: str = "",
            model_version: str = MODEL_VERSION,
            prompt_version: str = PROMPT_VERSION) -> dict:
    return analyse_batch([{
        "symbol": symbol,
        "side": side,
        "mechanical_summary": mechanical_summary,
        "decision_ref": decision_ref,
        "context_digest": context_digest,
        "model_version": model_version,
        "prompt_version": prompt_version,
    }])[0]


def analyse_positions(reviews: list[dict], *,
                      model_version: str = MODEL_VERSION) -> list[dict]:
    """Evalue en un seul appel GLM l'etat des theses de positions ouvertes.

    La sortie ne contient aucune instruction MT5. Le gestionnaire applique
    ensuite ses propres gardes de fraicheur, de confirmation et de compte DEMO.
    """
    if not reviews:
        return []
    prepared = []
    evidence_by_ref: dict[str, list[Evidence]] = {}
    symbols = list(dict.fromkeys(str(row.get("symbol", "")) for row in reviews[:8]))
    with ThreadPoolExecutor(max_workers=min(2, len(symbols) or 1)) as pool:
        evidence_by_symbol = dict(zip(symbols, pool.map(collect, symbols), strict=False))
    for review in reviews[:8]:
        ref = str(review.get("request_ref", ""))
        if not ref:
            continue
        symbol = str(review.get("symbol", ""))
        evidence = _balanced(evidence_by_symbol.get(symbol, []), limit=6)
        evidence_by_ref[ref] = evidence
        prepared.append({
            "request_ref": ref,
            "symbol": str(review.get("symbol", "")),
            "side": "long" if int(review.get("side", 0) or 0) > 0 else "short",
            "fav_r": float(review.get("fav_r", 0.0) or 0.0),
            "peak_fav_r": float(review.get("peak_fav_r", 0.0) or 0.0),
            "mae_r": float(review.get("mae_r", 0.0) or 0.0),
            "context": dict(review.get("context") or {}),
            "evidence": [
                {"source": item.source, "text": item.text[:180],
                 "observed_at": item.observed_at}
                for item in evidence
            ],
        })
    if not prepared:
        return []
    lines = [
        "Return ONLY one minified JSON object. Never repeat this prompt. No markdown.",
        "Exact schema: {\"verdicts\":[{\"request_ref\":\"exact input ref\","
        "\"state\":\"CALM|CAUTION|FEAR|PANIC|UNKNOWN\","
        "\"confidence\":\"number from 0 to 1\",\"reason\":\"French max 120 chars\"}]}",
        "CALM means thesis intact. CAUTION means weakening but not invalidated.",
        "FEAR needs two independent facts against the side and confidence >= 0.75.",
        "PANIC needs an abrupt event or severe mechanical invalidation.",
        "Missing, stale or ambiguous facts mean UNKNOWN or CAUTION.",
        "You are advisory only. Never create/close orders or change a stop-loss.",
    ]
    for item in prepared:
        lines.append(
            f"POSITION {item['request_ref']} {item['symbol']} {item['side']} "
            f"fav_r={item['fav_r']:.3f} peak_r={item['peak_fav_r']:.3f} "
            f"mae_r={item['mae_r']:.3f}"
        )
        for fact in item["evidence"]:
            lines.append(
                f"FACT {fact['source']} {fact['observed_at']} {fact['text'][:120]}"
            )
    prompt = "\n".join(lines)
    body = json.dumps({
        "model": model_version,
        "stream": False,
        "think": False,
        "format": "json",
        "prompt": prompt,
        "keep_alive": -1,
        "options": {
            "temperature": 0,
            "num_predict": min(300, 80 + 45 * len(prepared)),
            "num_ctx": 2048,
        },
    }).encode()
    parsed: dict = {}
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            outer = json.loads(response.read())
        parsed = _json_object(str(outer.get("response", "")))
    except Exception:  # noqa: BLE001 - UNKNOWN fail-closed pour chaque ticket
        parsed = {}
    raw_verdicts = parsed.get("verdicts", [])
    if not isinstance(raw_verdicts, list):
        raw_verdicts = []
    if not raw_verdicts and "state" in parsed:
        raw_verdicts = [parsed]
    by_ref = {}
    unbound = []
    for row in raw_verdicts:
        if not isinstance(row, dict):
            continue
        ref = str(row.get("request_ref", ""))
        if ref in {item["request_ref"] for item in prepared}:
            by_ref[ref] = row
        elif ref:
            # GLM4 peut recopier toute la ligne POSITION dans ce champ. Le
            # digest exact reste alors present et permet une liaison causale
            # non ambigue, sans jamais rapprocher par symbole.
            matches = [item["request_ref"] for item in prepared
                       if item["request_ref"] in ref]
            if len(matches) == 1:
                by_ref[matches[0]] = row
            else:
                unbound.append(row)
        else:
            unbound.append(row)
    # GLM omet parfois la reference malgre le schema. L'ordre du tableau est
    # alors la seule liaison admissible; jamais de rapprochement par symbole.
    for item, row in zip(
        [candidate for candidate in prepared
         if candidate["request_ref"] not in by_ref],
        unbound,
        strict=False,
    ):
        by_ref[item["request_ref"]] = row
    rendered_at = datetime_now_utc()
    out = []
    for item in prepared:
        ref = item["request_ref"]
        raw = by_ref.get(ref, {})
        state = str(raw.get("state", "UNKNOWN")).upper()
        if state not in {"CALM", "CAUTION", "FEAR", "PANIC", "UNKNOWN"}:
            state = "UNKNOWN"
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        evidence = evidence_by_ref.get(ref, [])
        reason = str(raw.get("reason", ""))[:240]
        if reason.lower() in {"french max 120 chars", "french, max 120 chars"}:
            reason = ""
        out.append({
            "request_ref": ref,
            "ticket": next((str(r.get("ticket", "")) for r in reviews
                            if str(r.get("request_ref", "")) == ref), ""),
            "symbol": item["symbol"],
            "state": state,
            "confidence": confidence,
            "reason": reason,
            "sources": sorted({row.source for row in evidence}),
            "evidence_digest": digest({
                "evidence": [{"source": row.source, "text": row.text,
                              "observed_at": row.observed_at} for row in evidence],
            }),
            "rendered_at": rendered_at,
            "model_version": model_version,
            "prompt_version": "position-fear-v1",
        })
    return out


def datetime_now_utc() -> str:
    """Horloge isolee pour garder les artefacts faciles a tester."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
