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

    assert len(appels) == 2
    assert appels[0][3] == {
        "fraicheur_max_s": 7200,
        "ratio_reconstruit_max": 0.05,
        "tolerance_future_s": 15,
        "maintenant_utc": "2026-08-21T00:00:00Z",
    }
    assert appels[1][3] == appels[0][3]
    assert sortie["qualite_archive"]["ltf"]["timeframe"] == "M15"
    assert sortie["qualite_archive"]["htf"]["timeframe"] == "H4"
    assert sortie["qualite_archive"]["seuils"] == {
        "fraicheur_max_s": 7200.0,
        "ratio_reconstruit_max": 0.05,
        "tolerance_future_s": 15.0,
    }
