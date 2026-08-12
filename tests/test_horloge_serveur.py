"""L'horloge du serveur MT5 n'est pas UTC — et le journal doit l'ignorer.

Constat du 12/08/2026 sur le compte DEMO Axi (UTC+3) : `deal.time` etiquete
"+00:00" a produit 35 clotures datees trois heures dans le futur, et des durees
de detention gonflees d'autant (7 minutes reelles journalisees 187). Une mesure
de temps de detention, une attribution de session ou un alignement
walk-forward faits sur ces horodatages sont faux sans qu'aucune erreur
n'apparaisse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from titanium.data.mt5_vendor import decalage_serveur, heure_serveur_en_utc


class FauxMt5:
    """Terminal minimal : chaque symbole publie un tick a une heure donnee."""

    def __init__(self, ticks: dict[str, float]):
        self._ticks = ticks

    def symbol_info_tick(self, symbole):
        if symbole not in self._ticks:
            return None
        return SimpleNamespace(time=self._ticks[symbole])


def _maintenant() -> float:
    return datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).timestamp()


def test_decalage_positif_est_mesure():
    mt5 = FauxMt5({"EURUSD": _maintenant() + 3 * 3600})
    assert decalage_serveur(mt5, ("EURUSD",), maintenant=_maintenant()) == 3 * 3600


def test_un_symbole_endormi_ne_masque_pas_le_decalage():
    """Marche ferme = tick vieux. Le maximum doit l'emporter sur la moyenne."""
    mt5 = FauxMt5({
        "EURUSD": _maintenant() + 3 * 3600,     # ouvert, tick frais
        "SPA35": _maintenant() - 6 * 3600,      # ferme depuis des heures
    })
    assert decalage_serveur(
        mt5, ("SPA35", "EURUSD"), maintenant=_maintenant()) == 3 * 3600


def test_absence_de_tick_ne_fabrique_aucun_decalage():
    mt5 = FauxMt5({})
    assert decalage_serveur(mt5, ("EURUSD",), maintenant=_maintenant()) == 0


def test_tick_absurde_est_refuse():
    mt5 = FauxMt5({"EURUSD": _maintenant() + 40 * 3600})
    assert decalage_serveur(mt5, ("EURUSD",), maintenant=_maintenant()) == 0


def test_latence_de_quelques_secondes_ne_devient_pas_un_decalage():
    mt5 = FauxMt5({"EURUSD": _maintenant() - 8})
    assert decalage_serveur(mt5, ("EURUSD",), maintenant=_maintenant()) == 0


def test_conversion_ramene_un_horodatage_serveur_en_utc():
    epoch_serveur = _maintenant() + 3 * 3600     # ce que MT5 renvoie a 12:00 UTC
    iso = heure_serveur_en_utc(epoch_serveur, 3 * 3600)
    assert iso == "2026-08-12T12:00:00+00:00"


def test_decalage_nul_laisse_l_horodatage_intact():
    epoch = _maintenant()
    assert heure_serveur_en_utc(epoch, 0) == "2026-08-12T12:00:00+00:00"


def test_les_barres_sont_publiees_en_vrai_utc(monkeypatch):
    """Regression 12/08/2026 : index en avance de 3 h sur ce courtier.

    Croiser des barres etiquetees UTC mais datees en heure serveur avec un
    journal en vrai UTC produit des fenetres decalees. Un rejeu des trades
    clos ecartait 12 trades sur 37 faute d'intersection.
    """
    import numpy as np

    import titanium.data.mt5_vendor as vendor
    from contextlib import contextmanager

    debut_serveur = int(datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc).timestamp())
    decalage = 3 * 3600

    class Terminal:
        TIMEFRAME_M15 = 15

        def symbol_info_tick(self, symbole):
            return SimpleNamespace(
                time=int(datetime.now(timezone.utc).timestamp()) + decalage)

        def copy_rates_from_pos(self, symbole, tf, start, count):
            return np.array(
                [(debut_serveur + 900 * i, 1.0, 1.1, 0.9, 1.05, 10, 2, 0)
                 for i in range(count)],
                dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"),
                       ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
                       ("spread", "i4"), ("real_volume", "i8")])

    @contextmanager
    def session():
        yield Terminal()

    monkeypatch.setattr(vendor, "mt5_session", session)
    monkeypatch.setattr(vendor, "ensure_symbol", lambda s: None)
    vendor.reinitialiser_horloge_serveur()

    df = vendor.get_rates("EURUSD", "M15", 3, closed_only=False)
    assert str(df.index[0]) == "2026-08-12 15:00:00+00:00"     # 18:00 serveur
    vendor.reinitialiser_horloge_serveur()


def test_l_horloge_n_est_pas_remesuree_a_chaque_barre(monkeypatch):
    """Une mesure par barre lue paierait un appel MT5 sur le chemin critique."""
    import titanium.data.mt5_vendor as vendor
    from contextlib import contextmanager

    appels = {"n": 0}

    class Terminal:
        def symbol_info_tick(self, symbole):
            appels["n"] += 1
            return SimpleNamespace(
                time=int(datetime.now(timezone.utc).timestamp()) + 3 * 3600)

    @contextmanager
    def session():
        yield Terminal()

    monkeypatch.setattr(vendor, "mt5_session", session)
    vendor.reinitialiser_horloge_serveur()
    assert vendor.decalage_serveur_cache(("EURUSD",)) == 3 * 3600
    premier = appels["n"]
    for _ in range(5):
        vendor.decalage_serveur_cache(("EURUSD",))
    assert appels["n"] == premier
    vendor.reinitialiser_horloge_serveur()


def test_une_horloge_illisible_laisse_les_barres_intactes(monkeypatch):
    """Terminal muet : mieux vaut un index non corrige qu'un index invente."""
    import titanium.data.mt5_vendor as vendor
    from contextlib import contextmanager

    @contextmanager
    def session():
        raise RuntimeError("terminal absent")
        yield  # pragma: no cover

    monkeypatch.setattr(vendor, "mt5_session", session)
    vendor.reinitialiser_horloge_serveur()
    assert vendor.decalage_serveur_cache(("EURUSD",)) == 0
    vendor.reinitialiser_horloge_serveur()
