"""Panel d'indicateurs — pour MESURER, jamais pour décider.

POURQUOI CE MODULE EXISTE
--------------------------
`stockstats` calcule 82 familles d'indicateurs sur nos propres bougies. Les
agents n'en voient que 13, et les portes déterministes aucun. Le gisement est
là, inexploité.

Mais l'ajouter à la **décision** serait une faute. V12 a déjà un scoring /16
— EMA200, BOS, OB/FVG, rejet, TRIX, ADX, delta volume, sweep, spectral — et son
profit factor mesuré vaut **0,20**. L'indicateur n°17 ne répare pas ça. Le tueur
mesuré est le **coût**, et aucun oscillateur ne réduit un spread.

Pire : avec quelques centaines de trades, tester 50 indicateurs **fabrique** des
corrélations fantômes. C'est exactement ce que la probabilité de sur-ajustement
(PBO) quantifie.

CE QUE FAIT CE MODULE, DONC
----------------------------
Il calcule le panel **à l'instant de la décision** et le range dans la trace,
pour qu'il soit journalisé avec le résultat du trade. Rien de plus. Aucun
indicateur ici n'influence un verdict. Plus tard,
`titanium/analysis/discriminants.py` dira lesquels séparent réellement les
gagnants des perdants — avec correction pour tests multiples.

**Mesurer d'abord, décider ensuite. Jamais l'inverse.**

LA NORMALISATION N'EST PAS COSMÉTIQUE
--------------------------------------
Un `close_50_sma` vaut 1,15 sur EURUSD et 19 000 sur USTECH. Brut, il est
incomparable entre actifs et toute statistique agrégée serait absurde. Chaque
valeur est donc ramenée à une échelle sans dimension :

* oscillateurs bornés  → tels quels (déjà comparables) ;
* niveaux de prix      → **écart au prix courant, en ATR** ;
* volatilité           → **en % du prix** ;
* volumes              → **ratio à leur propre moyenne**.
"""

from __future__ import annotations

import math
import warnings

import pandas as pd

#: Oscillateurs déjà bornés — repris sans transformation.
OSCILLATEURS = [
    "rsi_14", "rsi_6", "stochrsi", "cci_14", "wr_14", "mfi_14",
    "adx", "dx", "pdi", "kdjk", "kdjd", "kdjj", "cmo", "psl",
    "chop", "cti", "ker", "ao", "bop", "ftr", "qqe", "ppo", "pvo",
    "vr", "wt1", "wt2", "coppock", "trix", "macd", "macds", "macdh",
]

#: Niveaux de prix — convertis en écart au prix courant, exprimé en ATR.
NIVEAUX = [
    "close_20_sma", "close_50_sma", "close_200_sma", "close_10_ema",
    "close_20_ema", "boll", "boll_ub", "boll_lb", "supertrend", "vwma",
    "tema", "high_20_max", "low_20_min",
]

#: Mesures de dispersion — exprimées en % du prix.
DISPERSION = ["atr_14", "close_10_mstd"]

#: Rendements et volumes — déjà sans dimension, ou ramenés à leur moyenne.
DIVERS = ["log-ret", "close_-1_r", "volume_delta"]

#: Nombre d'indicateurs du panel. Sert au test de non-régression et à la
#: correction pour tests multiples côté analyse.
TAILLE_PANEL = len(OSCILLATEURS) + len(NIVEAUX) + len(DISPERSION) + len(DIVERS)


def _fini(x) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def calculer(df: pd.DataFrame, *, prefixe: str = "") -> dict[str, float]:
    """Calcule le panel sur les bougies fournies.

    Ne lève jamais : un indicateur qui échoue est simplement absent du
    résultat. Un panel incomplet vaut mieux qu'une décision interrompue.

    Args:
        df: bougies avec open/high/low/close (+ volume si disponible).
        prefixe: préfixe des clés, pour distinguer les timeframes
            (``"ltf_"`` / ``"htf_"``).

    Returns:
        ``{nom: valeur}`` — valeurs **normalisées**, comparables entre actifs.
    """
    out: dict[str, float] = {}
    if df is None or len(df) < 30:
        return out

    d = df.rename(columns={c: str(c).lower() for c in df.columns}).copy()
    if "volume" not in d.columns:
        for src in ("tick_volume", "v", "real_volume"):
            if src in d.columns:
                d = d.rename(columns={src: "volume"})
                break
    if not {"open", "high", "low", "close"}.issubset(d.columns):
        return out

    prix = _fini(d["close"].iloc[-1])
    if not prix or prix <= 0:
        return out

    try:
        from stockstats import wrap
    except ImportError:
        return out

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stock = wrap(d)

        # Échelle de référence : l'ATR. Sans lui, aucun niveau n'est comparable.
        atr = None
        try:
            atr = _fini(stock["atr_14"].iloc[-1])
        except Exception:  # noqa: BLE001
            pass

        def lire(nom):
            try:
                return _fini(stock[nom].iloc[-1])
            except Exception:  # noqa: BLE001
                return None

        for nom in OSCILLATEURS:
            v = lire(nom)
            if v is not None:
                out[f"{prefixe}{nom}"] = round(v, 6)

        # Niveaux → écart au prix, en ATR. Sans ATR exploitable on s'abstient
        # plutôt que de produire un chiffre incomparable.
        if atr and atr > 0:
            for nom in NIVEAUX:
                v = lire(nom)
                if v is not None:
                    out[f"{prefixe}{nom}_atr"] = round((prix - v) / atr, 4)

        for nom in DISPERSION:
            v = lire(nom)
            if v is not None:
                out[f"{prefixe}{nom}_pct"] = round(v / prix * 100.0, 5)

        for nom in DIVERS:
            v = lire(nom)
            if v is not None:
                out[f"{prefixe}{nom}"] = round(v, 6)

        # Volume relatif à sa propre moyenne : « 2.1 » se lit sur tout actif.
        if "volume" in d.columns:
            try:
                moy = float(d["volume"].tail(20).mean())
                if moy > 0:
                    out[f"{prefixe}vol_ratio"] = round(
                        float(d["volume"].iloc[-1]) / moy, 4)
            except Exception:  # noqa: BLE001
                pass

    return out


def panel(df_ltf: pd.DataFrame | None,
          df_htf: pd.DataFrame | None = None) -> dict[str, float]:
    """Panel complet sur les deux timeframes. Ne lève jamais.

    Les mêmes indicateurs sur deux horizons ne disent pas la même chose : un RSI
    M15 survendu dans une tendance H4 haussière n'a rien à voir avec le même RSI
    dans une tendance H4 baissière. C'est justement le genre d'interaction que
    la mesure doit pouvoir révéler.
    """
    out: dict[str, float] = {}
    try:
        out.update(calculer(df_ltf, prefixe="ltf_"))
        if df_htf is not None:
            out.update(calculer(df_htf, prefixe="htf_"))
    except Exception:  # noqa: BLE001 — la mesure ne casse jamais la décision
        pass
    return out
