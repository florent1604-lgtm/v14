"""Tests de la structure de correlation de l'univers.

Deux erreurs sont faciles a commettre ici et couteuses a laisser passer :
prendre une correlation contemporaine pour un signal d'avance, et laisser un
regroupement par chaine coller ensemble deux actifs sans rapport.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.correlations_univers import (  # noqa: E402
    avance_retard,
    blocs_correlation,
    paires_redondantes,
    representant,
)


def _univers(n: int = 3000, graine: int = 7):
    """Deux familles independantes, plus un suiveur retarde d'une barre."""
    rng = np.random.default_rng(graine)
    dollar = rng.normal(size=n)
    petrole = rng.normal(size=n)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "EURUSD": dollar + 0.10 * rng.normal(size=n),
        "GBPUSD": 0.95 * dollar + 0.15 * rng.normal(size=n),
        "USOIL": petrole + 0.10 * rng.normal(size=n),
        "UKOIL": 0.97 * petrole + 0.12 * rng.normal(size=n),
        # Suiveur : la valeur d'aujourd'hui est celle du petrole d'hier.
        "SUIVEUR": np.concatenate(([0.0], petrole[:-1])) + 0.05 * rng.normal(size=n),
    }, index=idx)


def test_les_blocs_separent_deux_familles_independantes():
    r = _univers()
    corr = r.corr()
    blocs = blocs_correlation(corr, seuil=0.7)
    par_symbole = {s: i for i, b in enumerate(blocs) for s in b}
    assert par_symbole["EURUSD"] == par_symbole["GBPUSD"]
    assert par_symbole["USOIL"] == par_symbole["UKOIL"]
    assert par_symbole["EURUSD"] != par_symbole["USOIL"]


def test_le_suiveur_retarde_ne_rejoint_pas_le_bloc_du_petrole():
    """Il partage la meme information, mais pas au meme instant."""
    r = _univers()
    corr = r.corr()
    blocs = blocs_correlation(corr, seuil=0.7)
    par_symbole = {s: i for i, b in enumerate(blocs) for s in b}
    assert par_symbole["SUIVEUR"] != par_symbole["USOIL"]


def test_le_representant_est_le_plus_central():
    r = _univers()
    corr = r.corr()
    bloc = ["EURUSD", "GBPUSD"]
    assert representant(bloc, corr) in bloc
    assert representant(["USOIL"], corr) == "USOIL"


def test_paires_redondantes_reperent_le_meme_actif_deux_fois():
    r = _univers()
    paires = paires_redondantes(r.corr(), seuil=0.9)
    couples = {tuple(sorted((a, b))) for a, b, _ in paires}
    assert ("UKOIL", "USOIL") in couples
    assert ("EURUSD", "USOIL") not in couples


def test_avance_retard_retrouve_le_decalage_injecte():
    r = _univers()
    sortie = avance_retard(r, [("USOIL", "SUIVEUR")], decalage_max=3)
    assert sortie, "aucune paire mesuree"
    d = sortie[0]
    assert d["meilleur_decalage"] == 1
    assert abs(d["correlation_meilleure"]) > abs(d["correlation_contemporaine"])
    assert d["gain_sur_contemporain"] > 0


def test_avance_retard_ne_voit_pas_d_avance_quand_il_n_y_en_a_pas():
    """Deux expressions du meme mouvement bougent ENSEMBLE : decalage nul."""
    r = _univers()
    sortie = avance_retard(r, [("EURUSD", "GBPUSD")], decalage_max=3)
    assert sortie[0]["meilleur_decalage"] == 0


def test_serie_trop_courte_est_ignoree():
    r = _univers(n=100)
    assert avance_retard(r, [("USOIL", "SUIVEUR")]) == []
