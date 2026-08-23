"""Borne de granularite reelle d'une serie de barres.

Le defaut mesure : ``results/barres/H4/DJ30.fs.parquet`` declare ``H4`` sur ses
11 881 lignes alors que les 2 729 premieres sont journalieres. Le courtier n'a
pas d'historique intraday si loin et sert la serie journaliere sous l'etiquette
demandee ; l'archive recopie l'etiquette sans la verifier.

Deux invariants sont figes ici, parce que les rater rendrait la mesure inutile
dans un sens ou dans l'autre :

- **Un jour ferie isole n'est pas une serie journaliere.** Le critere exige
  DEUX ecarts journaliers consecutifs. Avec un seul, chaque pont de mai ferait
  passer un fichier H4 pour du journalier.
- **La borne suit la DERNIERE barre grossiere, pas la premiere barre fine.**
  Les deux granularites s'entrelacent sur la zone de transition ; s'arreter a
  la premiere barre fine laisserait passer tout ce qui suit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.borne_granularite import borne  # noqa: E402

H4 = 14400
JOUR = 86400
T0 = 1_600_000_000


def _t(*blocs) -> np.ndarray:
    """Concatene des blocs ``(nombre, pas)`` en une serie d'horodatages."""
    temps, courant = [], T0
    for nombre, pas in blocs:
        for _ in range(nombre):
            temps.append(courant)
            courant += pas
    return np.asarray(temps, dtype=np.int64)


def test_serie_h4_pure_na_pas_de_borne():
    r = borne(_t((100, H4)))
    assert r["barres_grossieres"] == 0
    assert r["index_premiere_fine"] == 0
    assert r["derniere_date_grossiere"] is None


def test_prefixe_journalier_puis_h4_donne_une_borne():
    """Le cas DJ30.fs : 30 barres journalieres, puis du vrai H4."""
    r = borne(_t((30, JOUR), (100, H4)))
    assert r["barres_grossieres"] > 0
    assert 0 < r["index_premiere_fine"] <= 30
    assert r["derniere_date_grossiere"] is not None


def test_la_borne_suit_la_derniere_barre_grossiere():
    """Zone de transition : du journalier revient apres du H4."""
    serie = _t((20, JOUR), (10, H4), (20, JOUR), (60, H4))
    r = borne(serie)
    grossieres_apres = borne(serie[r["index_premiere_fine"]:])
    assert grossieres_apres["barres_grossieres"] == 0


def test_un_jour_ferie_isole_ne_declenche_rien():
    """Un seul grand ecart au milieu de H4 : pont, pas serie journaliere."""
    t = list(_t((60, H4)))
    t = [x if i < 30 else x + JOUR for i, x in enumerate(t)]
    r = borne(np.asarray(t, dtype=np.int64))
    assert r["barres_grossieres"] == 0
    assert r["index_premiere_fine"] == 0


def test_serie_journaliere_entiere_ne_laisse_rien():
    r = borne(_t((50, JOUR)))
    assert r["barres_grossieres"] > 0
    assert r["index_premiere_fine"] >= 48


def test_serie_trop_courte_est_neutre():
    for n in (0, 1, 2, 3):
        r = borne(_t((n, H4)))
        assert r["index_premiere_fine"] == 0
        assert r["barres_grossieres"] == 0
        assert r["barres_totales"] == n


def test_part_et_totaux_sont_coherents():
    r = borne(_t((30, JOUR), (70, H4)))
    assert r["barres_totales"] == 100
    assert r["part"] == round(r["barres_grossieres"] / 100, 6)
    assert 0.0 < r["part"] < 1.0


def test_ecart_de_plusieurs_jours_compte_aussi():
    """Un week-end vaut 72 h : multiple exact du jour, donc grossier."""
    r = borne(_t((10, JOUR), (5, 3 * JOUR), (10, JOUR), (60, H4)))
    assert r["barres_grossieres"] > 0
