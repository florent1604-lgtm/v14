"""Contexte ÉMOTION — PORTÉ DE V12 le 14/09/2026, sur autorité de Florent.

Source : `v12/poles/emotion/market_context.py`. La partie PURE
(`assemble_context`, les normalisations, la fraîcheur) est reprise telle
quelle. Seuls les accès aux données changent : V12 lisait
`data.mt5_provider`, V14 lit `titanium.data.mt5_vendor`.

TROIS ÉCARTS ASSUMÉS PAR RAPPORT À V12, tous dans le sens sûr :

1. **Chemin crypto retiré.** V12 nourrit la crypto par Binance WS (delta-volume
   réel, funding, long/short). V14 n'a pas ces stores ici — `collecteur_micro-
   structure.py` existe mais alimente un autre organe. Les cryptos passent donc
   par le chemin MT5, qui les cote toutes. Conséquence honnête : leur émotion
   est plus GROSSIÈRE qu'en V12 (pas de funding, pas de positionnement foule),
   et le moteur baisse sa confiance de lui-même — c'est le fail-safe prévu par
   la conception, pas un contournement.

2. **Risque macro omis.** `fundamentals.risk_scorer` n'existe pas sur cette
   branche de V14 (le module macro vit sur `feat/macro-delivery`). Une donnée
   absente est OMISE, jamais inventée : c'est la règle du module d'origine.

3. **Fraîcheur ancrée sur le tick MT5.** Marché fermé ⇒ tick vieux ⇒ STALE
   automatique ⇒ la porte attend. V12 avait dû corriger ce point : `get_tick`
   rend un horodatage ISO-8601 et non un epoch, et sans le parser `delta_ts`
   restait `None`, donc STALE ne se déclenchait JAMAIS sur MT5. Le piège est
   conservé ici, résolu de la même façon.

Docstring d'origine V12 conservée mot pour mot dans `_DOC_V12`.
"""

from __future__ import annotations

_DOC_V12 = """emotion/market_context.py — Adaptateur DONNÉES RÉELLES → contexte émotion (v12, 14/07/2026).

Branche le segment ÉMOTION (`emotion_engine`) sur les vrais stores de marché de
Titanium SANS le coupler : `emotion_engine` reste pur/testable ; ici on lit les
sources vivantes (Binance WS delta-volume, Futures funding/long-short,
fondamentaux macro, bougies 30 s) et on assemble le dict `context`.

FIDÉLITÉ (règle Florent — latences broker/Binance non contrôlées) : la source la
plus rapide (delta-volume) porte son timestamp ; `source_age_s` = l'âge de la
donnée la plus vieille réellement utilisée → le moteur marque STALE et baisse la
confiance. Une donnée absente est OMISE (fail-safe), jamais inventée.

Séparation des responsabilités :
  · `live_raw(symbol)`      lit les stores vivants (couplage Titanium, isolé ici) ;
  · `assemble_context(raw)` pur → dict `context` (testable sans le bot) ;
  · `build_context` / `emotion_for` : la chaîne complète.

⚠️ Read-only. Aucun branchement dans une décision de trading sans protocole M2.
"""


import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

_log = logging.getLogger(__name__)


def _clamp(x: float, lo: float, hi: float) -> float:
    if x != x:          # NaN propagé (jamais borné silencieusement à hi)
        return x
    return max(lo, min(hi, x))


