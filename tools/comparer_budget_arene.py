"""Le triplement du budget de risque change-t-il une seule decision du bot ?

CE QUI EST COMPARE
------------------
La meme arene d'execution, deux fois, cellule par cellule, avec pour seule
difference les DEUX plafonds de production :

    passe A   plafond de grappe 5,7 %   budget global 17,1 %   (livre)
    passe B   plafond de grappe 2,0 %   budget global  6,0 %   (precedent)

Meme code, memes donnees, memes politiques, memes scenarios, meme seed. Les
plafonds sont poses en memoire, a deux endroits indissociables : l'attribut du
module qui les declare, et le defaut que lit `place_disponible` (une valeur par
defaut est liee a l'import ; le seul attribut ne suffit pas). AUCUN fichier
n'est modifie, aucun seuil de production n'est deplace, et la section 2 relit
les plafonds effectivement poses pour le prouver.

LE CONTROLE QUI REND LA COMPARAISON NON VIDE
--------------------------------------------
Deux passes identiques ne prouvent rien si le harnais ne rapporte jamais de
changement. Une troisieme passe abaisse donc un reglage que l'arene lit
REELLEMENT (`risk.max_gross_exposure`) : si les cellules bougent la et pas
ailleurs, la comparaison est sensible et la conclusion tient.

LA PASSE REPRODUIT-ELLE LA MESURE PUBLIEE ?
-------------------------------------------
La section 4 rejoue le seed de l'artefact et compare, cellule par
cellule, le resultat frais a `results/execution_adaptative/*.ndjson`,
dont elle imprime les empreintes sha256. Sans cette verification,
comparer deux passes ne dirait pas si elles portent sur la mesure
publiee ou sur un harnais qui a derive. Si l'artefact est absent du
checkout (`results/` n'est pas versionne), elle le dit au lieu de
faire comme si.

LE SEED
-------
Le seed de reference enregistre dans les artefacts d'arene est `14082026`
(`results/execution_adaptative/execution_adaptative.json`). Le dossier de
production cite « seed 7 » ; ce nombre n'apparait dans AUCUN artefact d'arene
et aucun run d'arene ne l'emploie — il n'existe que dans des tests unitaires
(`MatchingSimulator(seed=7)`). Les deux sont donc joues, et nommes.

    .venv\\Scripts\\python.exe -X utf8 tools/comparer_budget_arene.py --jobs 4
    .venv\\Scripts\\python.exe -X utf8 tools/comparer_budget_arene.py \\
        --json results/comparaison_budget_arene.json

CE QUE CETTE COMPARAISON NE PERMET PAS DE CONCLURE
--------------------------------------------------
Ce n'est pas la boucle vive : l'arene simule des carnets synthetiques et
n'envoie aucun ordre. Aucun ordre reel n'est concerne. L'echantillon de
fenetres reste celui du harnais (864 scenarios, 3 splits), et une arene
insensible a un reglage ne dit pas que ce reglage est inutile en production :
elle dit seulement qu'il n'y entre pas.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools import arene_cellules as arene  # noqa: E402

#: Le seed enregistre dans les artefacts d'arene.
SEED_ARTEFACT = 14_082_026
#: Le seed que le dossier de production cite, absent de tout artefact d'arene.
SEED_DOSSIER = 7

PLAFONDS_LIVRES = {"grappe": 5.7, "global": 17.1}
PLAFONDS_PRECEDENTS = {"grappe": 2.0, "global": 6.0}

#: Les colonnes qui portent la DECISION d'execution. Comparer le `net_pnl` seul
#: laisserait passer une cellule dont le cout ou le taux de remplissage a bouge
#: a net constant.
COLONNES = ("net_pnl", "fill_ratio", "total_cost_bps", "filled_quantity",
            "rejected_reason")

TEMOIN = "market"


def _poser_plafonds(plafonds: dict[str, float]) -> None:
    """Pose les deux plafonds en memoire, sur les modules qui les declarent."""
    from titanium import correlation

    correlation.MAX_RISQUE_GRAPPE_PCT = float(plafonds["grappe"])
    # Le plafond est une valeur par defaut, liee a l'import : poser
    # l'attribut du module ne suffit pas a changer ce que lisent les
    # appelants de production. On re-lie le defaut pour que la passe
    # soit VRAIMENT sous l'autre plafond.
    correlation.place_disponible.__kwdefaults__["plafond"] = float(
        plafonds["grappe"]
    )
    try:
        from tools import live_demo
    except Exception:  # pragma: no cover - live_demo absent d'un checkout nu
        return
    live_demo.MAX_RISQUE_CUMULE_PCT = float(plafonds["global"])


def lire_plafonds_effectifs() -> dict[str, float]:
    """Les plafonds que liront les appelants, lus dans le code lui-meme."""
    from titanium import correlation

    effectifs = {
        "grappe": float(correlation.MAX_RISQUE_GRAPPE_PCT),
        "grappe_defaut_appelants": float(
            correlation.place_disponible.__kwdefaults__["plafond"]
        ),
    }
    try:
        from tools import live_demo
    except Exception:
        return effectifs
    effectifs["global"] = float(live_demo.MAX_RISQUE_CUMULE_PCT)
    return effectifs


def _cellules(rows: list[dict]) -> dict[str, list]:
    """Une signature par cellule : (politique, split, scenario).

    La cle d'appariement et la projection appartiennent a
    ``tools.arene_cellules`` ; ici on ne fait que nommer les colonnes de
    decision de ce harnais.
    """
    return arene.projeter(arene.indexer(rows), COLONNES)


def _comparer(gauche: dict[str, list], droite: dict[str, list]) -> dict:
    """Cellule par cellule : combien bougent, de combien.

    La REGLE (cle, colonnes, comptage, ecart net) vit dans
    ``tools.arene_cellules`` ; ce harnais ne fait que mettre le resultat en
    forme pour son rapport.
    """
    brut = arene.comparer(gauche, droite, colonnes=COLONNES)
    return {
        "cellules_comparees": brut["cellules_communes"],
        "cellules_differentes": brut["cellules_bougees"],
        "ecart_net_max": brut["ecart_net_max"],
        "exemples": [(cle, list(a), list(b)) for cle, _, a, b in brut["ecarts"][:5]],
        "politiques_touchees": sorted(brut["bougees_par_politique"]),
    }


def _passer(seed: int, jobs: int, *, plafonds=None, config=None,
            quick: bool = False) -> dict:
    """Une passe d'arene. `plafonds` et `config` sont poses en memoire."""
    from titanium.execution_sim.adaptive import ADAPTIVE_POLICIES
    from titanium.execution_sim.config import load_config
    from titanium.execution_sim.runner import MatrixSpec, run_matrix

    if plafonds is not None:
        _poser_plafonds(plafonds)
    charge = config if config is not None else load_config(None)
    spec = MatrixSpec(
        policies=(TEMOIN, *tuple(ADAPTIVE_POLICIES)), seed=seed, jobs=jobs,
        quick=quick,
    )
    rows = run_matrix(spec, charge)
    cellules = _cellules(rows)
    return {"cellules": cellules, "empreinte": arene.empreinte(cellules),
            "lignes": len(rows)}


