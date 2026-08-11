"""Carte du code de V14 — modules, dépendances, et faiblesses structurelles.

Équivalent local de ce que GitNexus offre à V12 : une lecture du dépôt par
lui-même, en lecture seule, servant à répondre à « qu'est-ce qui dépend de
quoi » et « qu'est-ce qui n'est couvert par rien ».

Pourquoi ce n'est pas cosmétique
---------------------------------
Les pannes de V14 ont toutes la même signature : un module existe, il est
importé, ses tests passent — et pourtant rien ne circule. Le flux retour
non journalisé, `edge_ok_for` sans aucun appelant, `get_rates_cache` non
importé. Aucune n'était visible en lisant un fichier ; toutes se voient sur
un graphe.

Ce module construit ce graphe par **analyse syntaxique**, sans exécuter la
moindre ligne du dépôt. Indexer un projet en l'important reviendrait à
déclencher ses effets de bord — pour V14, à ouvrir MT5.

Ce qu'il détecte
----------------
* **orphelin** — module que personne n'importe. Soit c'est un point
  d'entrée, soit c'est du code mort qui donne une fausse impression de
  couverture.
* **non couvert** — module sans aucun test qui le nomme.
* **symbole inerte** — fonction publique définie et jamais référencée
  ailleurs. C'est exactement la signature d'`edge_ok_for`.
* **cycle** — dépendance circulaire entre modules.
"""

from __future__ import annotations

import ast
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
CACHE = RACINE / "results" / "carte_code.json"

#: Arborescences hors périmètre. `tradingagents` est le socle importé tel
#: quel : le cartographier noierait les organes de V14 sous 71 modules
#: qu'on ne modifie pas.
EXCLUS = {".venv", "__pycache__", ".git", "results", "build", "dist",
          ".pytest_cache", ".ruff_cache", "node_modules"}

#: Dossiers de V14 à cartographier, dans l'ordre de lecture souhaité.
PERIMETRE = ("titanium", "tools")


@dataclass
class Module:
    chemin: str
    paquet: str = ""
    lignes: int = 0
    fonctions: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    importe: list = field(default_factory=list)   # modules internes importés
    importe_par: list = field(default_factory=list)
    tests: int = 0
    faiblesses: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _nom_module(p: Path) -> str:
    return p.relative_to(RACINE).with_suffix("").as_posix().replace("/", ".")


def _fichiers() -> list:
    out = []
    for dossier in PERIMETRE:
        base = RACINE / dossier
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if any(part in EXCLUS for part in p.parts):
                continue
            out.append(p)
    return sorted(out)


def _analyser(p: Path) -> Module | None:
    """Lit un fichier et en extrait sa structure. Ne lève jamais."""
    try:
        src = p.read_text(encoding="utf-8")
        arbre = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None

    m = Module(chemin=p.relative_to(RACINE).as_posix(),
               paquet=_nom_module(p).split(".")[0],
               lignes=src.count("\n") + 1)

    for n in ast.walk(arbre):
        if isinstance(n, ast.FunctionDef) or isinstance(n, ast.AsyncFunctionDef):
            # Les fonctions privées ne sont pas des points d'entrée : les
            # signaler inertes produirait un bruit permanent.
            if not n.name.startswith("_"):
                m.fonctions.append(n.name)
        elif isinstance(n, ast.ClassDef):
            m.classes.append(n.name)
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module.split(".")[0] in PERIMETRE:
                m.importe.append(n.module)
                # `from titanium.web import state` importe le MODULE
                # `titanium.web.state`, pas seulement le paquet. Sans cette
                # résolution, tout module importé de cette façon passait
                # pour orphelin — et la carte criait au loup.
                for a in n.names:
                    m.importe.append(f"{n.module}.{a.name}")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in PERIMETRE:
                    m.importe.append(a.name)

    m.importe = sorted(set(m.importe))
    return m


def _cycles(graphe: dict) -> list:
    """Cycles de dépendance, par parcours en profondeur."""
    trouves, pile, vus = [], [], set()

    def marcher(n):
        if n in pile:
            trouves.append(pile[pile.index(n):] + [n])
            return
        if n in vus:
            return
        vus.add(n)
        pile.append(n)
        for v in graphe.get(n, []):
            marcher(v)
        pile.pop()

    for n in list(graphe):
        marcher(n)
    # Déduplication : un cycle A→B→A est le même que B→A→B.
    uniques, signatures = [], set()
    for c in trouves:
        sig = tuple(sorted(set(c)))
        if sig not in signatures:
            signatures.add(sig)
            uniques.append(c)
    return uniques[:12]


