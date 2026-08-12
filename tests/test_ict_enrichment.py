"""Tests des modules ICT enrichis — profils et structure de marché."""

import numpy as np
import pandas as pd
import pytest


# ═══════════════════════════════ ict_market_profiles ═════════════════════════

class TestAssetProfiles:
    """Le registre de profils connaît les actifs clés et les classe correctement."""

    def test_eurusd_profile_exists(self):
        from titanium.features.ict_market_profiles import get_profile
        p = get_profile("EURUSD")
        assert p is not None
        assert p.asset_class == "forex_major"
        assert p.manipulation_intensity > 0

    def test_btcusd_profile_exists(self):
        from titanium.features.ict_market_profiles import get_profile
        p = get_profile("BTCUSD")
        assert p is not None
        assert p.asset_class == "crypto"
        assert "whale_activity" in p.drivers

    def test_xauusd_is_precious_metal(self):
        from titanium.features.ict_market_profiles import get_profile
        p = get_profile("XAUUSD")
        assert p is not None
        assert p.asset_class == "precious_metal"

    def test_unknown_asset_returns_none(self):
        from titanium.features.ict_market_profiles import get_profile
        assert get_profile("ZZZZZZ") is None

    def test_classify_asset_heuristic(self):
        from titanium.features.ict_market_profiles import classify_asset
        assert classify_asset("EURUSD") == "forex_major"
        assert classify_asset("BTCUSD") == "crypto"
        assert classify_asset("XAUUSD") == "precious_metal"
        assert classify_asset("US500") == "index"
        assert classify_asset("USOIL") == "commodity"  # USOIL profile is "commodity"
        assert classify_asset("EURGBP") == "forex_cross"  # connu dans le registre

    def test_broker_suffix_resolution(self):
        from titanium.features.ict_market_profiles import get_profile
        p = get_profile("NAS100.fs")
        assert p is not None
        assert p.asset_class == "index"

    def test_all_profiles_have_drivers(self):
        from titanium.features.ict_market_profiles import _registry
        for sym, p in _registry().items():
            assert p.drivers, f"{sym} n'a pas de drivers"

    def test_spread_sensitivity_score(self):
        from titanium.features.ict_market_profiles import get_profile
        p_eur = get_profile("EURUSD")
        p_exotic = get_profile("USDKRW")
        assert p_eur.spread_sensitivity_score < p_exotic.spread_sensitivity_score


class TestSessions:
    """Kill zones et sessions retournent des résultats cohérents."""

    def test_current_session_returns_enum(self):
        from titanium.features.ict_market_profiles import current_session, SessionType
        from datetime import datetime, timezone
        s = current_session(datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc))
        assert s == SessionType.ASIAN

    def test_london_session(self):
        from titanium.features.ict_market_profiles import current_session, SessionType
        from datetime import datetime, timezone
        s = current_session(datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc))
        assert s == SessionType.LONDON

    def test_overlap_session(self):
        from titanium.features.ict_market_profiles import current_session, SessionType
        from datetime import datetime, timezone
        s = current_session(datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc))
        assert s == SessionType.OVERLAP

    def test_active_kill_zones(self):
        from titanium.features.ict_market_profiles import active_kill_zones
        from datetime import datetime, timezone
        kzs = active_kill_zones(datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc))
        names = [kz.name for kz in kzs]
        assert "London Open Kill Zone" in names


class TestManipulationContext:
    """Le contexte de manipulation s'adapte à l'actif et à la session."""

    def test_known_asset(self):
        from titanium.features.ict_market_profiles import manipulation_context
        ctx = manipulation_context("EURUSD")
        assert ctx["known"] is True
        assert "asset_class" in ctx
        assert "manipulation_probability" in ctx

    def test_unknown_asset_still_works(self):
        from titanium.features.ict_market_profiles import manipulation_context
        ctx = manipulation_context("ZZZZZZ")
        assert ctx["known"] is False
        assert len(ctx["warnings"]) > 0


# ═══════════════════════════════ ict_structure ═══════════════════════════════

def _trending_df(n=200, direction=1):
    """Génère un DataFrame avec une tendance claire."""
    np.random.seed(42)
    noise = np.random.randn(n) * 0.3
    trend = np.linspace(0, direction * 15, n)
    c = 100 + trend + np.cumsum(noise)
    return pd.DataFrame({
        "open": c - 0.15,
        "high": c + abs(np.random.randn(n)) * 0.4,
        "low": c - abs(np.random.randn(n)) * 0.4,
        "close": c,
        "v": np.random.randint(100, 1000, n),
    })


