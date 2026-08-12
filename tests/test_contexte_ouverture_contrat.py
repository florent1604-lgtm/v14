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


def _kwargs_trackedstate_dans_live_demo() -> dict[str, set[str]]:
    """Tous les mots-cles passes a ``TrackedState(...)`` par live_demo.

    Lu dans l'AST plutot que dans le texte : un argument ajoute sur une ligne
    repliee ou renomme reste detecte.
    """
    import ast

    arbre = ast.parse((RACINE / "tools" / "live_demo.py").read_text(encoding="utf-8"))
    trouves: dict[str, set[str]] = {}
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call):
            continue
        cible = noeud.func
        nom = getattr(cible, "id", None) or getattr(cible, "attr", None)
        if nom != "TrackedState":
            continue
        fonction = "?"
        for parent in ast.walk(arbre):
            if isinstance(parent, ast.FunctionDef) and noeud in ast.walk(parent):
                fonction = parent.name
        trouves.setdefault(fonction, set()).update(
            kw.arg for kw in noeud.keywords if kw.arg)
    return trouves


def test_tous_les_arguments_passes_a_trackedstate_existent():
    """Le 12/08/2026, deux fois de suite, live_demo a passe un mot-cle inconnu.

    La premiere fois ``candle_source``, la seconde les champs ``limit_*`` sur
    le mauvais chemin. Dans les deux cas le ``TypeError`` etait avale par un
    ``except`` d'observabilite : aucune trace, et le contexte perdu.
    """
    champs = {f.name for f in fields(TrackedState)}
    for fonction, kwargs in _kwargs_trackedstate_dans_live_demo().items():
        inconnus = kwargs - champs - {"r"}
        assert not inconnus, (
            f"live_demo.{fonction} passe a TrackedState des mots-cles "
            f"inexistants : {sorted(inconnus)}"
        )


def test_la_provenance_limite_reste_sur_le_chemin_pending():
    """Un ordre au marche n'a ni prix planifie ni economie visee.

    Les renseigner depuis ``_attacher_contexte`` (chemin marche) a fait croire
    a une provenance limite sur des positions qui n'en avaient pas, et a laisse
    les vrais fills sans provenance. Corrige par Codex le 12/08/2026.
    """
    par_fonction = _kwargs_trackedstate_dans_live_demo()
    limite = {k for k in par_fonction.get("_memoriser_contexte_limit", set())
              if k.startswith("limit_")}
    marche = {k for k in par_fonction.get("_attacher_contexte", set())
              if k.startswith("limit_")}
    assert limite, "le chemin pending doit porter la provenance de la limite"
    assert not marche, (
        f"le chemin marche ne doit rien affirmer sur les limites : {sorted(marche)}"
    )


def test_le_journal_declare_sa_convention_d_horloge():
    """Sans marqueur, impossible de savoir si `closed_at` est UTC ou serveur.

    Les 37 premieres lignes du journal live portent l'heure du serveur
    etiquetee "+00:00". Les croiser avec des barres de marche produit des
    fenetres decalees de trois heures ; un rejeu des trades clos ecartait
    ainsi 10 trades sur 37 et surestimait l'esperance de 0,32 R.
    """
    champs = {f.name for f in fields(ClosedTrade)}
    assert "horloge" in champs
    assert ClosedTrade(context="x", pnl_r=0.0).horloge == ""    # ancien = inconnu
