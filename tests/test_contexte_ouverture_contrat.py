"""Le contexte d'ouverture doit survivre a l'ajout d'un champ de stratification.

Le 12/08/2026, ``tools/live_demo.py`` a commence a passer ``candle_source`` a
``TrackedState`` qui ne l'acceptait pas. Le ``TypeError`` etait avale par le
``except`` d'observabilite de ``_memoriser_contexte_limit`` : aucune trace,
aucun refus, et **tout le contexte d'ouverture etait perdu**. Un trade clos
sans contexte est definitivement inexploitable pour la mesure d'edge.

Ces tests verrouillent le contrat entre les deux modules, dans les deux sens.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import fields
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

import live_demo as L  # noqa: E402

from titanium.edge import ClosedTrade  # noqa: E402
from titanium.execution.position_manager import TrackedState  # noqa: E402


def _cles_de_stratification() -> set[str]:
    """Les cles que ``_stratification`` construit, lues dans son code source.

    La fonction interroge MT5 et la porte de confluence : l'appeler ici
    exigerait un terminal. On lit donc le dictionnaire qu'elle retourne.
    """
    source = inspect.getsource(L._stratification)
    debut = source.index("return {")
    corps = source[debut:]
    cles = set()
    for ligne in corps.splitlines():
        nu = ligne.strip()
        if nu.startswith('"') and '":' in nu:
            cles.add(nu.split('"')[1])
    return cles


def test_toute_cle_de_stratification_est_acceptee_par_trackedstate():
    champs = {f.name for f in fields(TrackedState)}
    manquantes = _cles_de_stratification() - champs
    assert not manquantes, (
        "live_demo._stratification produit des cles que TrackedState refuse : "
        f"{sorted(manquantes)}. Le TypeError serait avale et le contexte perdu."
    )


def test_trackedstate_se_construit_avec_la_stratification_complete():
    valeurs = {c: "" for c in _cles_de_stratification()}
    valeurs.update(quorum=2, support_pillars=3)
    etat = TrackedState(r=0.005, symbol="EURUSD", side=1, **valeurs)
    assert TrackedState.from_dict(etat.to_dict()) == etat


def test_le_journal_conserve_la_source_du_pilier_g5():
    champs = {f.name for f in fields(ClosedTrade)}
    assert "candle_source" in champs
    assert "candle_source" in {f.name for f in fields(TrackedState)}
