"""Structure de marché ICT — Break of Structure, Change of Character, Displacement.

CE QUE MANQUAIT À V14
---------------------
Le module `smc.py` détecte les ORDER BLOCKS et les FVG, mais il ne comprend pas
la STRUCTURE du marché — c'est-à-dire la séquence de Higher Highs / Higher Lows
(tendance haussière) ou Lower Highs / Lower Lows (tendance baissière).

Sans cette lecture, le bot ne peut pas distinguer :
- Un **Break of Structure** (BOS) : continuation de tendance, le prix casse le
  dernier swing dans le sens → Smart Money AJOUTE à la position.
- Un **Change of Character** (CHoCH) : le PREMIER break contre la tendance →
  Smart Money change de camp. C'est le signal le plus puissant d'ICT.

DISPLACEMENT
------------
Un displacement est un mouvement VIOLENT et unidirectionnel, souvent avec
de grosses bougies et peu de mèches. C'est la signature de l'entrée
institutionnelle — quand les institutions déplacent le prix, elles ne
négocient pas, elles EXÉCUTENT.

Un displacement laisse TOUJOURS une FVG derrière lui. Si le prix revient
combler cette FVG, c'est l'entrée optimale ICT : on entre là où les
institutions ont montré leur intention, au prix où elles ont laissé un
déséquilibre.

BREAKER BLOCKS
--------------
Quand un Order Block ÉCHOUE (le prix le traverse), il ne disparaît pas —
il change de RÔLE. Un OB haussier cassé devient une résistance (breaker
block). C'est la trace d'une position institutionnelle perdante que les
institutions doivent MITIGER.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from titanium.features.smc import compute_atr


# ═══════════════════════════════════════════════════════════════════════════════
# SWING POINTS — la colonne vertébrale de la structure
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SwingPoint:
    """Un point de swing identifié (fractal)."""
    index: int              # position dans le DataFrame
    price: float
    kind: str               # 'HH' | 'HL' | 'LH' | 'LL' | 'EH' | 'EL'
    timestamp: str = ""


@dataclass
class MarketStructure:
    """État de la structure de marché à un instant donné."""
    trend: str = "undefined"  # 'bullish' | 'bearish' | 'undefined'
    swings: list[SwingPoint] = field(default_factory=list)
    last_bos: dict | None = None
    last_choch: dict | None = None
    structure_breaks: list[dict] = field(default_factory=list)

    @property
    def is_bullish(self) -> bool:
        return self.trend == "bullish"

    @property
    def is_bearish(self) -> bool:
        return self.trend == "bearish"


def detect_swing_points(df: pd.DataFrame, *, k: int = 3,
                        max_points: int = 20) -> list[SwingPoint]:
    """Identifie les swing highs et swing lows (pivots fractals).

    Retourne les `max_points` derniers, du plus ancien au plus récent.
    Chaque swing est classé en HH/HL/LH/LL par rapport au swing précédent
    du même type.
    """
    if df is None or len(df) < 2 * k + 3:
        return []

    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    n = len(highs)

    raw_swings: list[tuple[int, float, str]] = []  # (index, price, 'high'|'low')

    for i in range(k, n - k):
        is_sh = all(highs[i] >= highs[i - j] for j in range(1, k + 1)) and \
                all(highs[i] >= highs[i + j] for j in range(1, k + 1))
        is_sl = all(lows[i] <= lows[i - j] for j in range(1, k + 1)) and \
                all(lows[i] <= lows[i + j] for j in range(1, k + 1))

        if is_sh:
            raw_swings.append((i, float(highs[i]), "high"))
        if is_sl:
            raw_swings.append((i, float(lows[i]), "low"))

    raw_swings.sort(key=lambda x: x[0])

    # Classifier HH/HL/LH/LL
    last_high = None
    last_low = None
    points: list[SwingPoint] = []

    for idx, price, typ in raw_swings:
        try:
            ts = str(df.index[idx])
        except (IndexError, AttributeError):
            ts = ""

        if typ == "high":
            if last_high is None:
                kind = "EH"  # premier high, pas de référence
            elif price > last_high:
                kind = "HH"  # Higher High
            elif price < last_high:
                kind = "LH"  # Lower High
            else:
                kind = "EH"  # Equal High
            last_high = price
        else:
            if last_low is None:
                kind = "EL"
            elif price > last_low:
                kind = "HL"  # Higher Low
            elif price < last_low:
                kind = "LL"  # Lower Low
            else:
                kind = "EL"  # Equal Low
            last_low = price

        points.append(SwingPoint(idx, price, kind, ts))

    return points[-max_points:]


def analyze_structure(df: pd.DataFrame, *, k: int = 3) -> MarketStructure:
    """Analyse complète de la structure de marché.

    Identifie :
    - La tendance (séquence de HH/HL ou LH/LL)
    - Les Break of Structure (BOS)
    - Les Change of Character (CHoCH)

    Un BOS confirme la tendance : en hausse, le prix casse le dernier HH.
    Un CHoCH la retourne : en hausse, le prix casse le dernier HL → première
    cassure baissière, le marché change de caractère.
    """
    swings = detect_swing_points(df, k=k)
    ms = MarketStructure(swings=swings)

    if len(swings) < 4:
        return ms

    # Déterminer la tendance par les derniers swings
    recent_highs = [s for s in swings if s.kind in ("HH", "LH", "EH")]
    recent_lows = [s for s in swings if s.kind in ("HL", "LL", "EL")]

    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return ms

    # Tendance = séquence dominante
    last_4 = swings[-4:]
    hh_count = sum(1 for s in last_4 if s.kind == "HH")
    hl_count = sum(1 for s in last_4 if s.kind == "HL")
    lh_count = sum(1 for s in last_4 if s.kind == "LH")
    ll_count = sum(1 for s in last_4 if s.kind == "LL")

    if hh_count + hl_count >= 3:
        ms.trend = "bullish"
    elif lh_count + ll_count >= 3:
        ms.trend = "bearish"
    else:
        # Regarder plus large
        last_6 = swings[-6:]
        hh_hl = sum(1 for s in last_6 if s.kind in ("HH", "HL"))
        lh_ll = sum(1 for s in last_6 if s.kind in ("LH", "LL"))
        if hh_hl > lh_ll:
            ms.trend = "bullish"
        elif lh_ll > hh_hl:
            ms.trend = "bearish"

    # Détecter BOS et CHoCH dans la séquence
    for i in range(1, len(swings)):
        curr = swings[i]
        prev = swings[i - 1]

        # BOS haussier : nouveau HH casse le précédent HH
        if curr.kind == "HH" and ms.trend == "bullish":
            ms.structure_breaks.append({
                "type": "BOS",
                "direction": "bullish",
                "at_index": curr.index,
                "price": curr.price,
                "timestamp": curr.timestamp,
                "meaning": "Break of Structure haussier — Smart Money continue d'accumuler"
            })
            ms.last_bos = ms.structure_breaks[-1]

        # BOS baissier : nouveau LL casse le précédent LL
        elif curr.kind == "LL" and ms.trend == "bearish":
            ms.structure_breaks.append({
                "type": "BOS",
                "direction": "bearish",
                "at_index": curr.index,
                "price": curr.price,
                "timestamp": curr.timestamp,
                "meaning": "Break of Structure baissier — Smart Money continue de distribuer"
            })
            ms.last_bos = ms.structure_breaks[-1]

        # CHoCH : première cassure CONTRE la tendance
        elif curr.kind == "LL" and ms.trend == "bullish":
            ms.structure_breaks.append({
                "type": "CHoCH",
                "direction": "bearish",
                "at_index": curr.index,
                "price": curr.price,
                "timestamp": curr.timestamp,
                "meaning": "Change of Character → baissier. Le dernier HL a été cassé. "
                           "Smart Money a changé de camp."
            })
            ms.last_choch = ms.structure_breaks[-1]
            ms.trend = "bearish"

        elif curr.kind == "HH" and ms.trend == "bearish":
            ms.structure_breaks.append({
                "type": "CHoCH",
                "direction": "bullish",
                "at_index": curr.index,
                "price": curr.price,
                "timestamp": curr.timestamp,
                "meaning": "Change of Character → haussier. Le dernier LH a été cassé. "
                           "Smart Money a changé de camp."
            })
            ms.last_choch = ms.structure_breaks[-1]
            ms.trend = "bullish"

    return ms


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLACEMENT — la signature de l'entrée institutionnelle
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Displacement:
    """Un mouvement de displacement identifié."""
    start_index: int
    end_index: int
    direction: int          # +1 haussier, -1 baissier
    magnitude_atr: float    # amplitude en multiples d'ATR
    candle_count: int       # nombre de bougies dans le displacement
    has_fvg: bool           # une FVG a-t-elle été créée ?
    strength: float         # force du displacement (0-1)
    meaning: str


def detect_displacement(df: pd.DataFrame, *, min_atr_mult: float = 2.0,
                        max_candles: int = 3,
                        lookback: int = 20) -> list[Displacement]:
    """Détecte les displacements récents — mouvements violents institutionnels.

    Un displacement ICT c'est :
    1. Un mouvement de 2+ ATR en 1-3 bougies
    2. Des corps PLEINS (peu de mèches dans le sens contraire)
    3. Souvent accompagné d'une FVG

    C'est le PIED de la montagne — quand les institutions MONTRENT leur main.
    """
    if df is None or len(df) < lookback + 5:
        return []

    atr = compute_atr(df)
    if atr <= 0:
        return []

    tail = df.tail(lookback)
    displacements: list[Displacement] = []

    highs = tail["high"].to_numpy(float)
    lows = tail["low"].to_numpy(float)
    opens = tail["open"].to_numpy(float)
    closes = tail["close"].to_numpy(float)
    n = len(tail)

    for window in range(1, max_candles + 1):
        for i in range(n - window):
            segment_high = max(highs[i:i + window])
            segment_low = min(lows[i:i + window])
            move = segment_high - segment_low

            if move < min_atr_mult * atr:
                continue

            # Direction : clôture finale vs ouverture initiale
            direction = 1 if closes[i + window - 1] > opens[i] else -1

            # Vérifier que les corps sont dans le même sens (pas de va-et-vient)
            same_dir = sum(1 for j in range(i, i + window)
                          if (closes[j] > opens[j]) == (direction > 0))
            if same_dir < window * 0.7:
                continue

            # Score de « propreté » : ratio corps/mèche
            total_body = sum(abs(closes[j] - opens[j]) for j in range(i, i + window))
            body_ratio = total_body / move if move > 0 else 0

            # Vérifier la présence d'une FVG
            has_fvg = False
            if window >= 2:
                for j in range(i, i + window - 1):
                    if direction > 0 and j + 2 < n:
                        if highs[j] < lows[j + 2]:
                            has_fvg = True
                            break
                    elif direction < 0 and j + 2 < n:
                        if lows[j] > highs[j + 2]:
                            has_fvg = True
                            break

            strength = min(1.0, (move / atr - min_atr_mult) / 3.0 * 0.5
                          + body_ratio * 0.3
                          + (0.2 if has_fvg else 0.0))

            tag = "haussier" if direction > 0 else "baissier"
            meaning = (
                f"Displacement {tag} : {move/atr:.1f}× ATR en {window} bougie(s). "
                f"{'FVG créée — zone de retour optimal.' if has_fvg else 'Pas de FVG visible.'} "
                f"Corps {body_ratio:.0%} du mouvement — "
                f"{'propre, intention claire' if body_ratio > 0.7 else 'mèches présentes, conviction modérée'}."
            )

            displacements.append(Displacement(
                start_index=i, end_index=i + window - 1,
                direction=direction, magnitude_atr=round(move / atr, 2),
                candle_count=window, has_fvg=has_fvg,
                strength=round(strength, 3), meaning=meaning
            ))

    # Dédupliquer : garder le plus fort par zone
    if not displacements:
        return []

    displacements.sort(key=lambda d: -d.strength)
    kept: list[Displacement] = []
    used_indices: set[int] = set()

    for d in displacements:
        indices = set(range(d.start_index, d.end_index + 1))
        if not indices & used_indices:
            kept.append(d)
            used_indices |= indices

    return sorted(kept, key=lambda d: d.start_index)


# ═══════════════════════════════════════════════════════════════════════════════
# BREAKER BLOCKS — les OB qui ont échoué changent de rôle
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BreakerBlock:
    """Un Order Block qui a échoué et qui est devenu une zone de mitigation."""
    index: int
    top: float
    bottom: float
    original_side: str      # côté ORIGINAL de l'OB ('ACHAT' ou 'VENTE')
    new_role: str           # 'resistance' (ex-OB achat) ou 'support' (ex-OB vente)
    tested: bool
    strength: float


def detect_breaker_blocks(df: pd.DataFrame, *, lookback: int = 50) -> list[BreakerBlock]:
    """Identifie les breaker blocks — OB cassés qui ont changé de rôle.

    Quand un OB haussier est traversé à la baisse, il devient une RÉSISTANCE.
    Quand un OB baissier est traversé à la hausse, il devient un SUPPORT.

    C'est une zone où les institutions qui avaient une position perdante
    cherchent à MITIGER leur perte — elles attendent que le prix revienne
    pour couper à moindre coût.
    """
    if df is None or len(df) < lookback + 5:
        return []

    from titanium.features.smc import BUY, SELL

    tail = df.tail(lookback + 5)
    highs = tail["high"].to_numpy(float)
    lows = tail["low"].to_numpy(float)
    opens = tail["open"].to_numpy(float)
    closes = tail["close"].to_numpy(float)
    n = len(tail)

    breakers: list[BreakerBlock] = []

    for i in range(1, n - 3):
        c_val, o_val = closes[i], opens[i]
        h_val, l_val = highs[i], lows[i]

        # OB haussier : bougie baissière (close < open)
        if c_val < o_val:
            ob_top, ob_bot = o_val, l_val
            # Vérifie s'il est cassé par la suite (close < ob_bot)
            broken = False
            broken_idx = None
            for j in range(i + 1, n):
                if closes[j] < ob_bot:
                    broken = True
                    broken_idx = j
                    break

            if broken and broken_idx is not None:
                # C'est un breaker ! Vérifie s'il a été retesté
                tested = False
                for j in range(broken_idx + 1, n):
                    if lows[j] <= ob_top and highs[j] >= ob_bot:
                        tested = True
                        break

                # Force basée sur le nombre de fois que l'OB original a tenu
                holds = sum(1 for j in range(i + 1, broken_idx)
                           if lows[j] >= ob_bot * 0.998)
                strength = min(1.0, 0.4 + holds * 0.15)

                breakers.append(BreakerBlock(
                    index=i, top=ob_top, bottom=ob_bot,
                    original_side=BUY,
                    new_role="resistance",
                    tested=tested,
                    strength=round(strength, 3)
                ))

        # OB baissier : bougie haussière (close > open)
        elif c_val > o_val:
            ob_top, ob_bot = h_val, c_val
            broken = False
            broken_idx = None
            for j in range(i + 1, n):
                if closes[j] > ob_top:
                    broken = True
                    broken_idx = j
                    break

            if broken and broken_idx is not None:
                tested = False
                for j in range(broken_idx + 1, n):
                    if highs[j] >= ob_bot and lows[j] <= ob_top:
                        tested = True
                        break

                holds = sum(1 for j in range(i + 1, broken_idx)
                           if highs[j] <= ob_top * 1.002)
                strength = min(1.0, 0.4 + holds * 0.15)

                breakers.append(BreakerBlock(
                    index=i, top=ob_top, bottom=ob_bot,
                    original_side=SELL,
                    new_role="support",
                    tested=tested,
                    strength=round(strength, 3)
                ))

    return breakers[-10:]  # les 10 plus récents


# ═══════════════════════════════════════════════════════════════════════════════
# PREMIUM / DISCOUNT — au-dessus ou en dessous de l'équilibre ?
# ═══════════════════════════════════════════════════════════════════════════════

def premium_discount_zone(df: pd.DataFrame, price: float,
                          *, lookback: int = 50) -> dict:
    """Le prix est-il en zone premium (cher) ou discount (bon marché) ?

    ICT divise le range entre le dernier swing high et swing low en deux :
    - Au-dessus du 50% (equilibrium) = PREMIUM → vendre
    - En dessous du 50% = DISCOUNT → acheter

    Les institutions achètent en discount et vendent en premium. TOUJOURS.
    """
    if df is None or len(df) < lookback:
        return {"available": False}

    tail = df.tail(lookback)
    swing_high = float(tail["high"].max())
    swing_low = float(tail["low"].min())
    rng = swing_high - swing_low

    if rng < 1e-9 or not np.isfinite(price) or price <= 0:
        return {"available": False}

    equilibrium = swing_low + rng * 0.5
    position_pct = (price - swing_low) / rng  # 0 = bas du range, 1 = haut

    if position_pct > 0.5:
        zone = "premium"
        advice = "vendre"
        detail = ("Zone PREMIUM : le prix est au-dessus de l'équilibre. "
                  "Les institutions VENDENT ici — chercher des setups short.")
    else:
        zone = "discount"
        advice = "acheter"
        detail = ("Zone DISCOUNT : le prix est en dessous de l'équilibre. "
                  "Les institutions ACHÈTENT ici — chercher des setups long.")

    return {
        "available": True,
        "zone": zone,
        "advice": advice,
        "position_pct": round(position_pct, 3),
        "equilibrium": round(equilibrium, 8),
        "swing_high": round(swing_high, 8),
        "swing_low": round(swing_low, 8),
        "detail": detail,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# POWER OF 3 — Accumulation, Manipulation, Distribution
# ═══════════════════════════════════════════════════════════════════════════════

def detect_po3_phase(df: pd.DataFrame, *, session_bars: int = 16) -> dict:
    """Identifie la phase Power of 3 en cours.

    Le Power of 3 (PO3) est le modèle central d'ICT :

    1. ACCUMULATION : le prix range dans une zone étroite. Les institutions
       construisent leur position dans le calme. C'est souvent la session
       asiatique.

    2. MANIPULATION : un faux mouvement (Judas Swing) dans le sens OPPOSÉ
       au vrai mouvement. Il déclenche les stops du retail et donne aux
       institutions un meilleur prix d'entrée. C'est souvent l'ouverture
       de Londres.

    3. DISTRIBUTION : le vrai mouvement. Les institutions exécutent leur
       plan et livrent leurs positions aux retardataires. C'est souvent
       la session de New York.
    """
    if df is None or len(df) < session_bars * 2:
        return {"phase": "unknown", "confidence": 0.0}

    atr = compute_atr(df)
    if atr <= 0:
        return {"phase": "unknown", "confidence": 0.0}

    recent = df.tail(session_bars)
    prior = df.iloc[-(session_bars * 2):-session_bars]

    recent_range = float(recent["high"].max() - recent["low"].min())
    prior_range = float(prior["high"].max() - prior["low"].min())

    # Ratio de range : accumulation = range étroit
    range_ratio = recent_range / atr if atr > 0 else 0

    if range_ratio < 1.5:
        return {
            "phase": "accumulation",
            "confidence": round(min(1.0, (1.5 - range_ratio) / 1.0), 2),
            "range_atr": round(range_ratio, 2),
            "detail": "Range étroit (< 1.5 ATR) — les institutions construisent "
                      "probablement une position. Attendre le breakout.",
        }

    # Vérifier si on a eu un faux mouvement suivi d'un retour
    closes = recent["close"].to_numpy(float)
    opens = recent["open"].to_numpy(float)
    highs_r = recent["high"].to_numpy(float)
    lows_r = recent["low"].to_numpy(float)

    # Le prix est-il revenu dans le range après l'avoir cassé ?
    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())

    broke_high = any(h > prior_high for h in highs_r)
    broke_low = any(lo < prior_low for lo in lows_r)
    current_close = closes[-1]

    if broke_high and current_close < prior_high:
        return {
            "phase": "manipulation",
            "confidence": 0.7,
            "direction": "bearish_after_fake_high",
            "detail": "Judas Swing HAUSSIER détecté : le prix a cassé le haut du range "
                      "puis est REVENU en dessous. Faux breakout → le vrai mouvement "
                      "est probablement BAISSIER.",
        }

    if broke_low and current_close > prior_low:
        return {
            "phase": "manipulation",
            "confidence": 0.7,
            "direction": "bullish_after_fake_low",
            "detail": "Judas Swing BAISSIER détecté : le prix a cassé le bas du range "
                      "puis est REVENU au-dessus. Faux breakout → le vrai mouvement "
                      "est probablement HAUSSIER.",
        }

    # Mouvement directionnel fort = distribution/expansion
    net_move = abs(closes[-1] - opens[0])
    if net_move > 1.5 * atr:
        direction = "haussier" if closes[-1] > opens[0] else "baissier"
        return {
            "phase": "distribution",
            "confidence": round(min(1.0, net_move / (3 * atr)), 2),
            "direction": direction,
            "detail": f"Expansion {direction} en cours ({net_move/atr:.1f}× ATR). "
                      "Les institutions distribuent leurs positions — ne pas courir "
                      "après le mouvement, attendre le retracement OTE.",
        }

    return {"phase": "transition", "confidence": 0.3,
            "detail": "Pas de phase PO3 claire identifiée."}


# ═══════════════════════════════════════════════════════════════════════════════
# API UNIFIÉE
# ═══════════════════════════════════════════════════════════════════════════════

def full_ict_analysis(df: pd.DataFrame, price: float, *, k: int = 3) -> dict:
    """Analyse ICT complète d'un actif.

    Combine structure de marché, displacement, breaker blocks, premium/discount,
    et phase PO3 en un seul dictionnaire.
    """
    structure = analyze_structure(df, k=k)
    displacements = detect_displacement(df)
    breakers = detect_breaker_blocks(df)
    pd_zone = premium_discount_zone(df, price)
    po3 = detect_po3_phase(df)

    return {
        "structure": {
            "trend": structure.trend,
            "last_bos": structure.last_bos,
            "last_choch": structure.last_choch,
            "n_structure_breaks": len(structure.structure_breaks),
            "n_swings": len(structure.swings),
        },
        "displacement": {
            "count": len(displacements),
            "latest": {
                "direction": displacements[-1].direction,
                "magnitude_atr": displacements[-1].magnitude_atr,
                "has_fvg": displacements[-1].has_fvg,
                "strength": displacements[-1].strength,
                "meaning": displacements[-1].meaning,
            } if displacements else None,
        },
        "breaker_blocks": {
            "count": len(breakers),
            "near_price": [
                {"top": b.top, "bottom": b.bottom, "role": b.new_role,
                 "tested": b.tested, "strength": b.strength}
                for b in breakers
                if abs(price - (b.top + b.bottom) / 2) < compute_atr(df) * 2
            ],
        },
        "premium_discount": pd_zone,
        "power_of_3": po3,
    }
