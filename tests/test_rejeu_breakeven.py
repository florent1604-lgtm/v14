"""Rejeu barre à barre du seuil de breakeven — tâche Prime 65541466.

Ce que ces tests verrouillent :

1. la fenêtre de barres se compte depuis MAINTENANT, pas sur la durée du
   trade — la régression qui avait écarté les douze plus gros gagnants ;
2. le stop se déclenche avant toute décision du moteur ;
3. l'ordre intra-barre est réellement les deux ordres, pas deux fois le même ;
4. un rejeu au seuil réel doit retomber sur le résultat réel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tools import rejeu_breakeven as rb


def _trade(**kw) -> rb.Trade:
    base = dict(
        symbol="TEST", side=1, entry=100.0, sl_initial=99.0,
        tp_initial=103.0, r_unit=1.0,
        ts_open=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
        ts_exit=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        pnl_r=0.0, mae_r=0.0, mfe_r=0.0, exit_reason="", context="")
    base.update(kw)
    return rb.Trade(**base)


def _barres(ohlc: list[tuple[float, float, float, float]],
            debut: datetime | None = None) -> pd.DataFrame:
    debut = debut or datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
    idx = pd.date_range(debut, periods=len(ohlc), freq="15min", tz="UTC")
    return pd.DataFrame(
        {"open": [x[0] for x in ohlc], "high": [x[1] for x in ohlc],
         "low": [x[2] for x in ohlc], "close": [x[3] for x in ohlc],
         "volume": np.full(len(ohlc), 1.0)}, index=idx)


# ═══════════════════════════════════════════════════════════════════════════════
# La régression qui inversait la conclusion
# ═══════════════════════════════════════════════════════════════════════════════

class TestFenetreDeBarres:
    """La première version demandait un nombre de barres proportionnel à la
    DURÉE du trade. Un trade court d'avant-hier était alors demandé sur 120
    barres — trente heures, qui ne remontaient pas jusqu'à lui. Douze trades
    sur trente-sept étaient écartés, et c'étaient les plus gros gagnants : le
    biais inversait la conclusion de l'outil."""

    def test_trade_court_mais_ancien_est_rejouable(self):
        maintenant = datetime.now(timezone.utc)
        ouvert = maintenant - timedelta(days=2)
        t = _trade(ts_open=ouvert, ts_exit=ouvert + timedelta(minutes=30))
        demande = {}

        def lecteur(sym, tf, n):
            demande["n"] = n
            # 3 jours de barres M15, comme MT5 en rendrait.
            return _barres([(100, 101, 99, 100)] * n,
                           debut=maintenant - timedelta(minutes=15 * n))

        assert rb.barres_du_trade(t, lecteur) is not None
        # Deux jours de M15 = ~192 barres. Une demande de 120 ne suffirait pas.
        assert demande["n"] > 192

    def test_fenetre_ne_commencant_pas_avant_l_ouverture_est_refusee(self):
        """Sans les premières barres, on ne sait pas si le breakeven s'arme."""
        t = _trade()
        # Barres commençant APRÈS l'ouverture du trade.
        tard = t.ts_open + timedelta(hours=1)
        lecteur = lambda *a: _barres([(100, 101, 99, 100)] * 50, debut=tard)  # noqa: E731
        assert rb.barres_du_trade(t, lecteur) is None

    def test_lecteur_qui_leve_rend_none(self):
        def lecteur(*_a):
            raise RuntimeError("MT5 muet")

        assert rb.barres_du_trade(_trade(), lecteur) is None


