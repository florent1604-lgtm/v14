"""La crypto cote en continu — recalibrage du 17/08/2026.

Constat mesuré (`results/shadow_prod.ndjson`, 10→17/08/2026) : 3391 setups
ENTER uniques sur la semaine, **zéro** les 15 et 16/08. Le blocage week-end,
écrit pour des marchés qui ferment (portage du vendredi soir, gap du dimanche,
stop non gardé pendant 48 h), éteignait aussi le seul marché ouvert : la
crypto. Elle n'a obtenu que 4 ordres pour 189 ENTER sur la période.
"""

from datetime import datetime, timezone

import pandas as pd

from titanium.features.builder import _weekend_block, build_feats

SAMEDI = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
VENDREDI_SOIR = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
MARDI = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _bougies(n=200, freq="15min"):
    idx = pd.date_range("2026-08-01", periods=n, freq=freq, tz="UTC")
    base = pd.Series(range(n), dtype=float) + 100.0
    return pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                         "close": base, "volume": 1000.0}, index=idx)


class TestBlocageWeekEnd:
    def test_marche_qui_ferme_reste_bloque(self):
        assert _weekend_block(SAMEDI) is True
        assert _weekend_block(VENDREDI_SOIR) is True
        assert _weekend_block(MARDI) is False

    def test_marche_continu_jamais_bloque(self):
        for instant in (SAMEDI, VENDREDI_SOIR, MARDI):
            assert _weekend_block(instant, marche_continu=True) is False

    def test_defaut_inchange(self):
        """Aucun appelant existant ne change de comportement."""
        assert _weekend_block(SAMEDI, False) is True


class TestPropagationDansLesFeatures:
    def test_features_crypto_ne_portent_pas_le_blocage(self):
        f = build_feats(_bougies(), _bougies(freq="4h"), now=SAMEDI,
                        marche_continu=True)
        assert f["cost"]["weekend_block"] is False

    def test_features_non_crypto_portent_le_blocage(self):
        f = build_feats(_bougies(), _bougies(freq="4h"), now=SAMEDI)
        assert f["cost"]["weekend_block"] is True

    def test_donnees_insuffisantes_propagent_aussi(self):
        f = build_feats(_bougies(n=5), None, now=SAMEDI, marche_continu=True)
        assert f["data_valid"] is False
        assert f["cost"]["weekend_block"] is False


class TestCreneauxIllimites:
    def test_plafond_desarme(self):
        from tools.live_demo import MAX_POSITIONS, MAX_RISQUE_CUMULE_PCT
        assert MAX_POSITIONS == 0            # 0 = illimité
        assert MAX_RISQUE_CUMULE_PCT > 0     # le budget de risque reste le mur

    def test_la_condition_de_plafond_ne_declenche_jamais(self):
        from tools.live_demo import MAX_POSITIONS
        assert not any(MAX_POSITIONS > 0 and n >= MAX_POSITIONS
                       for n in range(0, 100))
