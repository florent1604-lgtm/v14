"""Bibliothèque de bougies japonaises et leur signification.

Portage **fidèle** de V12 (`poles/smc/candlestick_engine.py`). Il remplace le
détecteur réduit à deux patterns qui servait de bouche-trou dans `smc.py` :
V14 retrouve ici le comportement de V12 sur le pilier G5.

C'est l'outil de CONFIRMATION de la méthode de Florent — « la confirmation peut
venir d'une simple bougie ».

API pure, **sans look-ahead** : n'analyse que des bougies clôturées.

TROIS RÈGLES QUI FONT TOUTE LA DIFFÉRENCE
------------------------------------------
Elles viennent d'une red-team Codex et sont la raison pour laquelle ce module
vaut mieux qu'une simple liste de patterns.

1. **Le contexte tranche.** Un marteau n'est un signal d'achat qu'APRÈS une
   baisse. Chaque pattern porte `needs_downtrend` / `needs_uptrend`, croisé avec
   la tendance EMA200 par l'appelant. Sans ça, un marteau en pleine hausse
   compterait comme un achat.

2. **Un pattern `confirm=True` n'est qu'un CANDIDAT.** Il ne produit aucun biais
   tant que la bougie close suivante ne l'a pas validé (clôture dans son sens,
   au-delà de la clôture du pattern). Aucune bougie en formation n'est jamais
   lue.

3. **Pas de somme de patterns corrélés.** Une seule contribution est retenue —
   la plus forte. Additionner trois patterns lus sur la même bougie reviendrait
   à compter trois fois la même information.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Candle:
    o: float
    h: float
    l: float  # noqa: E741 — vocabulaire OHLC, renommer nuirait à la lisibilité
    c: float

    @property
    def valid(self) -> bool:
        vals = (self.o, self.h, self.l, self.c)
        return (all(math.isfinite(x) for x in vals)
                and self.h >= self.l and self.h >= max(self.o, self.c)
                and self.l <= min(self.o, self.c))

    @property
    def flat(self) -> bool:
        return self.h - self.l <= 1e-12      # aucune amplitude : ce n'est pas un doji

    @property
    def body(self) -> float:
        return abs(self.c - self.o)

    @property
    def rng(self) -> float:
        return self.h - self.l              # pas de plancher : bougie plate → 0

    @property
    def upper(self) -> float:
        return self.h - max(self.o, self.c)

    @property
    def lower(self) -> float:
        return min(self.o, self.c) - self.l

    @property
    def bull(self) -> bool:
        return self.c > self.o              # strict : un doji n'est ni l'un ni l'autre

    @property
    def bear(self) -> bool:
        return self.c < self.o

    @property
    def mid(self) -> float:
        return (self.o + self.c) / 2.0


@dataclass(frozen=True)
class Pattern:
    name: str
    direction: int      # +1 haussier, −1 baissier, 0 indécision
    kind: str           # 'reversal' | 'continuation' | 'indecision'
    strength: float     # 0..1
    confirm: bool       # une bougie de confirmation est-elle requise ?
    meaning: str
    context: str = ""   # 'needs_downtrend' | 'needs_uptrend' | ''


def _c(row) -> Candle:
    return Candle(float(row["open"]), float(row["high"]),
                  float(row["low"]), float(row["close"]))


# ── Patterns 1 bougie ────────────────────────────────────────────────────────

def _doji(a: Candle, prev=None, p2=None) -> Pattern | None:
    if a.body > a.rng * 0.08:
        return None
    if a.upper > a.rng * 0.66 and a.lower < a.rng * 0.10:
        return Pattern("doji_pierre_tombale", -1, "reversal", 0.6, True,
                       "Doji pierre tombale : rejet violent des hauts → épuisement acheteur.",
                       "needs_uptrend")
    if a.lower > a.rng * 0.66 and a.upper < a.rng * 0.10:
        return Pattern("doji_libellule", +1, "reversal", 0.6, True,
                       "Doji libellule : rejet des bas → épuisement vendeur.",
                       "needs_downtrend")
    return Pattern("doji", 0, "indecision", 0.3, True,
                   "Doji : équilibre acheteurs/vendeurs, indécision — attendre confirmation.")


def _marteau(a: Candle, prev=None, p2=None) -> Pattern | None:
    if a.body < a.rng * 0.35 and a.lower > a.rng * 0.55 and a.upper < a.rng * 0.15:
        # marteau (bas de tendance) vs pendu (haut de tendance) : le contexte tranche
        return Pattern("marteau", +1, "reversal", 0.65, True,
                       "Marteau : longue mèche basse, rejet des vendeurs → retournement haussier probable.",
                       "needs_downtrend")
    return None


def _etoile_filante(a: Candle, prev=None, p2=None) -> Pattern | None:
    if a.body < a.rng * 0.35 and a.upper > a.rng * 0.55 and a.lower < a.rng * 0.15:
        return Pattern("etoile_filante", -1, "reversal", 0.65, True,
                       "Étoile filante : longue mèche haute, rejet des acheteurs → retournement baissier probable.",
                       "needs_uptrend")
    return None


def _marubozu(a: Candle, prev=None, p2=None) -> Pattern | None:
    if a.body > a.rng * 0.90:
        d = +1 if a.bull else -1
        return Pattern("marubozu", d, "continuation", 0.6, False,
                       "Marubozu haussier : pression acheteuse totale, corps plein sans mèche."
                       if a.bull else
                       "Marubozu baissier : pression vendeuse totale, corps plein sans mèche.")
    return None


def _toupie(a: Candle, prev=None, p2=None) -> Pattern | None:
    if a.body < a.rng * 0.30 and a.upper > a.rng * 0.25 and a.lower > a.rng * 0.25:
        return Pattern("toupie", 0, "indecision", 0.3, True,
                       "Toupie : petit corps, mèches des deux côtés → indécision, perte de momentum.")
    return None


# ── Patterns 2 bougies ───────────────────────────────────────────────────────

def _englobante(a: Candle, prev: Candle, p2=None) -> Pattern | None:
    if prev is None:
        return None
    if a.bull and prev.bear and a.c >= prev.o and a.o <= prev.c and a.body > prev.body:
        return Pattern("avalement_haussier", +1, "reversal", 0.75, False,
                       "Avalement haussier : la bougie verte engloutit la rouge → renversement acheteur.",
                       "needs_downtrend")
    if a.bear and prev.bull and a.c <= prev.o and a.o >= prev.c and a.body > prev.body:
        return Pattern("avalement_baissier", -1, "reversal", 0.75, False,
                       "Avalement baissier : la bougie rouge engloutit la verte → renversement vendeur.",
                       "needs_uptrend")
    return None


def _harami(a: Candle, prev: Candle, p2=None) -> Pattern | None:
    if prev is None or prev.body < prev.rng * 0.5:
        return None
    dedans = (max(a.o, a.c) <= max(prev.o, prev.c)
              and min(a.o, a.c) >= min(prev.o, prev.c))
    if dedans and a.body < prev.body * 0.6:
        if prev.bear:
            return Pattern("harami_haussier", +1, "reversal", 0.55, True,
                           "Harami haussier : petite bougie dans une grande rouge → ralentissement de la baisse.",
                           "needs_downtrend")
        if prev.bull:
            return Pattern("harami_baissier", -1, "reversal", 0.55, True,
                           "Harami baissier : petite bougie dans une grande verte → ralentissement de la hausse.",
                           "needs_uptrend")
    return None


def _pince(a: Candle, prev: Candle, p2=None) -> Pattern | None:
    if prev is None:
        return None
    tol = (a.rng + prev.rng) * 0.06
    if abs(a.l - prev.l) <= tol and prev.bear and a.bull:
        return Pattern("pince_basse", +1, "reversal", 0.5, True,
                       "Pince basse : deux plus-bas identiques → support défendu, rebond probable.",
                       "needs_downtrend")
    if abs(a.h - prev.h) <= tol and prev.bull and a.bear:
        return Pattern("pince_haute", -1, "reversal", 0.5, True,
                       "Pince haute : deux plus-hauts identiques → résistance défendue, repli probable.",
                       "needs_uptrend")
    return None


def _penetrante(a: Candle, prev: Candle, p2=None) -> Pattern | None:
    if prev is None:
        return None
    if prev.bear and a.bull and a.o < prev.l and a.c > prev.mid and a.c < prev.o:
        return Pattern("penetrante", +1, "reversal", 0.6, True,
                       "Ligne pénétrante : ouverture sous le bas, clôture au-dessus de la mi-bougie rouge → achat.",
                       "needs_downtrend")
    if prev.bull and a.bear and a.o > prev.h and a.c < prev.mid and a.c > prev.o:
        return Pattern("couvert_nuages", -1, "reversal", 0.6, True,
                       "Couverture en nuage noir : ouverture au-dessus du haut, clôture sous la mi-bougie verte → vente.",
                       "needs_uptrend")
    return None


# ── Patterns 3 bougies ───────────────────────────────────────────────────────

def _etoile(a: Candle, prev: Candle, p2: Candle) -> Pattern | None:
    if prev is None or p2 is None:
        return None
    etoile = prev.body < prev.rng * 0.35
    # 1re bougie = corps FRANC (pas un doji) ; 3e confirme dans le sens.
    grande = p2.body > p2.rng * 0.5
    if p2.bear and grande and etoile and a.bull and a.c > p2.mid:
        return Pattern("etoile_matin", +1, "reversal", 0.8, False,
                       "Étoile du matin : baisse → indécision → forte hausse. Retournement haussier fiable.",
                       "needs_downtrend")
    if p2.bull and grande and etoile and a.bear and a.c < p2.mid:
        return Pattern("etoile_soir", -1, "reversal", 0.8, False,
                       "Étoile du soir : hausse → indécision → forte baisse. Retournement baissier fiable.",
                       "needs_uptrend")
    return None


def _soldats(a: Candle, prev: Candle, p2: Candle) -> Pattern | None:
    if prev is None or p2 is None:
        return None

    def grande(x: Candle) -> bool:
        return x.body > x.rng * 0.6

    if (a.bull and prev.bull and p2.bull and grande(a) and grande(prev)
            and grande(p2) and a.c > prev.c > p2.c):
        return Pattern("trois_soldats", +1, "continuation", 0.7, False,
                       "Trois soldats blancs : trois hausses franches consécutives → tendance haussière forte.")
    if (a.bear and prev.bear and p2.bear and grande(a) and grande(prev)
            and grande(p2) and a.c < prev.c < p2.c):
        return Pattern("trois_corbeaux", -1, "continuation", 0.7, False,
                       "Trois corbeaux noirs : trois baisses franches consécutives → tendance baissière forte.")
    return None


def _inside_outside(a: Candle, prev: Candle, p2=None) -> Pattern | None:
    if prev is None:
        return None
    if a.h <= prev.h and a.l >= prev.l:
        return Pattern("inside_bar", 0, "indecision", 0.35, True,
                       "Inside bar : compression dans la bougie précédente → cassure imminente, jouer la sortie.")
    if a.h > prev.h and a.l < prev.l:
        if not (a.bull or a.bear):
            return None
        d = +1 if a.bull else -1
        return Pattern("outside_bar", d, "reversal", 0.5, True,
                       "Outside bar : englobe la précédente des deux côtés → prise de contrôle "
                       + ("acheteuse." if a.bull else "vendeuse."))
    return None


_DETECTEURS = [_doji, _marteau, _etoile_filante, _marubozu, _toupie,
               _englobante, _harami, _pince, _penetrante,
               _etoile, _soldats, _inside_outside]

#: Nombre de patterns reconnus — sert au dashboard et aux tests de non-régression.
NB_DETECTEURS = len(_DETECTEURS)


def analyze(df: pd.DataFrame) -> list[Pattern]:
    """Patterns présents sur la DERNIÈRE bougie clôturée. Fail-safe."""
    if df is None or len(df) < 3:
        return []
    try:
        a, prev, p2 = _c(df.iloc[-1]), _c(df.iloc[-2]), _c(df.iloc[-3])
    except Exception:  # noqa: BLE001
        return []
    # OHLC finis et cohérents ; une bougie plate (H=L) n'est pas un doji.
    if not a.valid or not prev.valid or not p2.valid or a.flat:
        return []
    out: list[Pattern] = []
    for det in _DETECTEURS:
        try:
            p = det(a, prev, p2)
        except Exception:  # noqa: BLE001 — un détecteur ne casse pas les autres
            p = None
        if p:
            out.append(p)
    return sorted(out, key=lambda p: -p.strength)


def net_bias(patterns: list[Pattern], *, uptrend: bool | None = None) -> dict:
    """Biais net, contexte et confirmation respectés. Voir les 3 règles en tête."""
    actionnables: list[Pattern] = []
    for p in patterns:
        if uptrend is not None:
            if p.context == "needs_downtrend" and uptrend:
                continue
            if p.context == "needs_uptrend" and not uptrend:
                continue
        actionnables.append(p)

    confirmes = [p for p in actionnables if not p.confirm]
    en_attente = [p.name for p in actionnables if p.confirm]
    if not confirmes:
        return {"direction": 0, "score": 0.0, "patterns": [],
                "pending_confirmation": en_attente}
    meilleur = max(confirmes, key=lambda p: p.strength)   # une seule contribution
    return {"direction": meilleur.direction,
            "score": round(meilleur.direction * meilleur.strength, 3),
            "patterns": [meilleur.name], "pending_confirmation": en_attente}


def _confirms(cand: Pattern, conf: Candle, patt_bar: Candle) -> bool:
    """La bougie N valide-t-elle le candidat formé en N−1 ?

    Règle causale : clôture dans le sens du candidat, au-delà de la clôture de la
    bougie du pattern.
    """
    if cand.direction > 0:
        return conf.bull and conf.c > patt_bar.c
    if cand.direction < 0:
        return conf.bear and conf.c < patt_bar.c
    return False


def net_bias_on_df(df: pd.DataFrame, *, uptrend: bool | None = None) -> dict:
    """Biais ACTIONNABLE sur la dernière bougie clôturée, avec automate de confirmation.

    * patterns `confirm=False` sur N : actionnables immédiatement ;
    * patterns `confirm=True` formés en N−1 : actionnables seulement si la bougie
      N (déjà clôturée) les confirme.

    Une seule contribution retenue. `confirmed_from_prev` liste les candidats
    validés, `pending_confirmation` ceux qui attendent encore.
    """
    vide = {"direction": 0, "score": 0.0, "patterns": [],
            "pending_confirmation": [], "confirmed_from_prev": []}
    if df is None or len(df) < 3:
        return vide

    immediat = net_bias(analyze(df), uptrend=uptrend)
    candidats = []
    if immediat["direction"] != 0 and immediat["patterns"]:
        candidats.append((immediat["direction"], abs(immediat["score"]),
                          immediat["patterns"][0]))

    confirmes_prec: list[str] = []
    if len(df) >= 4:
        try:
            conf, barre_pattern = _c(df.iloc[-1]), _c(df.iloc[-2])
            barres_ok = conf.valid and barre_pattern.valid and not conf.flat
        except Exception:  # noqa: BLE001
            barres_ok = False
        if barres_ok:
            for p in analyze(df.iloc[:-1]):          # patterns formés en N−1
                if not p.confirm:
                    continue                          # déjà couvert par `immediat`
                if uptrend is not None:
                    if p.context == "needs_downtrend" and uptrend:
                        continue
                    if p.context == "needs_uptrend" and not uptrend:
                        continue
                if _confirms(p, conf, barre_pattern):
                    confirmes_prec.append(p.name)
                    candidats.append((p.direction, p.strength, p.name))

    if not candidats:
        return {**vide, "pending_confirmation": immediat.get("pending_confirmation", [])}
    meilleur = max(candidats, key=lambda x: x[1])
    return {"direction": meilleur[0], "score": round(meilleur[0] * meilleur[1], 3),
            "patterns": [meilleur[2]],
            "pending_confirmation": immediat.get("pending_confirmation", []),
            "confirmed_from_prev": confirmes_prec}
