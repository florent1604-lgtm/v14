"""Banc de tests du pôle ÉMOTION — moteur circumplex et contexte MT5.

Exécutable sans terminal MT5 : le contexte est fabriqué par `RawInputs`
injecté, jamais par une lecture réelle. Trois contrats y sont figés :

1. fail-safe du moteur — une contribution non convertible ou levante est une
   donnée ABSENTE, jamais un crash de l'agrégation (sonde du 15/09 : une fn
   rendant "abc" crashait tout l'organe dans la boucle) ;
2. fidélité — stale/fiable borné, confiance jamais négative ;
3. fraîcheur — le tick MT5 est en heure SERVEUR : sans la correction du
   décalage partagé, la fenêtre STALE est dérangée dans les deux sens
   (Axi UTC+3 : tick frais lu dans le futur = jamais stale ; marché fermé
   lu 3 h trop vieux = stale alors que le tick est frais à l'échelle MT5).
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from titanium.emotion.contexte import (
    RawInputs,
    _normaliser_colonnes,
    assemble_context,
    bloc_emotion,
    emotion_depuis_barres,
)
from titanium.emotion.engine import (
    REGISTRY,
    EmotionSignal,
    compute_emotion,
)

# ─────────────────────────── moteur : fail-safe ──────────────────────────────


def test_contribution_non_convertible_est_omise_pas_un_crash():
    """Une fn de signal qui rend autre chose qu'un nombre est une donnée
    absente — l'agrégation continue avec les signaux sains. Avant correction,
    `float("abc")` levait HORS du try et crashait compute_emotion entier :
    un seul signal mal nourri tuait l'organe dans la boucle."""

    def chaine(ctx):
        return "abc"

    sig = EmotionSignal("sonde_chaine", "valence", 1.0, chaine, "sonde")
    REGISTRY.append(sig)
    try:
        st = compute_emotion({"delta_volume": 0.5})
    finally:
        REGISTRY.remove(sig)
    assert st.available is True
    assert "sonde_chaine" not in st.breakdown          # omise, pas comptée
    assert st.breakdown == {"delta_volume": 0.5}       # les sains restent
    assert st.valence == 50.0                          # (0.5 → ×100)


def test_contribution_qui_leve_est_omise():
    """Le contrat d'origine : une fn qui lève est ignorée, sans poison."""

    def boom(ctx):
        raise RuntimeError("panne source")

    sig = EmotionSignal("sonde_boom", "arousal", 1.0, boom, "sonde")
    REGISTRY.append(sig)
    try:
        st = compute_emotion({"delta_volume": 0.5})
    finally:
        REGISTRY.remove(sig)
    assert st.available is True
    assert "sonde_boom" not in st.breakdown


def test_nan_dans_les_entrees_est_omis_et_inf_borne_par_le_clamp():
    """NaN : omis (donnée absente). inf entré dans un signal qui clamp : le
    clamp est le contrat du signal — il rend une valeur FINIE bornée, ici
    l'énergie maximale, jamais une contribution non finie."""
    st = compute_emotion({"delta_volume": float("nan"), "atr_zscore": float("inf")})
    assert "delta_volume" not in st.breakdown          # NaN → omis
    assert st.breakdown["volatility"] == 1.0           # inf → clampé au max
    assert st.available is True


# ─────────────────────────── moteur : fidélité ───────────────────────────────


def test_stale_abaisse_la_confiance_et_neutralise_lactionnable():
    st = compute_emotion({"delta_volume": 0.8, "source_age_s": 999.0, "max_age_s": 60.0})
    assert st.stale is True
    assert st.confidence <= 0.5
    assert st.filter_block is None                     # non actionnable
    assert st.contrarian_bias is None
    assert any("non actionnable" in r for r in st.reasons)


def test_confiance_jamais_negative():
    """Un poids négatif est impossible par construction ; si un registre
    injecté l'autorisait, une confiance négative lirait « pire qu'aucune
    donnée » comme un passant au seuil EMOTION_MIN_CONFIDENCE (0,35)."""

    def negatif(ctx):
        return 0.0

    sig = EmotionSignal("sonde_neg", "valence", -2.0, negatif, "sonde invalide")
    REGISTRY.append(sig)
    try:
        st = compute_emotion({"delta_volume": 0.5})
    finally:
        REGISTRY.remove(sig)
    assert st.confidence >= 0.0


