"""Assemblage des features de la porte de confluence.

Porté de V12 (`fusion/confluence_adapter.build_feats`). C'est la pièce qui
manquait : sans elle, `confluence_gate.evaluate` attendait un dict que personne
ne produisait.

CE QUE FAIT CE MODULE
---------------------
Deux séries de bougies en entrée — une timeframe haute (structure) et une
timeframe d'entrée (déclenchement) — et il en sort le dict exact que la porte
attend. Rien d'autre : aucune décision, aucun accès réseau, aucun état.

D'OÙ VIENT LE SENS DU SETUP
----------------------------
Du niveau S/R, et de lui seul : **sur un support → long, sur une résistance →
short**. C'est la méthode de Florent, et c'est un choix d'architecture délibéré
(conseil Codex, V12) : la bougie et l'OTE restent des portes ET indépendantes.
Les laisser fabriquer le setup qu'elles sont ensuite censées confirmer viderait
la confluence de son sens.

La tendance HTF n'est qu'un **contexte** : elle décide de la famille
(continuation si alignée, reversal sinon), pas du sens.

UN SEUL ÉCART ASSUMÉ PAR RAPPORT À V12
---------------------------------------
**Pas de plan géométrique.** V12 calcule un régime (`_geometric`) lu ensuite par
sa porte neuronale. V14 n'a pas cette porte : la clé n'aurait aucun lecteur.

(Le pilier bougie, longtemps réduit à deux patterns, utilise désormais le moteur
complet `candlesticks.net_bias_on_df` — 12 détecteurs, contexte de tendance et
automate de confirmation, comme en V12.)

``cost.edge_ok`` vaut **None** par défaut, jamais True : l'edge est inconnu tant
qu'un laboratoire ne l'a pas mesuré. C'est ce qui fait que le mode PROD
(`require_edge=True`) bloque tant que la mesure n'existe pas — exactement le
correctif fail-open de V12 (revue Codex du 17/07/2026).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from titanium.features import candlesticks, indicators, smc
from titanium.features.structure import (
    compute_profile,
    fib_context,
    sr_context,
    volume_context,
)

#: Incrémenter dès que la façon de CALCULER une feature change — la trace le porte.
BUILDER_VERSION = "1.1.0"

#: G5 accepte un displacement en SECOURS quand le moteur de formes est muet.
#:
#: Mesuré le 12/08/2026 sur 134 setups directionnels réels (mission Prime
#: ba74e58a) : G5 passait 6.7 % du temps. Sur les 125 échecs, 80 % venaient
#: d'un moteur muet — aucun motif, ou motif en attente de confirmation. Or un
#: displacement (2 ATR ou plus en 1 à 3 bougies, corps pleins) EST une bougie
#: de confirmation, et `candlesticks` ne le voit pas : il cherche des FORMES
#: (englobante, marteau), jamais l'AMPLITUDE.
#:
#: Secours et non remplacement : sur les 18 setups où le displacement était
#: aligné, 8 avaient un moteur de formes disant le CONTRAIRE. Les écraser
#: serait un passage en force. On ne comble que le silence.
#:
#: Effet mesuré : G5 6.7 % → 14.2 %, quorum 21.6 % → 24.6 % (+4 setups / 134).
#: Mettre à False pour revenir au comportement d'avant.
DISPLACEMENT_FALLBACK = True

#: Amplitude minimale d'un displacement retenu, en multiples d'ATR. C'est le
#: seuil ICT canonique ; le relever rend le secours muet, l'abaisser en fait
#: un tampon (à 1 ATR, une bougie ordinaire suffirait).
DISPLACEMENT_MIN_ATR = 2.0

#: Fenêtre de recherche et fraîcheur exigée, en barres. Un displacement vieux
#: de quinze barres ne confirme plus l'entrée d'aujourd'hui.
DISPLACEMENT_LOOKBACK = 20
DISPLACEMENT_FRAICHEUR = 4

#: Émotion neutre : ni véto, ni attente. Utilisée quand aucun pôle n'est branché.
NEUTRAL_EMOTION = {"filter_block": None, "stale": False, "confidence": 1.0, "wait": False}


def _normalize(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Ramène les colonnes au contrat interne : open/high/low/close/v.

    MT5 rend ``tick_volume``, yfinance ``Volume``, V12 utilisait ``v``. Le profil
    de volume exige ``v`` : sans cette normalisation il rend silencieusement
    ``available: False`` et le pilier G2 est mort sans que rien ne le signale.
    """
    if df is None or len(df) == 0:
        return None
    d = df.rename(columns={c: str(c).lower() for c in df.columns})
    for source in ("tick_volume", "volume", "real_volume"):
        if "v" not in d.columns and source in d.columns:
            d = d.rename(columns={source: "v"})
            break
    if "v" not in d.columns:
        d = d.assign(v=0.0)
    return d if {"open", "high", "low", "close"}.issubset(d.columns) else None


