"""Tests de l'archiveur de barres.

La passe du 19/08/2026 a produit 54 millions de barres fausses sur trois points
— heure d'ete ignoree, barres fabriquees non signalees, premiere reponse vide
prise pour une absence d'historique. Chaque test ci-dessous fige l'un de ces
defauts pour qu'il ne revienne pas silencieusement.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.archiveur_barres import (  # noqa: E402
    DECALAGE_ETE_S,
    DECALAGE_HIVER_S,
    FENETRE_UTILE,
    decalage_utc,
    marquer_reconstruites,
    premiere_utile,
)


def _ts(texte: str) -> int:
    return int(datetime.strptime(texte, "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize("horodatage_serveur, attendu", [
    ("2026-08-19 09:00:00", DECALAGE_ETE_S),
    ("2026-07-01 00:00:00", DECALAGE_ETE_S),
    ("2026-01-15 09:00:00", DECALAGE_HIVER_S),
    ("2025-12-24 12:00:00", DECALAGE_HIVER_S),
    # Heure d'ete des Etats-Unis : 8 mars 2026 et 1er novembre 2026.
    ("2026-03-06 12:00:00", DECALAGE_HIVER_S),
    ("2026-03-10 12:00:00", DECALAGE_ETE_S),
    ("2026-10-30 12:00:00", DECALAGE_ETE_S),
    ("2026-11-03 12:00:00", DECALAGE_HIVER_S),
])
def test_decalage_suit_l_heure_d_ete_de_new_york(horodatage_serveur, attendu):
    assert decalage_utc(_ts(horodatage_serveur)) == attendu


def test_le_decalage_n_est_pas_constant_sur_l_annee():
    """Le defaut exact de la passe v1 : un seul decalage pour quatre ans."""
    ete = decalage_utc(_ts("2026-07-01 12:00:00"))
    hiver = decalage_utc(_ts("2026-01-01 12:00:00"))
    assert ete - hiver == 3600


def test_ouverture_hebdomadaire_retombe_a_l_heure_reelle():
    """Le FX ouvre a 21:00 UTC en ete et 22:00 UTC en hiver.

    En heure serveur, l'ouverture est toujours lundi 00:00. C'est le controle
    qui a revele que l'archive v1 datait les semaines d'hiver a 21:00.
    """
    for texte, heure_utc_attendue in [("2026-07-06 00:00:00", 21),
                                      ("2026-01-05 00:00:00", 22)]:
        serveur = _ts(texte)
        utc = datetime.fromtimestamp(serveur - decalage_utc(serveur),
                                     tz=timezone.utc)
        assert utc.hour == heure_utc_attendue


def test_barre_plate_sans_volume_est_reconstruite():
    df = pd.DataFrame({
        "high": [1.0, 1.0, 1.5],
        "low": [1.0, 1.0, 1.2],
        "tick_volume": [1, 40, 3],
    })
    assert list(marquer_reconstruites(df)) == [True, False, False]


def test_barre_plate_avec_volume_reel_n_est_pas_marquee():
    """Un exotique illiquide peut coter un seul prix sur une minute vraie."""
    df = pd.DataFrame({"high": [2.0], "low": [2.0], "tick_volume": [37]})
    assert list(marquer_reconstruites(df)) == [False]


def test_borne_utile_saute_le_prefixe_fabrique():
    reconstruit = np.array([True] * 300 + [False] * 1000)
    assert premiere_utile(reconstruit) == 300


def test_borne_utile_vaut_zero_sur_une_serie_propre():
    assert premiere_utile(np.zeros(500, dtype=bool)) == 0


def test_borne_utile_rejette_une_serie_entierement_fabriquee():
    reconstruit = np.ones(500, dtype=bool)
    assert premiere_utile(reconstruit) == 500


def test_borne_utile_tolere_un_trou_isole():
    """Une barre fabriquee tous les cent points ne disqualifie pas la serie."""
    reconstruit = np.zeros(1000, dtype=bool)
    reconstruit[50::FENETRE_UTILE] = True
    assert premiere_utile(reconstruit) == 0


def test_borne_ne_tombe_jamais_sur_une_barre_fabriquee():
    """Tolerer une barre fabriquee dans la fenetre, oui ; commencer dessus, non."""
    reconstruit = np.zeros(500, dtype=bool)
    reconstruit[0] = True
    assert premiere_utile(reconstruit) == 1
