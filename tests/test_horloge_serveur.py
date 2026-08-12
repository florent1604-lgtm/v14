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