def _trend(df_htf: pd.DataFrame) -> int:
    """Tendance HTF par rapport à l'EMA 200. 0 = indéterminée."""
    try:
        ema = smc.compute_ema200(df_htf["close"])
        px = float(df_htf["close"].iloc[-1])
        if not (ema == ema):        # NaN : historique trop court
            return 0
        return 1 if px > ema else (-1 if px < ema else 0)
    except Exception:  # noqa: BLE001
        return 0


def _fvg_actionnable(df: pd.DataFrame, side: str, price: float, tol: float) -> bool:
    """Une FVG ne compte que si elle est NON COMBLÉE **et** au contact du prix."""
    if not (price > 0) or not (tol > 0):
        return False
    for bot, top in smc.detect_fvg_unfilled(df, side):
        lo, hi = (bot, top) if bot <= top else (top, bot)
        if (lo - tol) <= price <= (hi + tol):
            return True
    return False


def _liquidity(df: pd.DataFrame, price: float, atr: float) -> int:
    """Pilier liquidité : +1 / −1 / 0.

    Deux signaux directionnels, chacun récent et normalisé : sweep de liquidité,
    ou FVG active au contact. L'Order Block n'entre PAS ici — il appartient au
    pilier OTE/OB, sinon la même zone serait comptée deux fois.
    """
    try:
        tol = (smc.compute_fib_tolerance(df.tail(min(50, len(df))), price, atr)
               if price > 0 else 0.0)
        haussier = (smc.detect_liquidity_sweep(df, smc.BUY, atr)
                    or _fvg_actionnable(df, smc.BUY, price, tol))
        baissier = (smc.detect_liquidity_sweep(df, smc.SELL, atr)
                    or _fvg_actionnable(df, smc.SELL, price, tol))
        if haussier and not baissier:
            return 1
        if baissier and not haussier:
            return -1
        return 0
    except Exception:  # noqa: BLE001
        return 0


def _ote_ob(df: pd.DataFrame, price: float, fib_ctx: dict) -> int:
    """Pilier OTE/OB : le nom promet DEUX conditions, on les exige toutes les deux.

    Le prix doit être dans la golden zone non invalidée **et** aligné sur un OB
    actif du même côté. Le bug d'origine (revue Codex 22/07) : la porte ne
    testait que `in_ote`, aucun Order Block malgré son nom.
    """
    try:
        if not (price > 0) or not fib_ctx.get("available") or not fib_ctx.get("in_ote"):
            return 0
        if fib_ctx.get("invalidated"):
            return 0
        direction = int(fib_ctx.get("direction") or 0)
        if direction == 0:
            return 0
        cote = smc.BUY if direction > 0 else smc.SELL
        aligne, statut, _q = smc.has_ob_or_fvg_alignment(df, cote, price)
        # On accepte aussi "fvg" : le rejeter rendait G4 inatteignable par FVG
        # (correctif présent dans la copie vivante de V12, absent de la copie morte).
        return direction if (aligne and statut in ("intact", "tested", "fvg")) else 0
    except Exception:  # noqa: BLE001
        return 0


