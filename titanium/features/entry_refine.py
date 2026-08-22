"""Affinage de l'entrée sur unités de temps inférieures — timing, zone, ancrage SL.

Porté de V12 (`fusion/entry_refine.py`). Le module y fait **trois** choses que
V12 livre en bloc et que V14 sépare délibérément :

1. **Ancrage du SL** sur le dernier swing micro M5, avec resserrement borné.
2. **Timing micro** : rupture de structure M5 et bougie de rejet.
3. **Zone d'entrée** : FVG M5 non comblée la plus proche du prix.

DIFFÉRENCE AVEC V12 — le plancher par défaut. V12 utilise
``sl_floor_frac = 0.6``, ce qui autorise un resserrement du SL jusqu'à −40 %.
V14 prend **1.0** par défaut, ce qui neutralise le point 1 tout en conservant
les points 2 et 3.

La raison est mesurée, pas doctrinale. `cost_r = spread / r_unit` avec un
écart-type relatif de 0,0 % sur 5651 trades BTC-JPY : diviser la distance de
stop par α multiplie le coût de transaction par 1/α. Sur BTC-JPY le coût médian
est de 0,0729 R ; au plancher V12 il deviendrait 0,1215 R, soit au-delà du
seuil de 0,12 R au-dessus duquel l'espérance en vérification devient négative
(voir `docs/RAPPORT_COUT_DECISIONNEL_20260822.md`).

Ce que ces mesures n'établissent **pas** : le bilan net d'un resserrement. Un
stop plus serré est aussi touché plus souvent, et les données comparent des
trades différents, non le même trade avec deux stops. Le défaut neutre est donc
une position d'attente, pas un verdict — il rend le resserrement mesurable par
A/B au lieu de l'imposer.

DIFFÉRENCE AVEC V12 — `detect_bos`. En V14 le détecteur rend ``(rompu, niveau)``
et non un booléen (commit `f8328e2`) ; l'appel est déballé en conséquence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from titanium.features.smc import (
    detect_bos,
    detect_fvg_unfilled,
    detect_rejection_candle,
)
from titanium.features.structure import _swings

# Poids du score d'affinage. Repris de V12 sans modification : ce sont des
# valeurs mesurées en démo, les changer relève de l'arbitrage Prime.
POIDS_BOS = 0.40
POIDS_REJET = 0.20
POIDS_ZONE = 0.40

# Tampon au-delà du swing servant d'ancrage, en fraction d'ATR.
TAMPON_ANCRAGE_ATR = 0.10

# Plancher de resserrement. 1.0 = aucun resserrement (voir docstring).
PLANCHER_SL_DEFAUT = 1.0

# Profondeur des fenêtres M5.
LOOKBACK_BOS = 20
LOOKBACK_FVG = 60
SWING_K_MICRO = 2


@dataclass
class Affinage:
    """Résultat d'un affinage. Toujours exploitable, même en cas d'échec.

    ``applique`` ne dit que ceci : l'ancrage du SL a produit une valeur. Il ne
    dit pas que le SL a changé — avec le plancher à 1.0 il ne change jamais.
    Pour savoir si le timing a confirmé, lire ``confirmation_micro``.
    """

    applique: bool = False
    sl_mult: float = 0.0
    score: float = 0.0
    confirmation_micro: bool = False
    dans_la_zone: bool = False
    zone_entree: tuple[float, float] | None = None
    ancrage_sl: float | None = None
    utf: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "applique": self.applique,
            "sl_mult": self.sl_mult,
            "score": self.score,
            "confirmation_micro": self.confirmation_micro,
            "dans_la_zone": self.dans_la_zone,
            "zone_entree": self.zone_entree,
            "ancrage_sl": self.ancrage_sl,
            "utf": list(self.utf),
            "notes": list(self.notes),
        }


def _est_achat(side_int: int) -> bool:
    return int(side_int) > 0


def _side_str(side_int: int) -> str:
    return "buy" if _est_achat(side_int) else "sell"


def _ancrage_micro_sl(df: pd.DataFrame | None, side_int: int,
                      prix_ref: float) -> float | None:
    """Swing d'invalidation le plus proche, du bon côté du prix, ou ``None``.

    · achat → le swing bas le plus HAUT encore sous le prix ;
    · vente → le swing haut le plus BAS encore au-dessus du prix.

    Identique à V12. Un swing du mauvais côté ne protège de rien, et parmi les
    bons c'est le plus proche qui porte l'invalidation.

    ATTENTION — `_swings` retient un extremum par ÉGALITÉ : sur un palier plat,
    chaque barre du palier compte comme swing. L'ancrage tombe alors sur le
    palier plutôt que sur le vrai creux. Ce n'est pas un défaut d'ici mais une
    propriété de `_swings` ; elle est bénigne tant que le SL n'est pas resserré
    (plancher 1.0) et devient un piège dès qu'on descend le plancher, car les
    paliers plats sont fréquents en M5 sur les périodes creuses — précisément
    celles où le spread est déjà défavorable.
    """
    if df is None or len(df) < 2 * SWING_K_MICRO + 3:
        return None
    if not {"high", "low"}.issubset(df.columns):
        return None
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    sh, sl = _swings(highs, lows, k=SWING_K_MICRO)
    if _est_achat(side_int):
        candidats = [float(lows[i]) for i in sl if float(lows[i]) < prix_ref]
        return max(candidats) if candidats else None
    candidats = [float(highs[i]) for i in sh if float(highs[i]) > prix_ref]
    return min(candidats) if candidats else None


def affiner(symbole: str, side_int: int, m5_df: pd.DataFrame | None, *,
            atr_ref: float, prix_ref: float, sl_mult_base: float,
            plancher_sl: float = PLANCHER_SL_DEFAUT) -> Affinage:
    """Affine l'entrée d'un setup déjà dirigé. Ne décide jamais du sens.

    ``plancher_sl`` est la fraction minimale de ``sl_mult_base`` que l'ancrage
    peut atteindre. À 1.0 le SL est laissé intact ; à 0.6 il peut être resserré
    jusqu'à −40 %, comportement de V12.

    Fail-safe : toute anomalie rend un `Affinage` neutre plutôt que de lever.
    Un affinage qui échoue ne doit jamais empêcher le trade de base.
    """
    resultat = Affinage(sl_mult=float(sl_mult_base or 0.0))
    try:
        side_int = int(side_int)
        atr_ref = float(atr_ref or 0.0)
        prix_ref = float(prix_ref or 0.0)
        sl_mult_base = float(sl_mult_base or 0.0)
        plancher_sl = float(plancher_sl)
    except (TypeError, ValueError):
        return resultat

    if side_int == 0 or atr_ref <= 0 or prix_ref <= 0 or sl_mult_base <= 0:
        return resultat
    if not 0.0 < plancher_sl <= 1.0:
        return resultat

    resultat.sl_mult = sl_mult_base
    side_str = _side_str(side_int)

    # 1) Ancrage du SL sur la micro-structure M5, resserrement borné.
    ancrage = _ancrage_micro_sl(m5_df, side_int, prix_ref)
    if ancrage is not None:
        distance = abs(prix_ref - ancrage) + TAMPON_ANCRAGE_ATR * atr_ref
        mult = distance / atr_ref
        plancher = sl_mult_base * plancher_sl
        # On ne resserre pas sous le plancher et on n'élargit jamais.
        mult = max(plancher, min(sl_mult_base, mult))
        resultat.sl_mult = round(mult, 3)
        resultat.ancrage_sl = round(float(ancrage), 6)
        resultat.applique = True
        resultat.utf.append("M5")
        if mult < sl_mult_base - 1e-9:
            resultat.notes.append(
                f"SL {sl_mult_base:.2f}->{resultat.sl_mult:.2f} ATR (swing M5)")

    # 2) Timing micro : rupture de structure puis bougie de rejet.
    rompu = False
    try:
        if m5_df is not None:
            rompu, _niveau = detect_bos(m5_df, side_str, lookback=LOOKBACK_BOS)
    except Exception:
        rompu = False
    try:
        rejet = bool(detect_rejection_candle(m5_df, side_str)) if m5_df is not None else False
    except Exception:
        rejet = False

    if rompu:
        resultat.score += POIDS_BOS
        resultat.notes.append("micro-BOS M5")
    if rejet:
        resultat.score += POIDS_REJET
        resultat.notes.append("rejet M5")
    resultat.confirmation_micro = bool(rompu or rejet)

    # 3) Zone d'entrée : FVG M5 non comblée la plus proche du prix.
    try:
        zones = detect_fvg_unfilled(m5_df, side_str, lookback=LOOKBACK_FVG) if m5_df is not None else []
    except Exception:
        zones = []
    if zones:
        def _distance(z: tuple[float, float]) -> float:
            bas, haut = min(z), max(z)
            if bas <= prix_ref <= haut:
                return 0.0
            return min(abs(prix_ref - bas), abs(prix_ref - haut))

        zone = min(zones, key=_distance)
        bas, haut = min(zone), max(zone)
        resultat.zone_entree = (round(bas, 6), round(haut, 6))
        resultat.dans_la_zone = bas <= prix_ref <= haut
        if resultat.dans_la_zone:
            resultat.score += POIDS_ZONE
            resultat.notes.append("dans FVG M5 non comblée")
        if "M5" not in resultat.utf:
            resultat.utf.append("M5")

    resultat.score = round(min(resultat.score, 1.0), 3)
    return resultat