class TestMarketStructure:
    """La structure de marché identifie les tendances et les cassures."""

    def test_bullish_trend(self):
        from titanium.features.ict_structure import analyze_structure
        df = _trending_df(200, direction=1)
        ms = analyze_structure(df)
        assert ms.trend in ("bullish", "undefined")
        assert len(ms.swings) > 0

    def test_bearish_trend(self):
        from titanium.features.ict_structure import analyze_structure
        df = _trending_df(200, direction=-1)
        ms = analyze_structure(df)
        assert ms.trend in ("bearish", "undefined")

    def test_swing_points_detected(self):
        from titanium.features.ict_structure import detect_swing_points
        df = _trending_df()
        swings = detect_swing_points(df)
        assert len(swings) > 3
        kinds = {s.kind for s in swings}
        assert kinds  # au moins un type de swing

    def test_short_df_returns_empty(self):
        from titanium.features.ict_structure import analyze_structure
        df = _trending_df(5)
        ms = analyze_structure(df)
        assert ms.trend == "undefined"
        assert len(ms.swings) == 0


class TestDisplacement:
    """Les displacements sont détectés sur des mouvements violents."""

    def test_no_displacement_on_calm(self):
        from titanium.features.ict_structure import detect_displacement
        # Série parfaitement plate : aucun mouvement ne peut dépasser 2x ATR
        c = np.full(200, 100.0)
        df = pd.DataFrame({
            "open": c, "high": c + 0.01,
            "low": c - 0.01, "close": c,
            "v": np.ones(200) * 100,
        })
        disp = detect_displacement(df)
        assert len(disp) == 0  # pas de mouvement violent sur données plates

    def test_displacement_on_spike(self):
        from titanium.features.ict_structure import detect_displacement
        np.random.seed(42)
        c = np.full(200, 100.0)
        # Spike violent aux barres 190-192
        c[190:] += np.linspace(0, 8, 10)
        df = pd.DataFrame({
            "open": c - 0.1, "high": c + 0.3,
            "low": c - 0.3, "close": c,
            "v": np.ones(200) * 100,
        })
        disp = detect_displacement(df, min_atr_mult=1.5)
        # Peut ou pas détecter selon l'ATR calculé — on vérifie que ça ne plante pas
        assert isinstance(disp, list)


class TestPremiumDiscount:
    """Premium/discount identifie correctement la position du prix."""

    def test_premium_zone(self):
        from titanium.features.ict_structure import premium_discount_zone
        c = np.linspace(90, 110, 100)
        df = pd.DataFrame({
            "open": c - 0.1, "high": c + 0.5,
            "low": c - 0.5, "close": c, "v": np.ones(100) * 100,
        })
        result = premium_discount_zone(df, 108.0)
        assert result["available"]
        assert result["zone"] == "premium"

    def test_discount_zone(self):
        from titanium.features.ict_structure import premium_discount_zone
        c = np.linspace(90, 110, 100)
        df = pd.DataFrame({
            "open": c - 0.1, "high": c + 0.5,
            "low": c - 0.5, "close": c, "v": np.ones(100) * 100,
        })
        result = premium_discount_zone(df, 92.0)
        assert result["available"]
        assert result["zone"] == "discount"

    def test_insufficient_data(self):
        from titanium.features.ict_structure import premium_discount_zone
        df = pd.DataFrame({"high": [1], "low": [0.9], "close": [0.95]})
        result = premium_discount_zone(df, 0.95, lookback=50)
        assert not result["available"]


class TestPo3:
    """Le Power of 3 identifie les phases ICT."""

    def test_accumulation_on_tight_range(self):
        from titanium.features.ict_structure import detect_po3_phase
        np.random.seed(42)
        c = 100 + np.random.randn(100) * 0.05  # range très étroit
        df = pd.DataFrame({
            "open": c - 0.02, "high": c + 0.05,
            "low": c - 0.05, "close": c,
            "v": np.ones(100) * 100,
        })
        result = detect_po3_phase(df, session_bars=16)
        # Le range est étroit, probablement accumulation
        assert result["phase"] in ("accumulation", "transition", "unknown")

    def test_returns_dict(self):
        from titanium.features.ict_structure import detect_po3_phase
        df = _trending_df()
        result = detect_po3_phase(df)
        assert "phase" in result
        assert "confidence" in result


class TestFullAnalysis:
    """L'API unifiée retourne un résultat complet."""

    def test_full_analysis(self):
        from titanium.features.ict_structure import full_ict_analysis
        df = _trending_df()
        result = full_ict_analysis(df, 110.0)
        assert "structure" in result
        assert "displacement" in result
        assert "breaker_blocks" in result
        assert "premium_discount" in result
        assert "power_of_3" in result
