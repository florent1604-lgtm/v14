"""Collecte fondamentale multi-source et arbitrage local Qwen/Ollama.

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
from dataclasses import dataclass
from datetime import date

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
    for call in calls:
        try:
            evidence.extend(call())
        except Exception:  # noqa: BLE001 - une source ne condamne pas les autres
            continue
    _CACHE[symbol] = (now, evidence)
    return evidence


def _json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def analyse(symbol: str, side: int, mechanical_summary: str, *,
            decision_ref: str = "", context_digest: str = "",
            model_version: str = MODEL_VERSION,
            prompt_version: str = PROMPT_VERSION) -> dict:
    cache_key = decision_ref or digest({
        "symbol": symbol, "side": side, "context_digest": context_digest,
        "mechanical_summary": mechanical_summary,
        "model_version": model_version, "prompt_version": prompt_version,
    })
    cached = _ANALYSIS_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < _TTL_S:
        return dict(cached[1])
    evidence = collect(symbol)
    evidence_payload = [
        {"source": item.source, "text": item.text,
         "observed_at": item.observed_at} for item in _balanced(evidence)
    ]
    evidence_digest = digest({"evidence": evidence_payload})
    if len(evidence) < 2:
        return {"action": "WAIT", "confidence": 0.0,
                "summary": "preuves fondamentales insuffisantes",
                "sources": [e.source for e in evidence],
                "evidence_digest": evidence_digest,
                "model_version": model_version,
                "prompt_version": prompt_version}
    prompt = {
        "role": "MT5 DEMO entry risk gate. JSON only.",
        "prompt_version": prompt_version,
        "rules": "ALLOW when at least two fresh sources do not explicitly contradict the side. "
                 "Lack of directional news alone is neutral and means ALLOW, not WAIT. "
                 "Use WAIT/BLOCK only for an explicit conflict, stale data, or event shock. "
                 "Never invent facts, create orders, or change stop-loss.",
        "symbol": symbol,
        "mechanical_side": "long" if side > 0 else "short",
        "mechanical_summary": mechanical_summary[:500],
        "evidence": [{**item, "text": item["text"][:180]}
                     for item in evidence_payload],
        "schema": {"action": "ALLOW|WAIT|BLOCK", "confidence": "0..1",
                   "summary": "French, max 240 chars"},
    }
    body = json.dumps({"model": model_version, "stream": False,
                       "format": "json", "prompt": json.dumps(prompt),
                       "keep_alive": -1,
                       "options": {"temperature": 0, "num_predict": 60,
                                   "num_ctx": 2048}}).encode()
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                     data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as response:
            outer = json.loads(response.read())
        result = _json_object(str(outer.get("response", "")))
        action = str(result.get("action", "WAIT")).upper()
        if action not in {"ALLOW", "WAIT", "BLOCK"}:
            action = "WAIT"
        answer = {"action": action,
                  "confidence": max(0.0, min(1.0, float(result.get("confidence", 0.0)))),
                  "summary": str(result.get("summary", ""))[:240],
                  "sources": sorted({e.source for e in evidence}),
                  "evidence_digest": evidence_digest,
                  "model_version": model_version,
                  "prompt_version": prompt_version}
        _ANALYSIS_CACHE[cache_key] = (time.time(), answer)
        return answer
    except Exception as exc:  # noqa: BLE001
        return {"action": "WAIT", "confidence": 0.0,
                "summary": f"Qwen local indisponible: {type(exc).__name__}",
                "sources": sorted({e.source for e in evidence}),
                "evidence_digest": evidence_digest,
                "model_version": model_version,
                "prompt_version": prompt_version}