def _displacement_dir(df: pd.DataFrame) -> tuple[int, float]:
    """Secours G5 : direction et force du displacement récent, ou (0, 0.0).

    Voir `DISPLACEMENT_FALLBACK` pour la mesure qui justifie ce secours. On ne
    retient que le plus récent : deux displacements opposés dans la fenêtre
    ne se compensent pas, le dernier a le dernier mot.
    """
    if not DISPLACEMENT_FALLBACK:
        return 0, 0.0
    try:
        from titanium.features.ict_structure import detect_displacement

        recents = [
            d for d in detect_displacement(
                df, min_atr_mult=DISPLACEMENT_MIN_ATR,
                lookback=DISPLACEMENT_LOOKBACK)
            if d.end_index >= DISPLACEMENT_LOOKBACK - DISPLACEMENT_FRAICHEUR
        ]
        if not recents:
            return 0, 0.0
        dernier = max(recents, key=lambda d: d.end_index)
        return int(dernier.direction), float(dernier.strength)
    except Exception:  # noqa: BLE001
        return 0, 0.0


def _setup_side(sr_ctx: dict) -> int | None:
    """Sens du setup : support → long, résistance → short. Rien d'autre."""
    if not sr_ctx.get("available") or sr_ctx.get("on_level") is None:
        return None
    kind = sr_ctx.get("on_level_kind")
    return +1 if kind == "support" else (-1 if kind == "resistance" else None)


def _setup_family(setup_side: int | None, trend: int) -> str | None:
    """Famille : alignée sur la tendance = continuation, sinon reversal."""
    if setup_side not in (-1, 1):
        return None
    return "continuation" if trend in (-1, 1) and setup_side == trend else "reversal"


def _weekend_block(now: datetime, marche_continu: bool = False) -> bool:
    """Vendredi soir et week-end : frais de portage, edge non prouvé.

    Vendredi à partir de 20 h UTC, plus samedi et dimanche.

    ⚠️ `marche_continu=True` désarme la règle. Elle a été écrite pour les
    marchés qui FERMENT : le vendredi soir on paie le portage, on subit le
    gap du dimanche soir, et le stop n'est pas gardé pendant 48 h. Rien de
    tout cela ne vaut pour la crypto, qui cote en continu — or c'est le SEUL
    marché ouvert pendant cette fenêtre. Mesuré le 17/08/2026 sur
    `results/shadow_prod.ndjson` : 3391 setups ENTER du 10 au 17/08, et
    **zéro** les 15 et 16/08. Le week-end n'était pas calme, il était éteint,
    et la crypto payait un garde-fou conçu contre un risque qu'elle ne court
    pas. Le plafond de coût de `titanium.sizing` continue, lui, d'écarter
    les cryptos à spread large (24 des 31 symboles du catalogue).
    """
    if marche_continu:
        return False
    jour, heure = now.weekday(), now.hour
    if jour == 4 and heure >= 20:
        return True
    return jour in (5, 6)


