"""Structure de marché — niveaux S/R, profil de volume, zone OTE de Fibonacci.

Porté de V12 (`core/sr_levels.py`, `core/volume_profile.py`,
`poles/smc/fib_ote.py`). Logique inchangée, API pure numpy/pandas, **sans
look-ahead**.

Ces trois lectures alimentent trois piliers distincts de la porte de confluence :

* **S/R** → G1 (`on_sr_level`), et c'est aussi ce qui donne le SENS du setup :
  sur un support → long, sur une résistance → short ;
* **profil de volume** → G2 (`fair_value`) : le prix est-il sur un nœud de
  valeur (VPOC/HVN) ? La value area seule est trop large — ~70 % des prix — et
  accepterait n'importe quoi ;
* **OTE** → G4, la golden zone [0.618–0.786] d'une impulsion non invalidée.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ═══════════════════════════ niveaux support/résistance ══════════════════════

@dataclass(frozen=True)
class Level:
    price: float
    touches: int
    kind: str            # 'support' (sous le prix) | 'resistance' (au-dessus)
    strength: float      # 0..1 — touches + récence + bonus volume
    volume_confirmed: bool = False


def _pivots(highs, lows, k: int = 3):
    """Pivots fractals : un extrême entouré de k barres de chaque côté."""
    sh, sl = [], []
    for i in range(k, len(highs) - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            sh.append((i, float(highs[i])))
        if lows[i] == min(lows[i - k:i + k + 1]):
            sl.append((i, float(lows[i])))
    return sh, sl


def _cluster(pivots, tol: float):
    """Regroupe les pivots distants de moins de `tol`."""
    if not pivots:
        return []
    pts = sorted(pivots, key=lambda x: x[1])
    clusters = [[pts[0]]]
    for idx, price in pts[1:]:
        if abs(price - clusters[-1][-1][1]) <= tol:
            clusters[-1].append((idx, price))
        else:
            clusters.append([(idx, price)])
    return clusters


def compute_sr_levels(df: pd.DataFrame, *, k: int = 3, tol_pct: float = 0.4,
                      volume_profile=None) -> list[Level]:
    """Niveaux S/R clusterisés, pondérés par touches et récence.

    Plus un niveau est retesté, plus il est fort. Un niveau confirmé par un nœud
    de volume (HVN) reçoit un bonus.
    """
    if (not isinstance(k, (int, np.integer)) or k <= 0
            or not np.isfinite(tol_pct) or tol_pct <= 0 or df is None
            or len(df) < 2 * k + 5 or not {"high", "low"}.issubset(df.columns)):
        return []
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    if (not np.isfinite(highs).all() or not np.isfinite(lows).all()
            or np.any(highs < lows)):
        return []
    n = len(highs)
    prix = float(df["close"].iloc[-1]) if "close" in df.columns else float(highs[-1])
    if not np.isfinite(prix) or prix <= 0:
        return []
    tol = prix * tol_pct / 100.0

    sh, sl = _pivots(highs, lows, k)
    hvns = list(getattr(volume_profile, "hvn", []) or [])
    niveaux: list[Level] = []

    for pivots in (sh, sl):
        for cl in _cluster(pivots, tol):
            price = float(np.mean([p for _, p in cl]))
            touches = len(cl)
            recence = max(idx for idx, _ in cl) / max(n - 1, 1)
            vol_ok = any(abs(price - h) <= tol for h in hvns)
            force = min(1.0, 0.25 * touches + 0.35 * recence + (0.2 if vol_ok else 0.0))
            kind = "support" if price < prix else "resistance"
            niveaux.append(Level(round(price, 8), touches, kind, round(force, 3), vol_ok))

    return sorted(niveaux, key=lambda lv: -lv.strength)


def nearest(levels: list[Level], price: float, kind: str | None = None) -> Level | None:
    cand = [lv for lv in levels if kind is None or lv.kind == kind]
    return min(cand, key=lambda lv: abs(lv.price - price)) if cand else None


def sr_context(df: pd.DataFrame, price: float, *, k: int = 3,
               volume_profile=None, tol_pct: float = 0.6) -> dict:
    """Le prix est-il sur un gros niveau ? Et de quel type ?

    ``on_level_kind`` est la clé : c'est de là que vient le SENS du setup.
    """
    if not np.isfinite(price) or price <= 0:
        return {"available": False}
    niveaux = compute_sr_levels(df, k=k, volume_profile=volume_profile)
    if not niveaux:
        return {"available": False}
    tol = price * max(0.1, float(tol_pct)) / 100.0
    sup = nearest(niveaux, price, "support")
    res = nearest(niveaux, price, "resistance")
    sur = next((lv for lv in niveaux if abs(lv.price - price) <= tol), None)
    return {
        "available": True,
        "nearest_support": sup.price if sup else None,
        "nearest_resistance": res.price if res else None,
        "on_level": sur.price if sur else None,
        "on_level_strength": sur.strength if sur else 0.0,
        "on_level_kind": sur.kind if sur else None,
        "n_levels": len(niveaux),
        "note": (f"sur niveau {sur.kind} (force {sur.strength})" if sur
                 else "entre deux niveaux"),
    }


# ═══════════════════════════════ profil de volume ════════════════════════════

@dataclass(frozen=True)
class VolumeProfile:
    vpoc: float
    val: float
    vah: float
    hvn: list[float] = field(default_factory=list)
    lvn: list[float] = field(default_factory=list)
    bin_size: float = 0.0

    def in_value_area(self, price: float) -> bool:
        return self.val <= price <= self.vah

    def nearest_hvn(self, price: float) -> float | None:
        return min(self.hvn, key=lambda h: abs(h - price)) if self.hvn else None

    def dist_to_vpoc_pct(self, price: float) -> float:
        return (price - self.vpoc) / self.vpoc * 100.0 if self.vpoc else 0.0


def compute_profile(df: pd.DataFrame, *, bins: int = 50, window: int | None = None,
                    value_area_pct: float = 0.70) -> VolumeProfile | None:
    """Profil de volume : le volume de chaque bougie est réparti sur sa plage."""
    if (df is None or len(df) < 10 or not {"high", "low", "v"}.issubset(df.columns)
            or not isinstance(bins, (int, np.integer)) or bins <= 0
            or (window is not None and (not isinstance(window, (int, np.integer)) or window <= 0))
            or not np.isfinite(value_area_pct) or not 0.0 < value_area_pct <= 1.0):
        return None
    d = df.tail(window) if window else df
    highs = d["high"].to_numpy(float)
    lows = d["low"].to_numpy(float)
    vols = d["v"].to_numpy(float)
    if (not np.isfinite(highs).all() or not np.isfinite(lows).all()
            or not np.isfinite(vols).all() or np.any(highs < lows) or np.any(vols < 0)):
        return None
    lo, hi = float(lows.min()), float(highs.max())
    if hi - lo < 1e-12:
        return None

    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    bin_size = (hi - lo) / bins
    hist = np.zeros(bins)

    for h, low, v in zip(highs, lows, vols):
        if v <= 0 or h <= low:
            idx = min(bins - 1, max(0, int((h - lo) / bin_size)))
            hist[idx] += max(v, 0.0)
            continue
        lo_b = max(0, int((low - lo) / bin_size))
        hi_b = min(bins - 1, int((h - lo) / bin_size))
        span = h - low
        for b in range(lo_b, hi_b + 1):
            recouvrement = min(h, edges[b + 1]) - max(low, edges[b])
            if recouvrement > 0:
                hist[b] += v * (recouvrement / span)

    total = hist.sum()
    if total <= 0:
        return None
    poc_idx = int(hist.argmax())

    # Value area : on étend depuis le POC vers le voisin le plus volumineux.
    lo_i = hi_i = poc_idx
    acc = hist[poc_idx]
    cible = total * value_area_pct
    while acc < cible and (lo_i > 0 or hi_i < bins - 1):
        gauche = hist[lo_i - 1] if lo_i > 0 else -1
        droite = hist[hi_i + 1] if hi_i < bins - 1 else -1
        if droite >= gauche:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]

    # HVN / LVN : extrema locaux après un lissage léger.
    sm = np.convolve(hist, np.array([0.25, 0.5, 0.25]), mode="same")
    moyenne = sm.mean()
    hvn = [float(centers[i]) for i in range(1, bins - 1)
           if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] > moyenne * 1.2]
    lvn = [float(centers[i]) for i in range(1, bins - 1)
           if sm[i] <= sm[i - 1] and sm[i] <= sm[i + 1] and sm[i] < moyenne * 0.5]

    return VolumeProfile(vpoc=float(centers[poc_idx]), val=float(centers[lo_i]),
                         vah=float(centers[hi_i]), hvn=hvn, lvn=lvn, bin_size=bin_size)


def volume_context(df: pd.DataFrame, price: float, *, window: int = 200) -> dict:
    """Le prix est-il sur une zone de juste prix (HVN ou VPOC) ?

    La value area seule couvre ~70 % des prix : s'en contenter accepterait
    presque tous les setups. On exige donc le nœud précis.
    """
    if not np.isfinite(price) or price <= 0:
        return {"available": False}
    prof = compute_profile(df, window=window)
    if prof is None:
        return {"available": False}
    hvn = prof.nearest_hvn(price)
    pres_hvn = hvn is not None and abs(price - hvn) <= 2 * prof.bin_size
    pres_vpoc = abs(price - prof.vpoc) <= prof.bin_size
    return {
        "available": True, "vpoc": prof.vpoc, "val": prof.val, "vah": prof.vah,
        "in_value_area": prof.in_value_area(price),
        "near_hvn": pres_hvn, "near_vpoc": pres_vpoc, "nearest_hvn": hvn,
        "on_fair_price_zone": pres_hvn or pres_vpoc,
        "dist_to_vpoc_pct": round(prof.dist_to_vpoc_pct(price), 3),
        "note": ("sur HVN (nœud de valeur fort)" if pres_hvn
                 else "sur VPOC (prix le plus échangé)" if pres_vpoc
                 else "dans la value area" if prof.in_value_area(price)
                 else "hors value area (déséquilibre)"),
    }


# ════════════════════════ Fibonacci — zone OTE ═══════════════════════════════

LEVELS = (0.382, 0.5, 0.618, 0.705, 0.786)
OTE_LOW, OTE_HIGH, GOLDEN = 0.618, 0.786, 0.705


@dataclass(frozen=True)
class FibZone:
    direction: int          # +1 impulsion haussière (setup LONG) / −1 (SHORT)
    swing_start: float
    swing_end: float
    ote_low: float = 0.0
    ote_high: float = 0.0
    golden: float = 0.0
    invalidation: float = 0.0

    def in_ote(self, price: float) -> bool:
        lo, hi = sorted((self.ote_low, self.ote_high))
        return lo <= price <= hi

    def invalidated(self, price: float) -> bool:
        return price < self.invalidation if self.direction > 0 else price > self.invalidation


def _swings(highs, lows, k: int = 3):
    sh, sl = [], []
    for i in range(k, len(highs) - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            sh.append(i)
        if lows[i] == min(lows[i - k:i + k + 1]):
            sl.append(i)
    return sh, sl


def compute_fib_ote(df: pd.DataFrame, *, k: int = 3) -> FibZone | None:
    """Dernière impulsion et sa zone OTE [0.618–0.786]."""
    if (not isinstance(k, (int, np.integer)) or k <= 0 or df is None
            or len(df) < 2 * k + 5 or not {"high", "low"}.issubset(df.columns)):
        return None
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    if (not np.isfinite(highs).all() or not np.isfinite(lows).all()
            or np.any(highs < lows)):
        return None
    sh, sl = _swings(highs, lows, k)
    if not sh or not sl:
        return None
    dernier_h, dernier_l = sh[-1], sl[-1]

    if dernier_l < dernier_h:               # jambe bas → haut
        direction = +1
        debut, fin = float(lows[dernier_l]), float(highs[dernier_h])
    else:                                    # jambe haut → bas
        direction = -1
        debut, fin = float(highs[dernier_h]), float(lows[dernier_l])

    move = fin - debut
    if abs(move) < 1e-9:
        return None

    return FibZone(direction=direction, swing_start=debut, swing_end=fin,
                   ote_low=fin - OTE_LOW * move, ote_high=fin - OTE_HIGH * move,
                   golden=fin - GOLDEN * move,
                   invalidation=debut)      # dépasser l'origine = structure cassée


def fib_context(df: pd.DataFrame, price: float, *, k: int = 3) -> dict:
    """Le prix est-il dans la golden zone d'une impulsion encore valide ?"""
    if not np.isfinite(price) or price <= 0:
        return {"available": False}
    fz = compute_fib_ote(df, k=k)
    if fz is None:
        return {"available": False}
    return {
        "available": True, "direction": fz.direction,
        "ote_zone": (round(min(fz.ote_low, fz.ote_high), 8),
                     round(max(fz.ote_low, fz.ote_high), 8)),
        "golden": round(fz.golden, 8), "invalidation": round(fz.invalidation, 8),
        "in_ote": fz.in_ote(price), "invalidated": fz.invalidated(price),
        "note": ("dans la golden zone" if fz.in_ote(price) and not fz.invalidated(price)
                 else "structure invalidée" if fz.invalidated(price)
                 else "hors zone OTE"),
    }
