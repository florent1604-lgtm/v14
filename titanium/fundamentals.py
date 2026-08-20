"""Analyse fondamentale causale et deterministe, reservee a la recherche SHADOW.

Ce module ne telecharge rien, ne lit aucun secret et ne produit aucune permission
de trading. Il transforme des faits deja normalises en une photographie explicable
par actif. Le branchement dans ``RiskGate`` est volontairement absent tant que le
signal n'a pas ete valide hors echantillon apres couts.

Deux horloges sont conservees pour chaque fait :

``event_at``
    instant economique de la publication ou de l'evenement ;
``available_at``
    premier instant auquel V14 pouvait effectivement connaitre l'information.

Une revision est une nouvelle ligne append-only avec un nouvel identifiant et une
nouvelle heure de disponibilite. L'historique ne doit jamais etre reecrit.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1
SHADOW_MODE = "SHADOW_ONLY"

_CURRENCIES = {
    "AUD",
    "BRL",
    "CAD",
    "CHF",
    "CLP",
    "CNH",
    "COP",
    "CZK",
    "EUR",
    "GBP",
    "HKD",
    "HUF",
    "IDR",
    "INR",
    "JPY",
    "KRW",
    "MXN",
    "NOK",
    "NZD",
    "PLN",
    "RON",
    "SEK",
    "SGD",
    "THB",
    "TWD",
    "USD",
    "ZAR",
}

_CRYPTO = {
    "AAVE",
    "ADA",
    "AVAX",
    "BAT",
    "BCH",
    "BNB",
    "BTC",
    "COMP",
    "DOGE",
    "DOT",
    "ETH",
    "LINK",
    "LTC",
    "MANA",
    "MATIC",
    "MKR",
    "SAND",
    "SOL",
    "SUSHI",
    "UNI",
    "XLM",
    "XRP",
    "XTZ",
}

_INDEX_EXPOSURES: dict[str, dict[str, float]] = {
    "USTECH": {"US_TECH": 1.0, "US_EQUITY": 0.5},
    "NAS100": {"US_TECH": 1.0, "US_EQUITY": 0.5},
    "US500": {"US_EQUITY": 1.0},
    "SP": {"US_EQUITY": 1.0},
    "US30": {"US_EQUITY": 1.0},
    "DJ30": {"US_EQUITY": 1.0},
    "US2000": {"US_EQUITY": 1.0, "US_SMALL_CAP": 0.5},
    "UK100": {"UK_EQUITY": 1.0},
    "GER40": {"DE_EQUITY": 1.0, "EU_EQUITY": 0.5},
    "DAX40": {"DE_EQUITY": 1.0, "EU_EQUITY": 0.5},
    "EU50": {"EU_EQUITY": 1.0},
    "FRA40": {"FR_EQUITY": 1.0, "EU_EQUITY": 0.5},
    "CAC40": {"FR_EQUITY": 1.0, "EU_EQUITY": 0.5},
    "SPA35": {"ES_EQUITY": 1.0, "EU_EQUITY": 0.5},
    "AUS200": {"AU_EQUITY": 1.0},
    "SPI200": {"AU_EQUITY": 1.0},
    "JPN225": {"JP_EQUITY": 1.0},
    "NK225": {"JP_EQUITY": 1.0},
    "HK50": {"HK_EQUITY": 1.0, "CN_EQUITY": 0.5},
    "HSI": {"HK_EQUITY": 1.0, "CN_EQUITY": 0.5},
    "CHINA50": {"CN_EQUITY": 1.0},
    "CN50": {"CN_EQUITY": 1.0},
    "SWI20": {"CH_EQUITY": 1.0},
    "NETH25": {"NL_EQUITY": 1.0, "EU_EQUITY": 0.5},
}

_COMMODITY_EXPOSURES: dict[str, dict[str, float]] = {
    "USOIL": {"OIL": 1.0, "USD": -0.25},
    "WTI": {"OIL": 1.0, "USD": -0.25},
    "UKOIL": {"OIL": 1.0, "USD": -0.25},
    "BRENT": {"OIL": 1.0, "USD": -0.25},
    "NATGAS": {"NATGAS": 1.0, "USD": -0.15},
    "COFFEE": {"COFFEE": 1.0, "USD": -0.25},
    "COCOA": {"COCOA": 1.0, "USD": -0.25},
    "SUGAR": {"SUGAR": 1.0, "USD": -0.25},
    "COTTON": {"COTTON": 1.0, "USD": -0.25},
    "WHEAT": {"WHEAT": 1.0, "USD": -0.25},
    "CORN": {"CORN": 1.0, "USD": -0.25},
    "SOYBEAN": {"SOYBEAN": 1.0, "USD": -0.25},
}


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
    else:
        parsed = value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp sans fuseau horaire")
    return parsed.astimezone(timezone.utc)


def _bounded(value: float, *, name: str, low: float, high: float) -> float:
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} doit etre fini dans [{low}, {high}]")
    return result


@dataclass(frozen=True)
class FundamentalRecord:
    """Fait fondamental versionne, causal et independant de toute execution.

    ``factors`` contient des contributions signees deja normalisees dans
    ``[-1, 1]``. Elles restent explicites car une surprise d'inflation peut etre
    positive pour une devise et negative pour un indice : aucune regle naive
    universelle n'est encodee ici.
    """

    record_id: str
    kind: str
    category: str
    event_at: datetime
    available_at: datetime
    factors: Mapping[str, float]
    confidence: float = 1.0
    half_life_hours: float = 24.0
    event_risk: float = 0.0
    risk_before_minutes: int = 0
    risk_after_minutes: int = 0
    source: str = ""

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("record_id vide")
        if self.kind not in {"release", "scheduled_event"}:
            raise ValueError("kind doit valoir release ou scheduled_event")
        object.__setattr__(self, "event_at", _utc(self.event_at))
        object.__setattr__(self, "available_at", _utc(self.available_at))
        object.__setattr__(
            self,
            "confidence",
            _bounded(self.confidence, name="confidence", low=0.0, high=1.0),
        )
        half_life = float(self.half_life_hours)
        if not math.isfinite(half_life) or half_life <= 0:
            raise ValueError("half_life_hours doit etre strictement positif")
        object.__setattr__(self, "half_life_hours", half_life)
        object.__setattr__(
            self,
            "event_risk",
            _bounded(self.event_risk, name="event_risk", low=0.0, high=1.0),
        )
        if self.risk_before_minutes < 0 or self.risk_after_minutes < 0:
            raise ValueError("les fenetres de risque ne peuvent pas etre negatives")
        cleaned = {}
        for factor, value in dict(self.factors).items():
            name = str(factor).strip().upper()
            if not name:
                raise ValueError("nom de facteur vide")
            cleaned[name] = _bounded(
                value,
                name=f"facteur {name}",
                low=-1.0,
                high=1.0,
            )
        if self.kind == "scheduled_event" and cleaned:
            raise ValueError("un evenement planifie ne porte pas de direction")
        object.__setattr__(self, "factors", cleaned)

    @classmethod
    def from_dict(cls, raw: Mapping) -> FundamentalRecord:
        return cls(
            record_id=str(raw["record_id"]),
            kind=str(raw["kind"]),
            category=str(raw.get("category") or ""),
            event_at=raw["event_at"],
            available_at=raw["available_at"],
            factors=raw.get("factors") or {},
            confidence=raw.get("confidence", 1.0),
            half_life_hours=raw.get("half_life_hours", 24.0),
            event_risk=raw.get("event_risk", 0.0),
            risk_before_minutes=int(raw.get("risk_before_minutes", 0)),
            risk_after_minutes=int(raw.get("risk_after_minutes", 0)),
            source=str(raw.get("source") or ""),
        )


@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    as_of: datetime
    status: str
    direction_score: float | None
    confidence: float
    event_risk: float
    matched_releases: int
    matched_events: int
    factors: Mapping[str, float]
    reasons: tuple[str, ...]
    mode: str = SHADOW_MODE

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": self.mode,
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "status": self.status,
            "direction_score": self.direction_score,
            "confidence": self.confidence,
            "event_risk": self.event_risk,
            "matched_releases": self.matched_releases,
            "matched_events": self.matched_events,
            "factors": dict(self.factors),
            "reasons": list(self.reasons),
            # Invariant de phase : cette sortie ne peut pas commander RiskGate.
            "would_block": False,
            "would_reduce": False,
        }


def _normalise_symbol(symbol: str) -> str:
    value = symbol.upper().strip()
    for suffix in (".FS", ".S"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return re.sub(r"[^A-Z0-9]", "", value)


def infer_exposures(symbol: str) -> dict[str, float]:
    """Rend les facteurs economiques auxquels un symbole est expose.

    Pour le FX, le calcul est bilateral : positif sur la devise de base et
    negatif sur la devise de cotation. Les alias d'indices et de matieres
    premieres sont regroupes afin de ne pas inventer des edges independants.
    """
    normal = _normalise_symbol(symbol)
    if normal in _INDEX_EXPOSURES:
        return dict(_INDEX_EXPOSURES[normal])
    if normal in _COMMODITY_EXPOSURES:
        return dict(_COMMODITY_EXPOSURES[normal])

    for metal, factor in {
        "XAU": "GOLD",
        "XAG": "SILVER",
        "XPT": "PLATINUM",
        "XPD": "PALLADIUM",
    }.items():
        if normal.startswith(metal):
            result = {factor: 1.0}
            quote = normal[3:6]
            if quote in _CURRENCIES:
                result[quote] = -1.0
            return result

    for crypto in sorted(_CRYPTO, key=len, reverse=True):
        if normal.startswith(crypto):
            result = {"CRYPTO": 1.0, crypto: 1.0}
            quote = normal[len(crypto) : len(crypto) + 3]
            if quote in _CURRENCIES:
                result[quote] = -1.0
            return result

    if len(normal) >= 6:
        base, quote = normal[:3], normal[3:6]
        if base in _CURRENCIES and quote in _CURRENCIES:
            return {base: 1.0, quote: -1.0}
    return {}


def _event_risk(record: FundamentalRecord, as_of: datetime) -> float:
    before = record.event_at - timedelta(minutes=record.risk_before_minutes)
    after = record.event_at + timedelta(minutes=record.risk_after_minutes)
    if before <= as_of <= after:
        return record.event_risk * record.confidence
    return 0.0


def score_fundamentals(
    symbol: str,
    records: Iterable[FundamentalRecord],
    *,
    as_of: datetime | str,
    exposures: Mapping[str, float] | None = None,
) -> FundamentalSnapshot:
    """Agrege les faits disponibles sans fuite du futur.

    Le score est vu du cote LONG de ``symbol``. Une valeur positive soutient le
    long, une valeur negative le short. ``None`` signifie donnees inconnues et
    ne doit jamais etre remplace silencieusement par zero.
    """
    decision_at = _utc(as_of)
    exposure_map = {
        str(k).upper(): float(v)
        for k, v in (exposures if exposures is not None else infer_exposures(symbol)).items()
        if float(v) != 0.0
    }
    weighted_sum = 0.0
    total_weight = 0.0
    matched_releases = 0
    matched_events = 0
    max_event_risk = 0.0
    factor_totals: dict[str, float] = {}
    seen: set[str] = set()

    ordered = sorted(records, key=lambda r: (r.available_at, r.record_id))
    for record in ordered:
        if record.record_id in seen:
            raise ValueError(f"record_id duplique: {record.record_id}")
        seen.add(record.record_id)
        if record.available_at > decision_at:
            continue

        active_risk = _event_risk(record, decision_at)
        if active_risk > 0:
            matched_events += 1
            max_event_risk = max(max_event_risk, active_risk)

        if record.kind != "release" or record.event_at > decision_at:
            continue
        contributions = {
            factor: record.factors[factor] * weight
            for factor, weight in exposure_map.items()
            if factor in record.factors
        }
        if not contributions:
            continue
        age_hours = max(0.0, (decision_at - record.event_at).total_seconds() / 3600.0)
        decay = math.exp(-math.log(2.0) * age_hours / record.half_life_hours)
        weight = record.confidence * decay
        if weight <= 0:
            continue
        raw = max(-1.0, min(1.0, sum(contributions.values())))
        weighted_sum += raw * weight
        total_weight += weight
        matched_releases += 1
        for factor, contribution in contributions.items():
            factor_totals[factor] = factor_totals.get(factor, 0.0) + contribution * weight

    reasons = []
    if not exposure_map:
        reasons.append("EXPOSITION_INCONNUE")
    if matched_releases == 0:
        reasons.append("AUCUNE_PUBLICATION_CAUSALE")
        score = None
        confidence = 0.0
        status = "event_risk" if max_event_risk > 0 else "unknown"
    else:
        score = max(-1.0, min(1.0, weighted_sum / total_weight))
        confidence = min(1.0, total_weight / 2.0)
        status = "neutral" if abs(score) < 0.10 else "directional"
        if max_event_risk > 0:
            status = "event_risk"
    if max_event_risk > 0:
        reasons.append("FENETRE_EVENEMENT_ACTIVE")

    return FundamentalSnapshot(
        symbol=symbol,
        as_of=decision_at,
        status=status,
        direction_score=None if score is None else round(score, 6),
        confidence=round(confidence, 6),
        event_risk=round(max_event_risk, 6),
        matched_releases=matched_releases,
        matched_events=matched_events,
        factors={k: round(v, 6) for k, v in sorted(factor_totals.items())},
        reasons=tuple(reasons),
    )