def indexer() -> dict:
    """Construit la carte. Coûteux — passer par `carte()` qui met en cache."""
    t0 = time.time()
    modules: dict = {}

    for p in _fichiers():
        m = _analyser(p)
        if m:
            modules[_nom_module(p)] = m

    # ── Sens inverse des dépendances.
    for nom, m in modules.items():
        for cible in m.importe:
            if cible in modules:
                modules[cible].importe_par.append(nom)

    # ── Couverture : un test « nomme » un module s'il l'importe.
    tests_par_module: dict = {}
    tdir = RACINE / "tests"
    corpus_tests = ""
    if tdir.is_dir():
        for t in tdir.glob("test_*.py"):
            try:
                src = t.read_text(encoding="utf-8")
            except OSError:
                continue
            corpus_tests += src
            for nom in modules:
                # Deux formes d'import à reconnaître : le chemin pointé
                # complet, et `from paquet import module`.
                paquet, _, feuille = nom.rpartition(".")
                vu = nom in src or (
                    paquet and f"from {paquet} import" in src
                    and feuille in src)
                if vu:
                    tests_par_module[nom] = tests_par_module.get(nom, 0) + 1
    for nom, m in modules.items():
        m.tests = tests_par_module.get(nom, 0)

    # ── Corpus complet, pour repérer les symboles jamais référencés.
    corpus = ""
    for p in _fichiers():
        try:
            corpus += p.read_text(encoding="utf-8")
        except OSError:
            continue
    corpus += corpus_tests

    # ── Faiblesses.
    for nom, m in modules.items():
        # Un `__init__` n'est jamais importé nommément et un outil est un
        # point d'entrée : ni l'un ni l'autre n'est orphelin.
        est_init = nom.endswith(".__init__")
        est_outil = m.chemin.startswith("tools/")
        if not m.importe_par and not est_init and not est_outil:
            m.faiblesses.append("orphelin")
        if m.tests == 0 and not est_init:
            m.faiblesses.append("non couvert")

        for f in m.fonctions:
            # Une occurrence = la définition elle-même. Deux = au moins un
            # usage. C'est grossier mais suffisant, et sans faux négatif.
            if corpus.count(f) <= 1:
                m.faiblesses.append(f"symbole inerte : {f}")

    graphe = {n: m.importe for n, m in modules.items()}
    cycles = _cycles(graphe)

    total_f = sum(len(m.faiblesses) for m in modules.values())
    return {
        "indexe_le": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duree_s": round(time.time() - t0, 2),
        "modules": {n: m.to_dict() for n, m in modules.items()},
        "cycles": cycles,
        "resume": {
            "modules": len(modules),
            "lignes": sum(m.lignes for m in modules.values()),
            "fonctions": sum(len(m.fonctions) for m in modules.values()),
            "orphelins": sum(1 for m in modules.values()
                             if "orphelin" in m.faiblesses),
            "non_couverts": sum(1 for m in modules.values()
                                if "non couvert" in m.faiblesses),
            "inertes": sum(1 for m in modules.values()
                           for f in m.faiblesses if f.startswith("symbole")),
            "cycles": len(cycles),
            "faiblesses": total_f,
        },
    }


def _empreinte() -> str:
    """Signature des dates de modification — dit si la carte est périmée."""
    try:
        return str(max((p.stat().st_mtime for p in _fichiers()), default=0))
    except Exception:  # noqa: BLE001
        return str(time.time())


def carte(*, forcer: bool = False) -> dict:
    """Carte à jour, réindexée seulement si le code a changé.

    L'indexation prend une seconde ; la refaire à chaque affichage serait du
    gaspillage, mais servir une carte périmée serait pire — on la conserve
    donc avec l'empreinte du dépôt qui l'a produite.
    """
    emp = _empreinte()
    if not forcer and CACHE.exists():
        try:
            vue = json.loads(CACHE.read_text(encoding="utf-8"))
            if vue.get("empreinte") == emp:
                return vue
        except Exception:  # noqa: BLE001
            pass

    d = indexer()
    d["empreinte"] = emp
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE)
    except Exception:  # noqa: BLE001
        pass
    return d


def faiblesses() -> list:
    """Liste à plat des faiblesses, la plus grave d'abord.

    Destinée aux analystes : elle leur dit quelles parties du bot sont
    fragiles au moment où ils jugent une entrée. Un verdict rendu sur un
    organe non couvert mérite d'être pondéré.
    """
    d = carte()
    rang = {"orphelin": 0, "non couvert": 1}
    out = []
    for nom, m in (d.get("modules") or {}).items():
        for f in m.get("faiblesses", []):
            out.append({
                "module": nom, "chemin": m.get("chemin"),
                "faiblesse": f,
                "gravite": rang.get(f, 2),
                "lignes": m.get("lignes", 0),
                "tests": m.get("tests", 0),
            })
    out.sort(key=lambda x: (x["gravite"], -x["lignes"]))
    return out