def build_feats(df_ltf: pd.DataFrame | None, df_htf: pd.DataFrame | None, *,
                price: float | None = None,
                emotion: dict | None = None,
                edge_ok: bool | None = None,
                now: datetime | None = None,
                marche_continu: bool = False,
                min_bars_ltf: int = 60,
                min_bars_htf: int = 60,
                with_indicators: bool = False) -> dict:
    """Assemble le dict attendu par `confluence_gate.evaluate`.

    Args:
        df_ltf: bougies de la timeframe d'ENTRÉE (déclenchement, OTE, bougie).
        df_htf: bougies de la timeframe HAUTE (tendance, S/R, profil de volume).
        price: prix de référence. Par défaut, la dernière clôture LTF.
        emotion: état émotionnel. Par défaut neutre — **jamais absent**, car un
            dict incomplet fait BLOCK côté porte.
        edge_ok: edge mesuré pour ce contexte. **None = inconnu**, ce qui bloque
            en mode PROD. Ne passer True que sur mesure réelle.
        now: horodatage (tests, rejeu).
        marche_continu: True pour un marché qui ne ferme pas (crypto). Désarme
            le blocage week-end, qui ne protège que des marchés fermés.
        min_bars_ltf / min_bars_htf: en-dessous, `data_valid=False`.

    Returns:
        Le dict de features. En cas de données insuffisantes, il porte
        ``data_valid: False`` et la porte bloquera — jamais d'exception.
    """
    maintenant = now or datetime.now(timezone.utc)
    if maintenant.tzinfo is None:
        maintenant = maintenant.replace(tzinfo=timezone.utc)

    invalide = {
        "data_valid": False,
        "emotion": dict(emotion or NEUTRAL_EMOTION),
        "cost": {"edge_ok": edge_ok,
                 "weekend_block": _weekend_block(maintenant, marche_continu)},
        "_trace": {"version": BUILDER_VERSION,
                   "decided_at": maintenant.isoformat(),
                   "reason": "donnees_insuffisantes"},
    }

    ltf, htf = _normalize(df_ltf), _normalize(df_htf)
    if ltf is None or htf is None or len(ltf) < min_bars_ltf or len(htf) < min_bars_htf:
        return invalide

    try:
        ref = float(price) if price is not None else float(ltf["close"].iloc[-1])
    except (TypeError, ValueError, IndexError):
        return invalide
    if not (ref > 0) or ref != ref:
        return invalide

    atr_ltf = smc.compute_atr(ltf)
    trend = _trend(htf)

    # Tolérance S/R adaptée à la volatilité : une tolérance fixe de 0.4 %
    # bloquait tout prix situé entre deux niveaux (constat V12).
    tol_pct = 1.5
    if atr_ltf > 0:
        tol_pct = float(min(3.0, max(1.5, atr_ltf / ref * 100.0)))

    fenetre = min(300, len(htf))
    profil = compute_profile(htf, window=fenetre)
    sr_ctx = sr_context(htf, ref, volume_profile=profil, tol_pct=tol_pct)
    vp_ctx = volume_context(htf, ref, window=fenetre)

    fib_ctx = fib_context(ltf, ref)
    ote_dir = _ote_ob(ltf, ref, fib_ctx)
    liquidity = _liquidity(ltf, ref, atr_ltf)
    # Moteur de chandeliers COMPLET : contexte de tendance croisé, automate de
    # confirmation, une seule contribution retenue. `uptrend=None` quand la
    # tendance est indéterminée — le contexte est alors ignoré plutôt qu'inventé.
    cndl = candlesticks.net_bias_on_df(
        ltf, uptrend=(trend > 0) if trend != 0 else None)
    candle_dir = int(cndl.get("direction") or 0)
    candle_force = abs(float(cndl.get("score", 0.0)))
    # Secours : le moteur de FORMES est muet, l'AMPLITUDE parle peut-être.
    candle_source = "formes" if candle_dir else ""
    if candle_dir == 0:
        candle_dir, candle_force = _displacement_dir(ltf)
        if candle_dir:
            candle_source = "displacement"

    setup_side = _setup_side(sr_ctx)
    setup_family = _setup_family(setup_side, trend)

    # Horodatage de la DERNIÈRE BARRE CLÔTURÉE de la timeframe d'entrée.
    # C'est la seule ancre stable pour dédupliquer une entrée : `decided_at`
    # vaut `now()` et change à chaque balayage, ce qui rend toute clé
    # d'idempotence inopérante. Défaut constaté en réel le 07/08/2026 —
    # trois positions ouvertes sur AUDUSD en quelques minutes.
    try:
        bar_time = ltf.index[-1].isoformat()
    except (AttributeError, IndexError):
        bar_time = ""

    return {
        "data_valid": True,
        "trend": trend,
        "setup_side": setup_side,
        "setup_family": setup_family,
        "on_sr_level": bool(sr_ctx.get("on_level")) if sr_ctx.get("available") else False,
        "fair_value": bool(vp_ctx.get("on_fair_price_zone")) if vp_ctx.get("available") else False,
        "liquidity": liquidity,
        "ote": ote_dir,
        "candle": candle_dir,
        "emotion": dict(emotion or NEUTRAL_EMOTION),
        # edge_ok=None ⇒ inconnu ⇒ BLOCK en PROD. Jamais True par défaut.
        "cost": {"edge_ok": edge_ok,
                 "weekend_block": _weekend_block(maintenant, marche_continu)},
        "strengths": {
            "trend_sr": round(float(sr_ctx.get("on_level_strength", 0.0)), 3),
            "fair_value": 0.5 if vp_ctx.get("on_fair_price_zone") else 0.0,
            "liquidity": 0.6 if liquidity != 0 else 0.0,
            "ote_ob": 0.7 if ote_dir != 0 else 0.0,
            "candle_confirmed": candle_force,
        },
        "_trace": {
            "version": BUILDER_VERSION,
            "decided_at": maintenant.isoformat(),
            # `bar_time` ≠ `decided_at` : le premier identifie la BARRE, le second
            # l'instant du calcul. Toute déduplication doit utiliser `bar_time`.
            "bar_time": bar_time,
            "price": ref,
            "atr": atr_ltf,
            "sr": sr_ctx.get("note"),
            "fair_value": vp_ctx.get("note"),
            "ote": fib_ctx.get("note"),
            # ── Valeurs NUMÉRIQUES des objets regardés. Le pont MT5 trace
            #    celles-ci : sans elles il devrait analyser les phrases
            #    ci-dessus, et le graphique finirait par raconter autre chose
            #    que ce qui a décidé.
            "sr_level": sr_ctx.get("on_level"),
            "sr_strength": sr_ctx.get("on_level_strength"),
            "vpoc": vp_ctx.get("vpoc"),
            "ote_zone": fib_ctx.get("ote_zone"),
            "ote_invalidation": fib_ctx.get("invalidation"),
            # FVG encore ouvertes du côté du setup, bornées pour ne pas gonfler
            # la trace : au-delà de 6 zones le graphique devient illisible.
            "fvg_open": [
                [round(b, 8), round(h, 8)]
                for b, h in smc.detect_fvg_unfilled(
                    ltf, smc.BUY if (setup_side or 0) > 0 else smc.SELL)[-6:]
            ] if setup_side in (-1, 1) else [],
            # Quelle source a fourni G5 : "formes", "displacement", ou "" si
            # aucune. Sans cette trace on ne pourrait pas, plus tard, comparer
            # l'espérance des deux sources sur les trades journalisés.
            "candle_source": candle_source,
            "candle_patterns": cndl.get("patterns", []),
            "candle_pending": cndl.get("pending_confirmation", []),
            "candle_confirmed_from_prev": cndl.get("confirmed_from_prev", []),
            "bars": {"ltf": len(ltf), "htf": len(htf)},
            # Panel d'indicateurs — MESURE SEULE. Aucune porte ne le lit, aucun
            # verdict n'en dépend. Il est journalisé avec le trade pour qu'on
            # puisse, plus tard et sur des données, savoir lesquels séparent
            # vraiment les gagnants des perdants.
            # Coûteux (~50 séries) : désactivé par défaut, activé par la boucle
            # d'amorçage qui, elle, journalise.
            "indicators": (indicators.panel(ltf, htf) if with_indicators else {}),
        },
    }


def risk_context_from(feats: dict, *, equity: float, risk_pct: float,
                      **surcharges) -> dict:
    """Construit le contexte du RiskGate à partir des features et du compte.

    Les défauts sont **sûrs** : rien n'est halté ni bloqué, mais l'appelant doit
    fournir explicitement equity et risk_pct. `side` et `confidence` ne sont pas
    ici — ils viennent des portes et de la délibération.
    """
    trace = feats.get("_trace") or {}
    contexte = {
        "price": trace.get("price", 0.0),
        "atr": trace.get("atr", 0.0),
        "equity": equity,
        "risk_pct": risk_pct,
        "halted": False,
        "circuit_breaker": False,
        "fundamentals_block": False,
        "fundamentals_reduce": False,
        "trend": int(feats.get("trend") or 0),
        "gross_exposure_pct": 0.0,
        "emotion_available": True,
        "emotion_would_block": bool((feats.get("emotion") or {}).get("filter_block")),
        "timeframe": "",
        "roundtrip_cost": None,
    }
    contexte.update(surcharges)
    return contexte
