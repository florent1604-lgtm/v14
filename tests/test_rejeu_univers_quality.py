"""Propagation des portes qualite du chargeur vers le rejeu universel."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from tools import rejeu_univers as ru


def test_rejeu_propage_les_portes_et_rapporte_la_qualite(monkeypatch):
    appels = []

    def faux_charger(symbole, timeframe, count=None, **kwargs):
        appels.append((symbole, timeframe, count, kwargs))
        index = pd.date_range("2026-08-20", periods=4, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "open": [1.0] * 4,
            "high": [1.1] * 4,
            "low": [0.9] * 4,
            "close": [1.0] * 4,
            "spread": [10.0] * 4,
        }, index=index)
        df.attrs["archive_quality"] = {
            "timeframe": timeframe,
            "ratio_reconstruit": 0.01,
            "ohlc_invalides": 0,
        }
        return df

    def faux_rejouer(*args, **kwargs):
        return SimpleNamespace(trades=[], n_enter=0, barres_evaluees=4, erreurs=0)

    monkeypatch.setattr(ru, "charger_barres", faux_charger)
    monkeypatch.setattr(ru, "specifications", lambda: {})
    import titanium.backtest as backtest
    monkeypatch.setattr(backtest, "rejouer", faux_rejouer)

    sortie = ru.rejouer_symbole(
        "TESTUSD", "M15", "H4", 100, 1,
        fraicheur_max_s=7200,
        ratio_reconstruit_max=0.05,
        tolerance_future_s=15,
        maintenant_utc="2026-08-21T00:00:00Z",
    )

    portes = {
        "fraicheur_max_s": 7200,
        "ratio_reconstruit_max": 0.05,
        "tolerance_future_s": 15,
        "maintenant_utc": "2026-08-21T00:00:00Z",
    }
    assert len(appels) == 2
    assert appels[0][3] == portes

    # Le HTF recoit les memes portes, PLUS une borne basse. Il n'a pas de
    # `count` naturel : sa portee utile est celle du LTF qu'il accompagne, pas
    # la profondeur de son fichier. Sans cette borne, il etait valide sur toute
    # son histoire — et le 22/08/2026 une barre DJ30.fs de 2009 a fait echouer
    # un rejeu de 149 symboles a 32, alors que le M15 du meme symbole commence
    # en 2022 et ne pouvait pas la lire.
    htf_kwargs = dict(appels[1][3])
    borne = htf_kwargs.pop("depuis_utc")
    assert htf_kwargs == portes
    assert borne == pd.Timestamp("2026-08-20", tz="UTC") - pd.Timedelta(days=1826)
    assert sortie["qualite_archive"]["ltf"]["timeframe"] == "M15"
    assert sortie["qualite_archive"]["htf"]["timeframe"] == "H4"
    assert sortie["qualite_archive"]["seuils"] == {
        "fraicheur_max_s": 7200.0,
        "ratio_reconstruit_max": 0.05,
        "tolerance_future_s": 15.0,
    }