def _finite(x: Any) -> Optional[float]:
    """float(x) si fini, sinon None (red-team Codex : un NaN/inf ou un non-numérique
    est une donnée ABSENTE, jamais une valeur extrême)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass
class RawInputs:
    """Entrées brutes d'un actif, avant normalisation. Tout est Optional : une
    source absente reste None et sera simplement omise du contexte."""
    delta_pct: Optional[float] = None          # (buy-sell)/total ∈ [-1,1]
    delta_ts: Optional[float] = None           # epoch UTC de la mesure delta
    funding: Optional[float] = None            # taux de funding (crypto)
    long_short_ratio: Optional[float] = None   # positionnement foule (>0)
    macro_risk: Optional[float] = None         # score de risque macro 0..100
    candles: Any = None                        # pd.DataFrame OHLCV 30s (high/low/close/v)
    now: Optional[float] = None                # epoch de référence (injectable pour tests)


def _candle_features(df):
    """Bougies OHLCV → (atr_zscore, volume_surge, valence_momentum). Chacun None
    si pas assez d'historique (fail-safe). Aucune dépendance dure à pandas."""
    if df is None:
        return None, None, None
    try:
        n = len(df)
    except Exception:
        return None, None, None
    if n < 8:
        return None, None, None

    try:
        highs = [float(x) for x in df["high"].tolist()]
        lows = [float(x) for x in df["low"].tolist()]
        closes = [float(x) for x in df["close"].tolist()]
        vols = [float(x) for x in df["v"].tolist()]
    except Exception:
        return None, None, None

    win = min(50, n)
    rng = [h - l for h, l in zip(highs[-win:], lows[-win:])]

    # atr_zscore : la barre courante est-elle anormalement volatile vs son histoire ?
    atr_z = None
    if len(rng) >= 8:
        mean_r = sum(rng) / len(rng)
        var = sum((r - mean_r) ** 2 for r in rng) / len(rng)
        std = var ** 0.5
        if std > 1e-12:
            atr_z = (rng[-1] - mean_r) / std

    # volume_surge 0..1 : volume de la dernière barre vs sa moyenne récente.
    vol_s = None
    recent = vols[-win:]
    mean_v = sum(recent) / len(recent) if recent else 0.0
    if mean_v > 1e-12:
        ratio = vols[-1] / mean_v
        vol_s = _clamp((ratio - 1.0) / 2.0, 0.0, 1.0)   # ×1 →0, ×3 →1

    # valence_momentum : l'émotion monte/retombe ? proxy = rendement court terme.
    mom = None
    if n >= 7 and closes[-7] > 1e-12:
        ret = closes[-1] / closes[-7] - 1.0
        mom = _clamp(ret * 20.0, -1.0, 1.0)             # ±5% ⇒ ±1

    return atr_z, vol_s, mom


def assemble_context(raw: RawInputs, *, max_age_s: float = 30.0) -> dict:
    """RawInputs → dict `context` pour `compute_emotion`. PUR (aucun accès store).
    N'ajoute que les clés dont la donnée est présente ; calcule `source_age_s`
    depuis la source la plus rapide (delta-volume)."""
    ctx: dict = {}
    now = raw.now if raw.now is not None else time.time()
    ages = []

    # Toute valeur non finie (NaN/inf) ou non numérique est OMISE (source absente).
    dts = _finite(raw.delta_ts)
    if dts is not None:
        ages.append(max(0.0, now - dts))
    dv = _finite(raw.delta_pct)
    if dv is not None:
        ctx["delta_volume"] = _clamp(dv, -1.0, 1.0)
    fr = _finite(raw.funding)
    if fr is not None:
        ctx["funding_rate"] = fr
    ls = _finite(raw.long_short_ratio)
    if ls is not None and ls > 0:
        ctx["long_short_ratio"] = ls
    mr = _finite(raw.macro_risk)
    if mr is not None:
        ctx["macro_risk"] = mr

    atr_z, vol_s, mom = _candle_features(raw.candles)
    atr_z, vol_s, mom = _finite(atr_z), _finite(vol_s), _finite(mom)
    if atr_z is not None:
        ctx["atr_zscore"] = round(atr_z, 3)
    if vol_s is not None:
        ctx["volume_surge"] = round(vol_s, 3)
    if mom is not None:
        ctx["valence_momentum"] = round(mom, 3)

    if ages:
        ctx["source_age_s"] = round(max(ages), 1)
    ctx["max_age_s"] = max_age_s
    return ctx


def _candle_body_pressure(df, k: int = 5) -> Optional[float]:
    """Proxy de pression acheteur/vendeur pour MT5 (Axi ne diffuse pas le carnet
    L2, donc pas de vrai order-flow). Moyenne, sur les k dernières bougies, de
    (close-open)/(high-low) ∈ [-1,1] : clôtures près du haut = achat, du bas = vente."""
    try:
        opens = [float(x) for x in df["open"].tolist()]
        highs = [float(x) for x in df["high"].tolist()]
        lows = [float(x) for x in df["low"].tolist()]
        closes = [float(x) for x in df["close"].tolist()]
    except Exception:
        return None
    kk = min(k, len(closes))
    vals = []
    for i in range(-kk, 0):
        rng = highs[i] - lows[i]
        if rng > 1e-12:
            vals.append((closes[i] - opens[i]) / rng)
    if not vals:
        return None
    return _clamp(sum(vals) / len(vals), -1.0, 1.0)


def _is_crypto(symbol: str) -> bool:
    return "/" in symbol   # "BTC/USDT" (Binance) vs "US50"/"XAUUSD"/"EURUSD" (MT5)