# ═══════════════════════════════════════════════════════════════════════════════
# Ordre intra-barre
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheminIntra:
    def test_long_ordre_defavorable_visite_le_bas_en_premier(self):
        bar = {"open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0}
        assert rb._chemin_intra(bar, 1, True) == [100.0, 98.0, 102.0, 101.0]

    def test_long_ordre_favorable_visite_le_haut_en_premier(self):
        bar = {"open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0}
        assert rb._chemin_intra(bar, 1, False) == [100.0, 102.0, 98.0, 101.0]

    def test_short_inverse_les_roles(self):
        """Pour un short, l'extrême ADVERSE est le haut, pas le bas."""
        bar = {"open": 100.0, "high": 102.0, "low": 98.0, "close": 99.0}
        assert rb._chemin_intra(bar, -1, True) == [100.0, 102.0, 98.0, 99.0]

    def test_les_deux_ordres_different(self):
        bar = {"open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0}
        assert rb._chemin_intra(bar, 1, True) != rb._chemin_intra(bar, 1, False)


# ═══════════════════════════════════════════════════════════════════════════════
# Rejeu
# ═══════════════════════════════════════════════════════════════════════════════

class TestRejeu:
    def test_stop_touche_sort_a_moins_un_r(self):
        t = _trade()
        b = _barres([(100, 100.2, 98.5, 99.5)])
        r = rb.rejouer(t, b, 0.8, adverse_dabord=True)
        assert r["sortie"] == "stop"
        assert r["pnl_r"] == pytest.approx(-1.0)

    def test_tp_touche_sort_au_tp(self):
        t = _trade()
        b = _barres([(100, 103.5, 99.9, 103.2)])
        r = rb.rejouer(t, b, 0.8, adverse_dabord=True)
        assert r["sortie"] == "tp"
        assert r["pnl_r"] == pytest.approx(3.0)

    def test_le_stop_prime_sur_la_decision_du_moteur(self):
        """Le courtier ne demande la permission à personne.

        Barre qui monte à +0.9R (le breakeven s'armerait) mais qui touche
        d'abord le stop : en ordre défavorable, le trade est mort avant.
        """
        t = _trade()
        b = _barres([(100, 100.9, 98.9, 100.5)])
        assert rb.rejouer(t, b, 0.8, adverse_dabord=True)["sortie"] == "stop"

    def test_breakeven_arme_puis_touche_sort_legerement_positif(self):
        t = _trade()
        b = _barres([
            (100.0, 100.9, 99.95, 100.8),   # +0.9R : le breakeven s'arme
            (100.8, 100.9, 99.90, 100.0),   # retour : le SL de breakeven prend
        ])
        r = rb.rejouer(t, b, 0.8, adverse_dabord=False)
        assert r["sortie"] == "stop"
        assert 0.0 < r["pnl_r"] < 0.2   # entrée + tampon, pas −1R

    def test_seuil_plus_bas_arme_plus_tot(self):
        """Le même trade, coupé à 0.30 mais épargné à 0.80."""
        t = _trade()
        b = _barres([
            (100.0, 100.4, 99.95, 100.3),   # +0.4R : arme à 0.30, pas à 0.80
            (100.3, 100.4, 99.90, 100.0),   # retour sous l'entrée
            (100.0, 102.5, 99.95, 102.4),   # puis ça part
        ])
        bas = rb.rejouer(t, b, 0.30, adverse_dabord=False)
        haut = rb.rejouer(t, b, 0.80, adverse_dabord=False)
        assert bas["pnl_r"] < haut["pnl_r"], "le seuil bas doit couper ce gagnant"

    def test_barres_epuisees_solde_au_dernier_cours(self):
        t = _trade(tp_initial=0.0)
        b = _barres([(100, 100.3, 99.9, 100.2)])
        r = rb.rejouer(t, b, 0.8, adverse_dabord=True)
        assert r["sortie"] == "fin_barres"
        assert r["pnl_r"] == pytest.approx(0.2)


# ═══════════════════════════════════════════════════════════════════════════════
# Chargement
# ═══════════════════════════════════════════════════════════════════════════════

def _ligne(**kw) -> str:
    """Une ligne d'excursion valide, horloge UTC sauf mention contraire."""
    import json

    d = {"symbol": "X", "side": 1, "entry": 1.0, "sl_initial": 0.9,
         "tp_initial": 1.2, "r_unit": 0.1, "pnl_r": 0.0, "horloge": "utc",
         "ts_open": "2026-08-10T08:00:00+00:00",
         "ts_exit": "2026-08-10T09:00:00+00:00"}
    d.update(kw)
    if d.get("horloge") is None:
        d.pop("horloge")
    return json.dumps(d)


class TestChargement:
    def test_fichier_absent(self, tmp_path):
        assert rb.charger(tmp_path / "rien.ndjson") == ([], 0)

    def test_r_unit_nul_ou_negatif_ecarte(self, tmp_path):
        p = tmp_path / "e.ndjson"
        p.write_text("\n".join(_ligne(r_unit=r) for r in (0.0, -1.0, 0.5)),
                     encoding="utf-8")
        trades, _ = rb.charger(p)
        assert len(trades) == 1

    def test_sortie_avant_ouverture_ecartee(self, tmp_path):
        p = tmp_path / "e.ndjson"
        p.write_text(_ligne(ts_open="2026-08-10T09:00:00+00:00",
                            ts_exit="2026-08-10T08:00:00+00:00"),
                     encoding="utf-8")
        assert rb.charger(p)[0] == []

    def test_ligne_cassee_ignoree(self, tmp_path):
        p = tmp_path / "e.ndjson"
        p.write_text(f"{_ligne()}\npas du json\n{_ligne()}\n", encoding="utf-8")
        assert len(rb.charger(p)[0]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Le refus d'horloge — deux erreurs qui s'annulaient
# ═══════════════════════════════════════════════════════════════════════════════

class TestRefusHorloge:
    """Avant le 12/08/2026, journal ET barres étaient en heure serveur : les
    deux erreurs s'annulaient et le croisement paraissait juste. Depuis que
    `get_rates` publie du vrai UTC, croiser une ligne ancienne décale la
    fenêtre de trois heures — un trade stoppé peut y survivre jusqu'au
    trailing. La fidélité du rejeu passait de +0,105R à +0,32R (Prime,
    0f452c5), soit trois fois le gain qu'on prétendait mesurer."""

    def test_ligne_sans_marqueur_refusee(self, tmp_path):
        p = tmp_path / "e.ndjson"
        p.write_text(_ligne(horloge=None), encoding="utf-8")
        trades, refuses = rb.charger(p)
        assert trades == []
        assert refuses == 1

    def test_ligne_avec_marqueur_vide_refusee(self, tmp_path):
        p = tmp_path / "e.ndjson"
        p.write_text(_ligne(horloge=""), encoding="utf-8")
        assert rb.charger(p) == ([], 1)

    def test_ligne_avec_autre_horloge_refusee(self, tmp_path):
        p = tmp_path / "e.ndjson"
        p.write_text(_ligne(horloge="serveur"), encoding="utf-8")
        assert rb.charger(p) == ([], 1)

    def test_ligne_utc_acceptee(self, tmp_path):
        p = tmp_path / "e.ndjson"
        p.write_text(_ligne(horloge="utc"), encoding="utf-8")
        trades, refuses = rb.charger(p)
        assert len(trades) == 1
        assert refuses == 0

    def test_melange_ne_contamine_pas(self, tmp_path):
        """Une seule ligne saine parmi des anciennes reste exploitable seule."""
        p = tmp_path / "e.ndjson"
        p.write_text("\n".join([_ligne(horloge=None), _ligne(horloge=""),
                                _ligne(horloge="utc"), _ligne(horloge=None)]),
                     encoding="utf-8")
        trades, refuses = rb.charger(p)
        assert len(trades) == 1
        assert refuses == 3

    def test_le_refus_precede_toute_autre_validation(self, tmp_path):
        """Une ligne d'horloge inconnue ET par ailleurs invalide compte comme
        refus d'horloge : on n'examine pas ce qu'on ne sait pas dater."""
        p = tmp_path / "e.ndjson"
        p.write_text(_ligne(horloge=None, r_unit=0.0), encoding="utf-8")
        assert rb.charger(p) == ([], 1)
