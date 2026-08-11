"""Détecteurs Smart Money Concepts — OB, FVG, sweep de liquidité, bougie de rejet.

Porté de V12 (`poles/smc/smc_engine.py`). **Logique inchangée**, y compris les
deux correctifs de la revue Codex du 22/07/2026 qui sont la raison d'être de ce
module dans sa forme actuelle :

1. **FVG non comblées seulement.** `detect_fvg` rendait TOUTES les FVG de
   l'historique. Sur du M15 il y en a des dizaines par côté, la plupart déjà
   rebouchées — donc « FVG haussière » et « FVG baissière » étaient vraies en
   même temps, et le pilier liquidité restait figé à 0 (0/20 mesuré en réel).
   Une FVG rebouchée n'est plus un signal : le prix est repassé au travers.

2. **Tampon de sweep normalisé par l'ATR.** Le tampon fixe de 0,3 % n'a aucun
   sens transversal : il vaut ~9 ATR sur un indice calme et une fraction d'ATR
   sur une crypto volatile.

DIFFÉRENCE AVEC V12 : l'ATR est calculé nativement (moyenne de Wilder) au lieu
de passer par `pandas_ta`. Une dépendance de moins pour une formule de dix
lignes, et le résultat est identique.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Constantes issues de la config V12 (valeurs par défaut mesurées).
OB_FVG_ATR_MULT = 0.750
OB_FVG_PCT_FALLBACK = 0.004
FIB_LEVEL_LOW = 0.618
FIB_LEVEL_HIGH = 0.786
FIB_SWING_LOOKBACK = 60
LIQUIDITY_LOOKBACK = 50
LIQUIDITY_SWEEP_ATR_MULT = 0.25

BUY, SELL = "ACHAT", "VENTE"


def _is_buy(side: str) -> bool:
    return BUY in str(side)


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR courant (moyenne de Wilder). 0.0 si données insuffisantes.

    Wilder lisse en EMA d'alpha 1/period — c'est ce que fait `pandas_ta.atr`.
    """
    if df is None or len(df) < period + 1:
        return 0.0
    if not {"high", "low", "close"}.issubset(df.columns):
        return 0.0
    try:
        h = df["high"].astype(float)
        low = df["low"].astype(float)
        prev_close = df["close"].astype(float).shift(1)
        tr = pd.concat([h - low, (h - prev_close).abs(), (low - prev_close).abs()],
                       axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        val = float(atr.iloc[-1])
        return val if np.isfinite(val) else 0.0
    except Exception:  # noqa: BLE001 — un détecteur ne casse jamais la chaîne
        return 0.0


def compute_ema200(series: pd.Series) -> float:
    """EMA 200 des clôtures. NaN si l'historique est trop court."""
    if series is None or len(series) < 200:
        return float("nan")
    try:
        return float(series.astype(float).ewm(span=200, adjust=False).mean().iloc[-1])
    except Exception:  # noqa: BLE001
        return float("nan")


def compute_fib_tolerance(df: pd.DataFrame, price: float, atr_val: float) -> float:
    """Tolérance OB/FVG dynamique, dérivée de la zone Fibonacci [0.618–0.786]."""
    fallback = max(abs(price) * OB_FVG_PCT_FALLBACK, atr_val * OB_FVG_ATR_MULT)
    if df is None or len(df) < 10:
        return fallback
    try:
        tail = df.tail(FIB_SWING_LOOKBACK)
        sh, sl = float(tail["high"].max()), float(tail["low"].min())
        rng = sh - sl
        if rng < 1e-9:
            return fallback
        tol = abs((sl + FIB_LEVEL_HIGH * rng) - (sl + FIB_LEVEL_LOW * rng)) * 0.5
        return max(fallback, min(tol, rng * 0.15))
    except Exception:  # noqa: BLE001
        return fallback


def ob_status(df_after: pd.DataFrame, ob_top: float, ob_bot: float, side: str) -> str:
    """Statut d'un Order Block après sa création : intact / tested / broken."""
    if df_after is None or df_after.empty:
        return "intact"
    tested = False
    achat = _is_buy(side)
    for i in range(len(df_after)):
        ligne = df_after.iloc[i]
        c, lo, hi = float(ligne["close"]), float(ligne["low"]), float(ligne["high"])
        if achat:
            if c < ob_bot:
                return "broken"
            if lo <= ob_top and hi >= ob_bot:
                tested = True
        else:
            if c > ob_top:
                return "broken"
            if hi >= ob_bot and lo <= ob_top:
                tested = True
    return "tested" if tested else "intact"


def detect_fvg(df: pd.DataFrame, side: str) -> list[tuple[float, float]]:
    """Toutes les Fair Value Gaps du sens donné, comblées ou non.

    Utile pour la qualité d'un OB. **Ne jamais s'en servir pour décider** : voir
    le correctif n°1 en tête de module.
    """
    zones = []
    if df is None or len(df) < 3:
        return zones
    achat = _is_buy(side)
    for i in range(2, len(df)):
        h0 = float(df.iloc[i - 2]["high"])
        l0 = float(df.iloc[i - 2]["low"])
        l2 = float(df.iloc[i]["low"])
        h2 = float(df.iloc[i]["high"])
        if achat and h0 < l2:
            zones.append((h0, l2))
        elif not achat and l0 > h2:
            zones.append((h2, l0))
    return zones


def detect_fvg_unfilled(df: pd.DataFrame, side: str,
                        lookback: int = 100) -> list[tuple[float, float]]:
    """FVG encore OUVERTES dans la fenêtre récente. C'est celles-ci qui décident.

    Une zone n'est retenue que si sa borne de déséquilibre n'a jamais été
    franchie par une barre postérieure à sa création.
    """
    if df is None:
        return []
    n = len(df)
    if n < 3:
        return []
    debut = max(2, n - lookback)
    lows = df["low"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    achat = _is_buy(side)
    ouvertes: list[tuple[float, float]] = []
    for i in range(debut, n):
        h0, l0 = highs[i - 2], lows[i - 2]
        l2, h2 = lows[i], highs[i]
        if achat and h0 < l2:
            # comblée si une barre postérieure redescend sous la borne basse
            if i + 1 >= n or lows[i + 1:].min() > h0:
                ouvertes.append((float(h0), float(l2)))
        elif not achat and l0 > h2:
            # comblée si une barre postérieure remonte au-dessus de la borne haute
            if i + 1 >= n or highs[i + 1:].max() < l0:
                ouvertes.append((float(h2), float(l0)))
    return ouvertes


def has_ob_or_fvg_alignment(df: pd.DataFrame, side: str, price: float,
                            lookback: int = 50) -> tuple[bool, str, float]:
    """Le prix est-il dans/proche d'un OB ou d'une FVG du bon côté ?

    Returns:
        ``(aligné, statut, qualité 0-1)`` — statut ∈ intact / tested / fvg / none.
    """
    if df is None or len(df) < 5 or price <= 0:
        return False, "none", 0.0

    tail = df.tail(lookback).copy()
    atr_val = compute_atr(tail) or abs(price) * OB_FVG_PCT_FALLBACK
    tol = compute_fib_tolerance(tail, price, atr_val)
    fvg_zones = detect_fvg(tail, side)
    achat = _is_buy(side)

    for fvg_bot, fvg_top in fvg_zones:
        if abs(price - (fvg_bot + fvg_top) / 2.0) <= tol:
            return True, "fvg", 0.85

    for i in range(len(tail) - 2, 0, -1):
        ligne = tail.iloc[i]
        c, o = float(ligne["close"]), float(ligne["open"])
        h, lo = float(ligne["high"]), float(ligne["low"])
        if achat and c < o:
            ob_top, ob_bot = o, lo
        elif not achat and c > o:
            ob_top, ob_bot = h, c
        else:
            continue

        if not (ob_bot - tol) <= price <= (ob_top + tol):
            continue

        statut = ob_status(tail.iloc[i + 1:].copy(), ob_top, ob_bot, side)
        if statut == "broken":
            continue

        qualite = 1.0 if statut == "intact" else 0.65
        if any(ob_bot <= b and t <= ob_top for b, t in fvg_zones):
            qualite = min(1.0, qualite * 1.2)
        return True, statut, round(qualite, 3)

    return False, "none", 0.0


def detect_liquidity_sweep(df: pd.DataFrame, side: str,
                           atr: float | None = None,
                           atr_mult: float = LIQUIDITY_SWEEP_ATR_MULT) -> bool:
    """Sweep de liquidité : la mèche perce un extrême, la clôture reconquiert.

    Le tampon de reconquête est une fraction d'ATR quand l'ATR est fourni —
    sinon 0,3 %, conservé pour rétrocompatibilité mais déconseillé (voir le
    correctif n°2 en tête de module).
    """
    if df is None or len(df) < LIQUIDITY_LOOKBACK + 5:
        return False
    try:
        tail = df.tail(5)
        historique = df.iloc[-(LIQUIDITY_LOOKBACK + 5):-5]
        use_atr = atr is not None and float(atr) > 0
        buf = float(atr) * atr_mult if use_atr else None
        if _is_buy(side):
            plus_bas = float(historique["low"].min())
            confirmation = (plus_bas + buf) if use_atr else plus_bas * 1.003
            return (any(float(r) < plus_bas for r in tail["low"])
                    and float(tail["close"].iloc[-1]) > confirmation)
        plus_haut = float(historique["high"].max())
        confirmation = (plus_haut - buf) if use_atr else plus_haut * 0.997
        return (any(float(r) > plus_haut for r in tail["high"])
                and float(tail["close"].iloc[-1]) < confirmation)
    except Exception:  # noqa: BLE001
        return False


def detect_rejection_candle(df: pd.DataFrame, side: str) -> bool:
    """Bougie de rejet : longue mèche du bon côté, petit corps."""
    if df is None or len(df) < 3:
        return False
    try:
        c = df.iloc[-1]
        o, h, lo, cl = (float(c["open"]), float(c["high"]),
                        float(c["low"]), float(c["close"]))
        corps, plage = abs(cl - o), h - lo
        if plage < 1e-9:
            return False
        if _is_buy(side):
            meche_basse = min(o, cl) - lo
            return meche_basse > plage * 0.5 and corps < plage * 0.35
        meche_haute = h - max(o, cl)
        return meche_haute > plage * 0.5 and corps < plage * 0.35
    except Exception:  # noqa: BLE001
        return False