def live_raw(symbol: str) -> RawInputs:
    """Stores VIVANTS crypto (Binance WS + Futures + macro). Fail-safe.
    À appeler DANS le process du bot (les stores sont en mémoire du process)."""
    raw = RawInputs(now=time.time())
    try:
        from data.binance_ws import delta_vol, candle_store
        d = delta_vol.get(symbol)
        if d:
            raw.delta_pct = d.get("delta_pct")
            raw.delta_ts = d.get("ts") or None
        raw.candles = candle_store.get(symbol)
    except Exception as exc:
        _log.warning("[EMOTION] delta/bougies Binance %s : %s", symbol, exc)
    try:
        from data.futures_data import futures_store
        f = futures_store.get(symbol)
        if f and f.get("ok"):
            raw.funding = f.get("funding")
            raw.long_short_ratio = f.get("long_short_ratio")
    except Exception as exc:
        _log.warning("[EMOTION] futures %s : %s", symbol, exc)
    # Risque macro : absent de cette branche de V14. Donnee OMISE, jamais
    # inventee — la regle du module d'origine.
    return raw


def _normaliser_colonnes(df):
    """Ramene les colonnes MT5 au contrat attendu par le moteur d'emotion.

    ⚠️ LA FUSION SE JOUE ICI. V12 lit `df["v"]` ; V14 rend `tick_volume`. Sans
    cette ligne, le volume n'arrive jamais : `volume_surge` reste None, l'axe
    AROUSAL — la moitie du modele circumplex — vaut 0.0 pour tous les actifs, et
    la confiance s'effondre a 0,12, sous le seuil actionnable de 0,35. Branche
    ainsi, V14 aurait dit WAIT sur TOUS les trades, et on aurait conclu que
    l'organe etait trop severe alors qu'il etait simplement aveugle.

    Mesure du 14/09/2026 avant correction : arousal=0.0 et confidence=0.12 sur
    les six actifs testes, sans une seule erreur levee.

    C'est exactement le piege que `titanium/features/builder.py` documente pour
    le profil de volume — « sans cette normalisation il rend silencieusement
    available: False et le pilier G2 est mort sans que rien ne le signale ».
    Meme faute, meme silence, autre organe. V14 savait la corriger, V12 savait
    ressentir : la fusion est la.
    """
    if df is None:
        return None
    try:
        if "v" in df.columns:
            return df
        for source in ("tick_volume", "real_volume", "volume", "Volume"):
            if source in df.columns:
                df = df.copy()
                df["v"] = df[source]
                return df
    except Exception:  # noqa: BLE001 — une normalisation ne casse pas la lecture
        return df
    return df


def live_raw_mt5(symbol: str, *, tf: str = "M15", n: int = 120) -> RawInputs:
    """Stores VIVANTS MT5/Axi (là où passent les VRAIS trades démo/réel : indices,
    forex, métaux). Signaux plus grossiers que le crypto (pas de funding/long-short,
    pas d'order-flow L2) — la confiance du moteur baisse d'elle-même (fail-safe).
    Fraîcheur ancrée sur le dernier TICK (marché fermé → stale automatique)."""
    raw = RawInputs(now=time.time())
    try:
        from titanium.data.mt5_vendor import get_rates
        df = _normaliser_colonnes(get_rates(symbol, tf, n))
        raw.candles = df
        if df is not None and len(df) >= 2:
            raw.delta_pct = _candle_body_pressure(df)
    except Exception as exc:
        _log.warning("[EMOTION] bougies MT5 %s : %s", symbol, exc)
    # Fraîcheur : get_tick renvoie `ts` en ISO-8601 (PAS un epoch) → parser, sinon
    # delta_ts resterait None et STALE ne se déclencherait JAMAIS sur MT5.
    try:
        # Fraicheur ancree sur le dernier TICK, corrige en UTC par le decalage
        # serveur MESURE PARTAGE (meme autorite que get_rates, tests/test_horloge_serveur.py).
        # ⚠️ tick.time est en heure SERVEUR (Axi = UTC+3) : sans la correction,
        # un tick frais d'3 h dans le futur — donc age negatif, jamais stale —
        # ou un marche ferme lu 3 h trop vieux derangent la fenetre STALE dans
        # les deux sens. `decalage_serveur_cache` est TTL-cache (900 s) et
        # rend 0 tant qu'aucune mesure n'est credible : aucun appel MT5
        # supplementaire en regime etabli.
        from titanium.data.mt5_vendor import _mt5, decalage_serveur_cache, mt5_lock, mt5_session
        with mt5_lock, mt5_session():
            tk = _mt5().symbol_info_tick(symbol)
        horodatage = float(getattr(tk, "time", 0) or 0) if tk else 0.0
        if horodatage > 0:
            raw.delta_ts = horodatage - decalage_serveur_cache((symbol,))
    except Exception as exc:
        _log.warning("[EMOTION] tick MT5 %s : %s", symbol, exc)
    # Risque macro : absent de cette branche de V14. Donnee OMISE, jamais
    # inventee — la regle du module d'origine.
    return raw


