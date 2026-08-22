"""Le rejeu doit utiliser l'horloge des barres, jamais l'horloge murale."""

from types import SimpleNamespace

import pandas as pd

from titanium import backtest


def _barres() -> pd.DataFrame:
    index = pd.date_range("2026-08-22T00:00:00Z", periods=12, freq="15min")
    return pd.DataFrame({
        "open": [100.0] * len(index),
        "high": [101.0] * len(index),
        "low": [99.0] * len(index),
        "close": [100.0] * len(index),
        "volume": [10.0] * len(index),
    }, index=index)


def test_rejouer_transmet_cloture_causale_et_marche_continu(monkeypatch):
    ltf = _barres()
    htf = _barres()
    appels_features = []
    appels_porte = []

    def faux_build(*_args, **kwargs):
        appels_features.append(kwargs)
        return {"_trace": {"decided_at": kwargs["now"].isoformat()}}

    def fausse_porte(_feats, **kwargs):
        appels_porte.append(kwargs)
        return SimpleNamespace(entered=False)

    import titanium.edge as edge
    import titanium.features.builder as builder
    import titanium.gates.confluence_gate as gate

    monkeypatch.setattr(edge, "asset_class_of", lambda _s: "crypto")
    monkeypatch.setattr(builder, "build_feats", faux_build)
    monkeypatch.setattr(gate, "evaluate", fausse_porte)

    resultat = backtest.rejouer("BTCUSD", ltf, htf, amorcage=1)

    assert resultat.erreurs == 0
    assert appels_features
    assert appels_features[0]["now"] == ltf.index[1] + pd.Timedelta(minutes=15)
    assert all(a["marche_continu"] is True for a in appels_features)
    assert [a["decided_at"] for a in appels_porte] == [
        a["now"] for a in appels_features
    ]


def test_rejouer_ne_marque_pas_le_fx_comme_marche_continu(monkeypatch):
    ltf = _barres()
    appels = []

    import titanium.edge as edge
    import titanium.features.builder as builder
    import titanium.gates.confluence_gate as gate

    monkeypatch.setattr(edge, "asset_class_of", lambda _s: "fx")
    monkeypatch.setattr(
        builder, "build_feats",
        lambda *_a, **k: appels.append(k) or {"_trace": {}},
    )
    monkeypatch.setattr(
        gate, "evaluate", lambda *_a, **_k: SimpleNamespace(entered=False))

    backtest.rejouer("EURUSD", ltf, ltf, amorcage=1)

    assert appels
    assert all(a["marche_continu"] is False for a in appels)

