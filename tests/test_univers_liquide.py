"""Filtre de liquidité FX — recalibrage du 17/08/2026.

Le journal de la démo 10055401 (128 trades clos du 10 au 17/08) attribue
−21.3 R des −29.3 R de perte aux croisements et exotiques entrés dans
l'univers quand le balayage est passé au catalogue complet. Ces tests
verrouillent le filtre qui les écarte, et le fait qu'il ne touche à RIEN
d'autre.
"""

from titanium.edge import FX_NEGOCIABLES, est_paire_fx, fx_illiquide


def test_majeures_restent_negociables():
    for sym in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY"):
        assert sym in FX_NEGOCIABLES
        assert not fx_illiquide(sym)


def test_suffixe_courtier_ignore():
    assert not fx_illiquide("EURUSD.fs")
    assert fx_illiquide("USDZAR.fs")


def test_croisements_et_exotiques_ecartes():
    # Les six symboles les plus coûteux du journal du 10-17/08/2026.
    for sym in ("AUDCAD", "USDZAR", "SGDJPY", "EURCAD", "EURHUF", "CHFSEK"):
        assert est_paire_fx(sym)
        assert fx_illiquide(sym)


def test_hors_fx_intouche():
    for sym in ("XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "US500", "USTECH",
                "UKOIL", "JPN225", "NAS100.fs"):
        assert not fx_illiquide(sym)
