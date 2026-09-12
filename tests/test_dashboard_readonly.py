"""Garde-fous structurels du tableau de bord temps réel.

Le tableau de bord est un lecteur. Il n'ouvre pas d'ordre, n'en annule pas,
n'arme rien et ne construit jamais d'exécuteur. Une phrase de README ne tient
pas six mois ; ces tests, si.

Le point sensible mesuré par l'audit : ``ExecutionPolicy.from_config`` lit
``DEFAULT_CONFIG``. Avec le même ``.env`` que la boucle, un process de
tableau de bord qui l'appellerait hériterait de ``exec_enabled=True`` et
disposerait d'une policy armée. Il n'en a aucun besoin pour afficher :
``state.wall()`` lui rend déjà l'état des trois verrous, sous forme de
dictionnaire inerte.

On analyse l'AST plutôt que le texte : la docstring du module cite ces noms
pour expliquer l'interdit, et un ``grep`` naïf s'y prendrait les pieds.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from titanium.web import live_engine

pytestmark = pytest.mark.unit

#: Tout ce qui, appelé depuis le process du tableau de bord, ouvrirait un
#: chemin vers le courtier ou fabriquerait une autorité d'exécution.
INTERDITS = frozenset({
    "order_send", "order_check", "order_calc_margin",
    "ExecutionPolicy", "assert_can_trade", "execute_recorded",
    "MetaTrader5", "mt5_lock", "mt5_session",
    "TRADE_ACTION_DEAL", "TRADE_ACTION_SLTP", "TRADE_ACTION_REMOVE",
})

MODULE = Path(live_engine.__file__)


def _symboles(chemin: Path) -> set[str]:
    """Noms et attributs réellement utilisés par le code, docstrings exclues."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    return (
        {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
        | {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
        | {
            alias.name.split(".")[0]
            for n in ast.walk(arbre)
            if isinstance(n, ast.Import)
            for alias in n.names
        }
    )


def test_le_moteur_ne_peut_pas_agir_sur_le_courtier():
    """Aucun symbole d'exécution dans le lecteur du tableau de bord."""
    fautifs = sorted(_symboles(MODULE) & INTERDITS)
    assert not fautifs, (
        f"symboles d'exécution dans un module lecture seule : {fautifs}. "
        "Le tableau de bord doit passer par titanium.web.state, qui rend des "
        "dictionnaires inertes."
    )


def test_le_moteur_nimporte_que_state():
    """Le lecteur ne parle qu'à ``titanium.web.state``.

    Toute autre porte d'entrée dans ``titanium`` élargirait sa surface : c'est
    par là qu'un import « juste pour afficher » finit par ramener une policy
    armée.
    """
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    modules = {
        n.module
        for n in ast.walk(arbre)
        if isinstance(n, ast.ImportFrom) and n.module
    }
    titanium = {m for m in modules if m.startswith("titanium")}
    assert titanium <= {"titanium.web"}, (
        f"imports titanium non autorisés dans le lecteur : {sorted(titanium)}"
    )


def test_les_positions_sont_mises_en_cache():
    """``state.positions()`` prend le verrou MT5 sans cache : le lecteur doit
    en poser un.

    Sans TTL, un front à 2 s prendrait ``mt5_lock`` trente fois par minute —
    le verrou dont la boucle armée a besoin pour passer ses ordres. Le
    tableau de bord dégraderait la boucle qu'il observe, et le défaut serait
    invisible dans l'interface.
    """
    assert live_engine.TTL_POSITIONS >= 5.0, (
        "TTL positions trop court : pression inacceptable sur le verrou MT5"
    )


def test_un_seul_lecteur_par_process():
    """Deux lecteurs doubleraient la pression sur le verrou sans rien apporter."""
    assert live_engine.get_engine() is live_engine.get_engine()


def test_une_donnee_vieille_est_annoncee_perimee():
    """Une donnée périmée n'est jamais présentée comme fraîche."""
    bloc = live_engine.Bloc("essai", ttl=5.0)
    assert bloc.enveloppe()["jamais_lu"] is True
    assert bloc.perime is True, "un bloc jamais lu doit être périmé, pas vide et muet"

    import time

    bloc.valeur = {"x": 1}
    bloc.lu_a = time.monotonic()
    assert bloc.frais is True
    assert bloc.enveloppe()["stale"] is False

    bloc.lu_a = time.monotonic() - (5.0 * live_engine.STALE_FACTOR + 1.0)
    assert bloc.perime is True
    assert bloc.enveloppe()["stale"] is True