def build_context(symbol: str, *, max_age_s: Optional[float] = None,
                  raw: Optional[RawInputs] = None) -> dict:
    """Contexte émotion d'un actif depuis les vraies données (ou `raw` injecté).
    Route automatiquement crypto Binance ('BTC/USDT') vs MT5 ('US50', 'XAUUSD')."""
    if raw is not None:
        return assemble_context(raw, max_age_s=max_age_s if max_age_s is not None else 30.0)
    if _is_crypto(symbol):
        return assemble_context(live_raw(symbol),
                                max_age_s=max_age_s if max_age_s is not None else 30.0)
    return assemble_context(live_raw_mt5(symbol),
                            max_age_s=max_age_s if max_age_s is not None else 60.0)


def emotion_for(symbol: str, *, max_age_s: Optional[float] = None,
                raw: Optional[RawInputs] = None):
    """Émotion de marché LIVE d'un actif (crypto OU MT5), read-only → EmotionState."""
    from titanium.emotion.engine import compute_emotion
    return compute_emotion(build_context(symbol, max_age_s=max_age_s, raw=raw))


# ═══════════════════════════ pont vers la boucle V14 ═════════════════════════


def emotion_depuis_barres(symbole: str, df, *, max_age_s: float = 60.0):
    """Emotion d'un actif à partir de bougies DEJA LUES par l'appelant.

    Amélioration apportée par V14 à l'organe de V12. `live_raw_mt5` refait une
    requête d'historique de 120 barres par actif ; la boucle en évalue une
    trentaine par tour et vient précisément de lire ces mêmes barres pour
    `build_feats`. Les redemander paierait deux fois le verrou MT5 — « un
    observateur qui affame l'observé ne mesure plus rien ».

    Seul le TICK est encore demandé : c'est un appel local et peu coûteux, et
    il est indispensable. Sans lui, la fraîcheur s'ancrerait sur l'horodatage
    de la dernière bougie, vieux de 15 minutes en M15, et TOUT serait déclaré
    STALE — donc WAIT sur tous les actifs, en permanence.
    """
    brut = RawInputs(now=time.time())
    brut.candles = _normaliser_colonnes(df)
    if brut.candles is not None:
        brut.delta_pct = _candle_body_pressure(brut.candles)
    try:
        from titanium.data.mt5_vendor import _mt5, decalage_serveur_cache, mt5_lock, mt5_session
        with mt5_lock, mt5_session():
            tk = _mt5().symbol_info_tick(symbole)
        horodatage = float(getattr(tk, "time", 0) or 0) if tk else 0.0
        # Meme correction serveur→UTC que live_raw_mt5 : voir la note la-bas.
        if horodatage > 0:
            brut.delta_ts = horodatage - decalage_serveur_cache((symbole,))
    except Exception as exc:  # noqa: BLE001
        _log.warning("[EMOTION] tick %s : %s", symbole, exc)
    return emotion_for(symbole, max_age_s=max_age_s, raw=brut)


def bloc_emotion(symbole: str, df) -> dict:
    """Forme normale attendue par `confluence_gate`, depuis l'organe vivant.

    Le repli est celui de V12 et NON celui de V14 : émotion indisponible ⇒
    `stale=True, confidence=0.0, wait=True`, donc la porte ATTEND. V14 rendait
    `confidence=1.0, wait=False` et lisait l'absence de mesure comme une
    approbation — c'est le défaut que ce port corrige, il serait absurde de le
    réintroduire ici.

    La PERCEPTION accompagne les clés de gating (label, valence, arousal,
    momentum). V12 avait dû corriger ce point le 28/07 : ne transmettre que le
    gating jetait le ressenti, et le journal ne voyait que des `null` alors que
    l'organe fonctionnait parfaitement. On ne refait pas cette erreur.
    """
    vide = {"filter_block": None, "stale": True, "confidence": 0.0,
            "wait": True, "available": False, "label": None,
            "valence": None, "arousal": None}
    try:
        st = emotion_depuis_barres(symbole, df)
    except Exception:  # noqa: BLE001 — un organe qui casse ne casse pas la boucle
        return vide
    if not getattr(st, "available", False):
        return {**vide, "label": getattr(st, "label", None)}
    sens = {"long": 1, "short": -1}.get(getattr(st, "filter_block", None))
    return {
        "filter_block": sens,
        "stale": bool(st.stale),
        "confidence": float(st.confidence),
        "wait": bool(st.stale),
        "available": True,
        "label": st.label,
        "valence": round(float(st.valence) / 100.0, 4),
        "arousal": round(float(st.arousal) / 100.0, 4),
        "momentum": getattr(st, "momentum", None),
        "contrarian_bias": getattr(st, "contrarian_bias", None),
    }