def structure_immunisee() -> list[str]:
    """Les modules du simulateur qui mentionnent un plafond de production.

    C'est la lecture structurelle : les plafonds ne peuvent pas entrer dans
    l'arene s'ils n'y sont pas importes ni nommes.
    """
    dossiers = [RACINE / "titanium" / "execution_sim"]
    fautifs = []
    for dossier in dossiers:
        for chemin in sorted(dossier.rglob("*.py")):
            texte = chemin.read_text(encoding="utf-8", errors="replace")
            for nom in ("MAX_RISQUE_GRAPPE_PCT", "MAX_RISQUE_CUMULE_PCT",
                        "titanium.correlation", "tools.live_demo"):
                if nom in texte:
                    fautifs.append(f"{chemin.relative_to(RACINE)}: {nom}")
    return fautifs


def porte_de_production(plafonds: dict[str, float]) -> dict:
    """Ce que le plafond de grappe gouverne VRAIMENT, sur un livre fige.

    Meme livre, meme candidat, seul le plafond change : on releve a partir de
    quel risque deja porte la porte se ferme. C'est la ou le triplement se voit.
    """
    from titanium import correlation

    # Le plafond est passe en argument : cette sonde ne mute aucun etat
    # global, donc elle ne peut pas contaminer l'appelant.
    limite = float(plafonds["grappe"])

    class _Pos:
        # 1 % de risque par lot sur 10 000 d'equite : le volume porte le %.
        def __init__(self, symbol: str, vol: float = 1.0) -> None:
            self.symbol = symbol
            self.sl = 1.099
            self.price_open = 1.100
            self.volume = vol

    class _Spec:
        trade_tick_size = 0.00001
        trade_tick_value = 1.0

    def _mt5(positions):
        return type("M", (), {
            "positions_get": staticmethod(lambda *a, **k: positions),
            "symbol_info": staticmethod(lambda s: _Spec()),
        })()

    grappes = correlation.Grappes(
        par_actif={"EURJPY": "g4", "NZDJPY": "g4", "AUDJPY": "g4"},
        membres={"g4": ["EURJPY", "NZDJPY", "AUDJPY"]},
    )
    # 1 % par lot sur 10 000 d'equite : le volume porte directement le %.
    seuil = None
    for dixieme in range(10, 201):
        porte = dixieme / 10.0
        # La grappe porte  %, et on demande 0,5 % de plus sur AUDJPY.
        ok, motif = correlation.place_disponible(
            "AUDJPY", 0.5, _mt5([_Pos("EURJPY", vol=porte)]),
            grappes, 10_000.0, plafond=limite,
        )
        if "invalide" in motif or "inconnue" in motif:
            # Un livre invalide n'est pas un livre a son plafond.
            return {"plafond_grappe_pct": float(plafonds["grappe"]),
                    "erreur": motif}
        if not ok:
            seuil = float(porte)
            break
    return {
        "plafond_grappe_pct": float(plafonds["grappe"]),
        "refus_quand_la_grappe_porte_pct": seuil,
        "grappes_pleines_dans_le_global": round(
            float(plafonds["global"]) / float(plafonds["grappe"]), 4
        ),
        "trades_a_1pct_dans_le_global": int(float(plafonds["global"]) // 1.0),
    }


#: L'artefact de mesure publie par `tools/execution_adaptative.py`.
ARTEFACT = RACINE / "results" / "execution_adaptative" / "execution_adaptative.ndjson"
ARTEFACT_MD = RACINE / "results" / "execution_adaptative" / "execution_adaptative.md"


def charger_artefact(chemin: Path) -> dict[str, list]:
    """Les cellules de l'artefact `ndjson`, dans la meme signature que la passe."""
    return _cellules(arene.lire_ndjson(chemin))


def verifier_reproduction(seed: int, jobs: int = 4, *, quick: bool = False,
                          artefact: Path | None = None) -> dict:
    """La passe rejoue-t-elle, cellule par cellule, l'artefact publie ?

    Sans cette verification, comparer deux passes ne dit pas si elles portent
    sur la mesure publiee ou sur un harnais qui a derive.
    """
    chemin = Path(artefact) if artefact is not None else ARTEFACT
    if not chemin.exists():
        return {"artefact": str(chemin), "present": False,
                "raison": "artefact absent de ce checkout"}

    mesure = _passer(seed, jobs, quick=quick)
    publie = charger_artefact(chemin)
    comparaison = _comparer(publie, mesure["cellules"])
    resultat = {
        "artefact": str(chemin.relative_to(RACINE)),
        "present": True,
        "sha256_ndjson": arene.sha256_fichier(chemin),
        "cellules_publiees": len(publie),
        "cellules_rejouees": len(mesure["cellules"]),
        "cellules_differentes": comparaison["cellules_differentes"],
        "cellules_comparees": comparaison["cellules_comparees"],
        "empreinte_publiee": arene.empreinte(publie),
        "empreinte_rejouee": mesure["empreinte"],
    }
    if ARTEFACT_MD.exists():
        resultat["sha256_markdown"] = arene.sha256_fichier(ARTEFACT_MD)
        resultat["markdown"] = str(ARTEFACT_MD.relative_to(RACINE))
    return resultat


def comparer_arene(seed: int, jobs: int = 4, *, quick: bool = False,
                   avec_controle: bool = True) -> dict:
    """La comparaison complete : deux jeux de plafonds, plus le controle.

    Le controle abaisse `risk.max_gross_exposure`, un reglage que l'arene
    LIT vraiment : sans lui, « aucune cellule ne bouge » ne prouverait rien.
    """
    a = _passer(seed, jobs, plafonds=PLAFONDS_LIVRES, quick=quick)
    relus_a = lire_plafonds_effectifs()
    b = _passer(seed, jobs, plafonds=PLAFONDS_PRECEDENTS, quick=quick)
    relus_b = lire_plafonds_effectifs()

    controle = None
    if avec_controle:
        from titanium.execution_sim.config import load_config

        controle_cfg = load_config(None)
        controle_cfg["risk"]["max_gross_exposure"] = 100.0
        controle = _passer(seed, jobs, plafonds=PLAFONDS_LIVRES,
                           config=controle_cfg, quick=quick)

    # Les passes ont pose leurs plafonds en memoire : on rend l'etat livre,
    # pour qu'un appelant ne les retrouve pas decales.
    _poser_plafonds(PLAFONDS_LIVRES)

    return {
        "a": a,
        "b": b,
        "controle": controle,
        "budget_vs_budget": _comparer(a["cellules"], b["cellules"]),
        "budget_vs_controle": (
            _comparer(a["cellules"], controle["cellules"])
            if controle is not None else None
        ),
        "plafonds_effectifs": {"A": relus_a, "B": relus_b},
    }


def main() -> int:
    analyseur = argparse.ArgumentParser(description="Comparer l'arene sous deux jeux de plafonds")
    analyseur.add_argument("--jobs", type=int, default=4)
    analyseur.add_argument("--seeds", type=int, nargs="+",
                           default=[SEED_ARTEFACT, SEED_DOSSIER])
    analyseur.add_argument("--json", type=Path, default=None)
    args = analyseur.parse_args()

    rapport: dict = {"seeds": {}, "structure": {}}

    print("\n1. L'ARENE LIT-ELLE LES PLAFONDS DE PRODUCTION ?")
    print("-" * 49)
    fautifs = structure_immunisee()
    print(f"  modules de `execution_sim` nommant un plafond de production : "
          f"{len(fautifs)}")
    for fautif in fautifs:
        print(f"    {fautif}")
    rapport["structure"]["modules_fautifs"] = fautifs

    for seed in args.seeds:
        print(f"\n2. SEED {seed}" + (" (artefact)" if seed == SEED_ARTEFACT
                                     else " (cite par le dossier, absent des artefacts)"))
        print("-" * 40)
        if seed == SEED_DOSSIER:
            print("  ⚠️  aucun artefact d'arene n'enregistre ce seed : les runs")
            print("     d'arene emploient 14082026. Il est joue quand meme.")

        resultat = comparer_arene(seed, args.jobs)
        a, b, controle = resultat["a"], resultat["b"], resultat["controle"]
        ab = resultat["budget_vs_budget"]
        ac = resultat["budget_vs_controle"]
        relus_a = resultat["plafonds_effectifs"]["A"]
        relus_b = resultat["plafonds_effectifs"]["B"]
        print(f"  plafonds reellement poses : A={relus_a} B={relus_b}")
        if relus_a == relus_b:
            print("  ✗ les deux passes tournent sous les MEMES plafonds :")
            print("    la comparaison serait vide par construction.")
            return 2
        rapport["seeds"][str(seed) + "_plafonds_effectifs"] = {
            "A": relus_a, "B": relus_b,
        }

        print(f"  cellules comparees            : {ab['cellules_comparees']}")
        print(f"  5,7/17,1  vs  2,0/6,0 -> differentes : "
              f"{ab['cellules_differentes']}")
        print(f"  CONTROLE (max_gross_exposure 100)  -> differentes : "
              f"{ac['cellules_differentes']}"
              + (f" · politiques touchees : {ac['politiques_touchees'][:4]}"
                 if ac["politiques_touchees"] else ""))
        print(f"  empreintes : A={a['empreinte'][:16]} B={b['empreinte'][:16]} "
              f"C={controle['empreinte'][:16]}")
        if ab["cellules_differentes"] == 0:
            print("  -> AUCUNE cellule ne bouge : l'arene ne lit pas ce budget.")
        rapport["seeds"][str(seed)] = {
            "lignes": a["lignes"],
            "empreinte_livres": a["empreinte"],
            "empreinte_precedents": b["empreinte"],
            "empreinte_controle": controle["empreinte"],
            "budget_vs_budget": ab,
            "budget_vs_controle": ac,
        }

    print("\n3. CE QUE LE BUDGET GOUVERNE REELLEMENT")
    print("-" * 39)
    livre = porte_de_production(PLAFONDS_LIVRES)
    precedent = porte_de_production(PLAFONDS_PRECEDENTS)
    print("  porte de GRAPPE — le refus tombe quand la grappe porte deja :")
    for etiquette, valeurs in (("2,0 % (avant)", precedent),
                               ("5,7 % (apres)", livre)):
        refus = valeurs["refus_quand_la_grappe_porte_pct"]
        print(f"    plafond {etiquette:>14}  -> refus des {refus} % portes")
    print("  enveloppe GLOBALE :")
    for etiquette, valeurs in (("6,0 % (avant)", precedent),
                               ("17,1 % (apres)", livre)):
        print(f"    budget {etiquette:>14}  -> "
              f"{valeurs['grappes_pleines_dans_le_global']:.2f} grappes pleines, "
              f"{valeurs['trades_a_1pct_dans_le_global']} trades a 1 %")
    rapport["porte"] = {"livres": livre, "precedents": precedent}

    print("\n4. LA PASSE REPRODUIT-ELLE L'ARTEFACT PUBLIE ?")
    print("-" * 48)
    reproduction = verifier_reproduction(args.seeds[0], args.jobs)
    rapport["reproduction"] = reproduction
    if not reproduction.get("present"):
        print(f"  artefact absent de ce checkout ({reproduction['artefact']}) :")
        print("  la reproduction ne peut pas etre constatee ici.")
    else:
        print(f"  {reproduction['artefact']}")
        print(f"    sha256 {reproduction['sha256_ndjson']}")
        if "sha256_markdown" in reproduction:
            print(f"  {reproduction['markdown']}")
            print(f"    sha256 {reproduction['sha256_markdown']}")
        print(f"  cellules : publiees={reproduction['cellules_publiees']} "
              f"rejouees={reproduction['cellules_rejouees']} "
              f"differentes={reproduction['cellules_differentes']}")
        if reproduction["cellules_differentes"] == 0:
            print("  -> la passe rejoue l'artefact publie au bit pres.")
        else:
            print("  -> la passe DIVERGE de l'artefact publie : les deux passes")
            print("     ne portent donc pas sur la mesure publiee.")

    print("\n5. CE QUE CETTE COMPARAISON NE PERMET PAS DE CONCLURE")
    print("-" * 56)
    for ligne in (
        "Ce n'est pas la boucle vive : l'arene simule des carnets synthetiques.",
        "Aucun ordre reel n'est concerne, aucun seuil de production n'est touche.",
        "L'echantillon de fenetres reste celui du harnais (864 scenarios, 3 splits).",
        "L'arene insensible a un reglage ne dit pas que ce reglage est inutile en",
        "production — elle dit seulement qu'il n'y entre pas.",
    ):
        print(f"  {ligne}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(rapport, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"\nrapport ecrit : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