def test_emotion_inconnue_marquee_stale_dans_le_bloc():
    """Le repli de bloc_emotion reste celui de V12 : indisponible ⇒ wait=True,
    la porte ATTEND — jamais le faux calme confidence=1.0 de V14."""
    bloc = bloc_emotion("EURUSD", None)
    assert bloc["available"] is False
    assert bloc["wait"] is True
    assert bloc["confidence"] == 0.0


# ───────────────────── contexte : fraîcheur & horloge serveur ────────────────


def _df(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "v": [1000.0 + 10 * i for i in range(n)],
    })


def test_tick_serveur_corrige_en_utc(monkeypatch):
    """Le tick MT5 est en heure SERVEUR (Axi UTC+3). La fraîcheur doit être
    calculée sur l'instant UTC — sinon un tick frais d'3 h dans le futur
    (âge négatif, jamais stale) et un marché fermé lu 3 h trop vieux
    dérangent la fenêtre STALE dans les deux sens."""

    decalage = 3 * 3600
    maintenant = 1_700_000_000.0
    tick_serveur = maintenant + decalage               # ce que MT5 rend à t

    import titanium.emotion.contexte as ctx_mod

    class FakeMT5:
        def symbol_info_tick(self, symbol):
            class T:
                time = tick_serveur
            return T()

    class FakeLock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeSession:
        def __enter__(self):
            return FakeMT5()

        def __exit__(self, *a):
            return False

    captures = {}

    def fake_decalage(symboles=(), *, force=False):
        captures["symboles"] = symboles
        return decalage

    monkeypatch.setattr(
        "titanium.data.mt5_vendor._mt5", lambda: FakeMT5())
    monkeypatch.setattr(
        "titanium.data.mt5_vendor.mt5_lock", FakeLock())
    monkeypatch.setattr(
        "titanium.data.mt5_vendor.mt5_session", lambda: FakeSession())
    monkeypatch.setattr(
        "titanium.data.mt5_vendor.decalage_serveur_cache", fake_decalage)

    raw = ctx_mod.live_raw_mt5("EURUSD", n=30)
    assert captures["symboles"] == ("EURUSD",)
    # Le timestamp retenu est l'instant UTC, pas l'heure serveur :
    assert raw.delta_ts == pytest.approx(tick_serveur - decalage)


def test_emotion_depuis_barres_utilise_le_tick_corrige(monkeypatch):
    """Même correction sur le chemin de la boucle (emotion_depuis_barres) :
    la fraîcheur ancrée sur l'horloge UTC du serveur, pas sur son heure murale."""

    decalage = 3 * 3600
    maintenant = time.time()
    tick_serveur = maintenant + decalage

    class FakeMT5:
        def symbol_info_tick(self, symbol):
            class T:
                time = tick_serveur
            return T()

    class FakeLock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeSession:
        def __enter__(self):
            return FakeMT5()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("titanium.data.mt5_vendor._mt5", lambda: FakeMT5())
    monkeypatch.setattr("titanium.data.mt5_vendor.mt5_lock", FakeLock())
    monkeypatch.setattr("titanium.data.mt5_vendor.mt5_session", lambda: FakeSession())
    monkeypatch.setattr(
        "titanium.data.mt5_vendor.decalage_serveur_cache",
        lambda symboles=(), *, force=False: decalage)

    st = emotion_depuis_barres("EURUSD", _df())
    # Tick frais à l'instant UTC ⇒ PAS stale (avant correction, l'heure
    # serveur non corrigée donnait un âge de -3 h : jamais stale non plus,
    # mais pour la mauvaise raison — et un marché fermé était stale 3 h
    # trop tôt).
    assert st.stale is False


def test_assemble_context_age_serveur_non_corrige_serait_negatif():
    """Garde de contrat : un âge négatif n'est jamais accepté par
    assemble_context (borné à 0) — c'est ce qui masquait le décalage serveur."""
    ctx = assemble_context(RawInputs(now=1000.0, delta_ts=2000.0))
    assert ctx["source_age_s"] == 0.0                  # max(0, now - ts)


def test_normalisation_volume_mt5():
    """La fusion V12→V14 se joue ici : V14 rend tick_volume, le moteur lit `v`.
    Sans normalisation, l'axe AROUSAL entier vaut 0 en silence."""
    n = 30
    df = pd.DataFrame({
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "tick_volume": [1000.0 + 10 * i for i in range(n)],
    })
    df = _normaliser_colonnes(df)                      # le pont V14 → V12
    ctx = assemble_context(RawInputs(now=time.time(), candles=df))
    assert "volume_surge" in ctx                       # le volume est arrivé
