"""Mesure la famille d'execution adaptative contre le temoin ``market``.

Mode **backtest/dry-run** uniquement : ce script lit ``execution_sim``, qui
refuse ``execution.live_enabled`` vrai. Il n'appelle ni MT5, ni compte, ni
reseau.

Ce qui est calcule, et pourquoi
-------------------------------

Les 864 scenarios sont identiques d'une technique a l'autre et portent le meme
alpha. L'ecart APPARIE scenario par scenario isole donc le delta d'execution du
bruit de tirage. C'est la seule lecture qui distingue une vraie difference de
cout d'une difference de tirage -- lecon des quinze politiques historiques.

Sortie : ``results/execution_adaptative/`` (CSV, JSON, Markdown). Aucun seuil
n'est modifie, aucune promotion n'en decoule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from titanium.execution_sim.adaptive import ADAPTIVE_CATALOG, ADAPTIVE_POLICIES  # noqa: E402
from titanium.execution_sim.config import load_config  # noqa: E402
from titanium.execution_sim.runner import (  # noqa: E402
    ALL_POLICIES,
    MatrixSpec,
    _run_case,
    engine_fingerprint,
    generate_scenarios,
    run_matrix,
)
from titanium.macro import load_policy  # noqa: E402
from titanium.macro.contracts import MacroRisk, MacroState  # noqa: E402
from titanium.macro.gate import MACRO_POSTURE_KEY, macro_block  # noqa: E402
from tools import arene_cellules as arene  # noqa: E402

TEMOIN = "market"

REGIMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spread", ("normal", "wide")),
    ("volatility", ("low", "medium", "high")),
    ("size", ("small", "large")),
    ("liquidity", ("low", "high")),
    ("trend", ("up", "down", "range")),
)

#: Axes que la famille adaptative declare observer : libelle lisible, colonne
#: du rapport, cle du forcage, valeurs. Les DEUX cles sont distinctes a dessein :
#: la colonne s'appelle ``adapt_inventory_ratio``, le forcage attend
#: ``inventory_ratio``. Confondre les deux faisait pedaler une sonde dans le
#: vide et declarait tous les axes inertes -- vu en testant la sonde.
AXES_ADAPTATIFS: tuple[tuple[str, str, str, tuple[float, ...]], ...] = (
    ("inventaire", "adapt_inventory_ratio", "inventory_ratio", (-0.75, 0.0, 0.75)),
    ("urgence", "adapt_urgency", "urgency", (0.15, 0.5, 0.9)),
    ("horizon", "adapt_horizon_ms", "horizon_ms", (8_000.0, 20_000.0, 60_000.0)),
)

#: Ces techniques declarent dependre d'un axe precis ; les autres n'en
#: dependent pas et ne doivent donc pas y reagir.
AXE_DECLARE: dict[str, str] = {
    "adapt_inventory_skew": "inventaire",
    "adapt_urgency_ladder": "urgence",
    "adapt_deadline_ladder": "horizon",
    "adapt_ladder_maker_taker": "horizon",
}


def _moyenne(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _ecart_type(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _index(rows: list[dict[str, Any]], policy: str) -> dict[tuple, dict[str, Any]]:
    return {
        (row["scenario_id"], row["split"]): row for row in rows if row["policy"] == policy
    }


def _appariement(rows: list[dict[str, Any]], policy: str) -> list[tuple[dict, float, float]]:
    """(scenario, net de la politique, net du temoin) pour chaque scenario commun."""
    reference = _index(rows, TEMOIN)
    paires = []
    for row in rows:
        if row["policy"] != policy:
            continue
        temoin = reference.get((row["scenario_id"], row["split"]))
        if temoin is None:
            continue
        paires.append((row, float(row["net_pnl"]), float(temoin["net_pnl"])))
    return paires


def _statistiques(paires: list[tuple[dict, float, float]]) -> dict[str, float]:
    if not paires:
        return dict.fromkeys(
            (
                "n",
                "delta_moyen",
                "erreur_type",
                "z",
                "gagnes",
                "perdus",
                "pire_cas",
                "meilleur_cas",
                "fill",
                "cost_bps",
            ),
            0.0,
        )
    deltas = [net - temoin for _, net, temoin in paires]
    moyenne = _moyenne(deltas)
    erreur = _ecart_type(deltas) / math.sqrt(len(deltas)) if len(deltas) > 1 else 0.0
    return {
        "n": float(len(deltas)),
        "delta_moyen": round(moyenne, 10),
        "erreur_type": round(erreur, 10),
        "z": round(moyenne / erreur, 6) if erreur > 0 else 0.0,
        "gagnes": round(sum(value > 0 for value in deltas) / len(deltas), 6),
        "perdus": round(sum(value < 0 for value in deltas) / len(deltas), 6),
        "pire_cas": round(min(deltas), 10),
        "meilleur_cas": round(max(deltas), 10),
        "fill": round(_moyenne([float(row["fill_ratio"]) for row, _, _ in paires]), 6),
        "cost_bps": round(_moyenne([float(row["total_cost_bps"]) for row, _, _ in paires]), 6),
    }


def _par_regime(paires: list[tuple[dict, float, float]]) -> dict[str, dict[str, float]]:
    sortie: dict[str, dict[str, float]] = {}
    for axe, modalites in REGIMES:
        for modalite in modalites:
            sous = [paire for paire in paires if paire[0][axe] == modalite]
            sortie[f"{axe}={modalite}"] = {
                "n": float(len(sous)),
                "delta_moyen": round(_moyenne([net - temoin for _, net, temoin in sous]), 10),
            }
    for libelle, colonne, _cle, modalites in AXES_ADAPTATIFS:
        for modalite in modalites:
            sous = [paire for paire in paires if paire[0].get(colonne) == modalite]
            sortie[f"{libelle}={modalite}"] = {
                "n": float(len(sous)),
                "delta_moyen": round(_moyenne([net - temoin for _, net, temoin in sous]), 10),
            }
    return sortie


def _signature(row: dict[str, Any]) -> tuple:
    return (
        round(float(row["net_pnl"]), 10),
        round(float(row["fill_ratio"]), 10),
        round(float(row["total_cost_bps"]), 10),
    )


def _independance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cherche les techniques indiscernables, scenario par scenario.

    Deux techniques identiques sur TOUS les scenarios ne sont pas deux
    resultats : c'est un nom en double. Le rapport des quinze politiques avait
    trouve ce defaut entre ``iceberg`` et ``limit passive`` ; le mesurer ici en
    fait une porte, pas une relecture.
    """
    signatures = {
        policy: {row["scenario_id"]: _signature(row) for row in rows if row["policy"] == policy}
        for policy in ADAPTIVE_POLICIES
    }
    paires = []
    collisions = []
    for index, gauche in enumerate(ADAPTIVE_POLICIES):
        for droite in ADAPTIVE_POLICIES[index + 1 :]:
            a, b = signatures[gauche], signatures[droite]
            communs = set(a) & set(b)
            identiques = sum(1 for cle in communs if a[cle] == b[cle])
            entree = {
                "gauche": gauche,
                "droite": droite,
                "scenarios_communs": len(communs),
                "identiques": identiques,
                "collision": bool(communs) and identiques == len(communs),
            }
            paires.append(entree)
            if entree["collision"]:
                collisions.append(f"{gauche} == {droite}")
    return {"paires": paires, "collisions": collisions, "independantes": not collisions}


def _sonde_axes(
    config: dict[str, Any], scenarios: list[Any]
) -> dict[str, dict[str, bool]]:
    """Un axe declare change-t-il reellement le resultat ?

    Meme scenario, un seul axe deplace a la fois, sur PLUSIEURS scenarios : sur
    un seul, une technique qui ne trade pas ce jour-la rend la meme signature a
    toutes les valeurs de l'axe et paraîtrait inerte a tort -- defaut trouve en
    testant la sonde elle-meme. L'axe est declare REACTIF des qu'un scenario
    montre une signature differente, ce qui est un effet causal et non un
    tirage, puisque tout le reste est tenu fixe.
    """
    scenarios = list(scenarios)
    resultat: dict[str, dict[str, bool]] = {}
    for policy in ADAPTIVE_POLICIES:
        detail: dict[str, bool] = {}
        for libelle, _colonne, cle, modalites in AXES_ADAPTATIFS:
            reactif = False
            for scenario in scenarios:
                signatures = {
                    _signature(
                        _run_case(policy, scenario, config, 100_000.0, axes_override={cle: m})
                    )
                    for m in modalites
                }
                if len(signatures) > 1:
                    reactif = True
                    break
            detail[libelle] = reactif
        resultat[policy] = detail
    return resultat


def _classes_equivalence(independance: dict[str, Any]) -> list[list[str]]:
    """Regroupe les techniques indiscernables sur tous les scenarios."""
    parent = {policy: policy for policy in ADAPTIVE_POLICIES}

    def trouver(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for paire in independance["paires"]:
        if paire["collision"]:
            gauche, droite = trouver(paire["gauche"]), trouver(paire["droite"])
            if gauche != droite:
                parent[droite] = gauche
    groupes: dict[str, list[str]] = {}
    for policy in ADAPTIVE_POLICIES:
        groupes.setdefault(trouver(policy), []).append(policy)
    return [sorted(groupe) for groupe in groupes.values()]


def _par_tiers(paires: list[tuple[dict, float, float]]) -> dict[str, float]:
    sortie = {}
    for split in ("development", "validation", "final_oos"):
        sous = [paire for paire in paires if paire[0]["split"] == split]
        sortie[split] = round(_moyenne([net - temoin for _, net, temoin in sous]), 10)
    return sortie


def _empreinte_config(config: dict[str, Any]) -> str:
    charge = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(charge.encode()).hexdigest()[:16]


def build_report(rows: list[dict[str, Any]], *, seed: int, quick: bool) -> dict[str, Any]:
    paires = {policy: _appariement(rows, policy) for policy in (TEMOIN, *ADAPTIVE_POLICIES)}
    synthese = {}
    for policy in ADAPTIVE_POLICIES:
        stats = _statistiques(paires[policy])
        stats["par_regime"] = _par_regime(paires[policy])
        stats["par_tiers"] = _par_tiers(paires[policy])
        hypothese = next(
            (item["hypothesis"] for item in ADAPTIVE_CATALOG if item["name"] == policy), ""
        )
        stats["hypothese"] = hypothese
        synthese[policy] = stats
    classement = sorted(
        ADAPTIVE_POLICIES, key=lambda name: (-synthese[name]["delta_moyen"], name)
    )
    independance = _independance(rows)
    classes = _classes_equivalence(independance)
    gagnantes = [name for name in classement if synthese[name]["delta_moyen"] > 0]
    classes_gagnantes = [
        groupe for groupe in classes if any(synthese[name]["delta_moyen"] > 0 for name in groupe)
    ]
    axes = _sonde_axes(load_config(), generate_scenarios(seed=seed, quick=quick)[:12])
    return {
        "mode": "backtest/dry-run",
        "live_enabled": False,
        "seed": seed,
        "quick": quick,
        "engine_version": engine_fingerprint(),
        "temoin": TEMOIN,
        "temoins_disponibles": list(ALL_POLICIES),
        "synthese": synthese,
        "classement": classement,
        "independance": independance,
        "classes_equivalence": classes,
        "gagnantes": gagnantes,
        "gagnantes_distinctes": len(classes_gagnantes),
        "sonde_axes": axes,
        # Un axe NON declare qui ne change rien est normal : la technique ne
        # pretend pas dependre de lui. Seul un axe DECLARE et inerte est un
        # defaut -- c'est une technique qui ne s'adapte pas a ce qu'elle annonce.
        "axes_inertes": sorted(
            f"{policy}:{libelle}"
            for policy, detail in axes.items()
            for libelle, reagit in detail.items()
            if not reagit and AXE_DECLARE.get(policy) == libelle
        ),
        "axes_declares": dict(AXE_DECLARE),
    }


def ecrire(resultat: dict[str, Any], rows: list[dict[str, Any]], output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "execution_adaptative.json"
    ndjson_path = output / "execution_adaptative.ndjson"
    md_path = output / "execution_adaptative.md"
    json_path.write_text(
        json.dumps(resultat, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    with ndjson_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    lignes = [
        "# Execution adaptative V14 — mesure contre `market`",
        "",
        f"Mode **backtest/dry-run**, `live_enabled=false`, seed {resultat['seed']}, "
        f"moteur `{resultat['engine_version']}`.",
        "",
        "Delta APPARIE scenario par scenario contre le temoin `market` : le bruit de "
        "tirage est retire des deux cotes.",
        "",
        "## Classement par delta apparié",
        "",
        "| # | technique | delta | ±se | z | gagne | perd | pire cas | fill | coût bps |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rang, name in enumerate(resultat["classement"], 1):
        stats = resultat["synthese"][name]
        lignes.append(
            f"| {rang} | {name} | {stats['delta_moyen']:+.4f} | {stats['erreur_type']:.4f} | "
            f"{stats['z']:+.2f} | {stats['gagnes']:.1%} | {stats['perdus']:.1%} | "
            f"{stats['pire_cas']:+.4f} | {stats['fill']:.1%} | {stats['cost_bps']:.3f} |"
        )
    lignes += [
        "",
        "## Independance des techniques",
        "",
        f"{len(resultat['classement'])} noms, **{len(resultat['classes_equivalence'])} "
        f"comportements distincts**, {len(resultat['gagnantes'])} gagnants dont "
        f"**{resultat['gagnantes_distinctes']} gagnants distincts**.",
        "",
    ]
    if resultat["independance"]["collisions"]:
        lignes.append("⚠️ Collisions (identiques sur TOUS les scenarios) :")
        lignes += [f"- {item}" for item in resultat["independance"]["collisions"]]
    else:
        lignes.append(
            "Aucune paire identique sur tous les scenarios : chaque ligne du "
            "classement est un resultat independant."
        )
    lignes += [
        "",
        "| paire indiscernable (>=99 % des scenarios) | identiques / communs |",
        "|---|---:|",
    ]
    proches = sorted(
        (
            p
            for p in resultat["independance"]["paires"]
            if p["scenarios_communs"] and p["identiques"] / p["scenarios_communs"] >= 0.99
        ),
        key=lambda item: (-item["identiques"] / item["scenarios_communs"], item["gauche"]),
    )
    for paire in proches:
        lignes.append(
            f"| {paire['gauche']} / {paire['droite']} | {paire['identiques']} / "
            f"{paire['scenarios_communs']} |"
        )
    if not proches:
        lignes.append("| aucune | — |")
    lignes += [
        "",
        "## Sonde d'axes — l'axe declare change-t-il le resultat ?",
        "",
        "Meme scenario, un seul axe deplace a la fois. `inerte` = toutes les "
        "valeurs rendent la meme signature.",
        "",
        "| technique | axe declare | reactif |",
        "|---|---|---|",
    ]
    for name in resultat["classement"]:
        declare = AXE_DECLARE.get(name, "—")
        detail = resultat["sonde_axes"][name]
        etat = "—" if declare == "—" else ("oui" if detail.get(declare) else "INERTE")
        lignes.append(f"| {name} | {declare} | {etat} |")
    if resultat["axes_inertes"]:
        lignes += ["", "⚠️ Axes inertes : " + ", ".join(resultat["axes_inertes"])]
    lignes += ["", "## Tenue hors echantillon (tiers final)", "", "| technique | dev | validation | OOS |", "|---|---:|---:|---:|"]
    for name in resultat["classement"]:
        tiers = resultat["synthese"][name]["par_tiers"]
        lignes.append(
            f"| {name} | {tiers['development']:+.4f} | {tiers['validation']:+.4f} | "
            f"{tiers['final_oos']:+.4f} |"
        )
    lignes += ["", "## Delta par regime", ""]
    for name in resultat["classement"]:
        regimes = resultat["synthese"][name]["par_regime"]
        paires = " ; ".join(f"{cle} {regimes[cle]['delta_moyen']:+.3f}" for cle in regimes)
        lignes.append(f"- **{name}** — {paires}")
    lignes += [
        "",
        "## Hypotheses enregistrees",
        "",
    ]
    for name in resultat["classement"]:
        lignes.append(f"- `{name}` : {resultat['synthese'][name]['hypothese']}")
    lignes += [
        "",
        "## Lecture et limites",
        "",
        "- Aucune de ces mesures ne prouve une rentabilite : les quotes, la "
        "profondeur et le chemin intrabarre sont synthetiques.",
        "- Un delta positif moyen n'est pas une garantie par trade : lire la "
        "colonne « pire cas » et le taux de scenarios perdants.",
        "- Les techniques qui dependent d'un carnet de reference (ancre "
        "microprice, tranchage par profondeur) restent des approximations tant "
        "que les quotes broker ne sont pas archivees.",
        "- Ce fichier ne modifie aucun seuil et n'autorise aucun passage live.",
    ]
    md_path.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return {"json": json_path, "ndjson": ndjson_path, "markdown": md_path}


def _tension(valeur: str) -> float:
    """Tension d'execution : [0, 1], refusee hors bornes.

    Hors bornes, le mecanisme refuse deja de planifier -- une posture
    illisible n'est pas une posture neutre -- mais l'arene rendait alors un
    rapport a un seul comportement et 136 collisions, en imprimant la valeur
    declaree comme si elle avait ete appliquee. Un refus explicite vaut mieux
    qu'un rapport qui a l'air d'une mesure.
    """
    try:
        tension = float(valeur)
    except ValueError:
        raise argparse.ArgumentTypeError(f"tension illisible : {valeur!r}") from None
    if not 0.0 <= tension <= 1.0:
        raise argparse.ArgumentTypeError(f"tension hors de [0, 1] : {valeur!r}")
    return tension


def _table_lisible(valeur: str) -> Path:
    """Table ndjson de reference : elle doit exister DES LE LANCEMENT.

    Sans ce refus, une faute de frappe rendait une `FileNotFoundError`
    brute apres douze secondes de calcul. Le cas n'est pas theorique :
    l'artefact de reference n'est pas versionne, donc un checkout neuf
    n'en a aucun et c'est le chemin que le mode d'emploi decrit.
    """
    chemin = Path(valeur)
    if not chemin.is_file():
        raise argparse.ArgumentTypeError(f"table de reference introuvable : {chemin}")
    return chemin


def bloc_posture(tension: float, *, echelle_s: float) -> dict[str, Any] | None:
    """Bloc macro d'une posture d'execution, construit par son PROPRIETAIRE.

    La forme du bloc n'est pas recopiee ici : ``titanium.macro.gate`` la
    possede (``macro_block``), et la posture sort de ``macro_posture``, seule
    formule du depot. Le harnais ne choisit que l'INSTANT d'une publication :
    la tension demandee est traduite en delai avant publication, sur
    l'echelle de temps du veto, ce qui la rend lisible par la fonction que la
    production emploie. Une tension nulle vaut neutre : aucun bloc, donc
    exactement le comportement d'avant.
    """
    if tension <= 0.0:
        return None
    delai_s = echelle_s * (1.0 - tension) / tension
    risque = MacroRisk(
        state=MacroState.CLEAR,
        score=0.0,
        now=datetime.now(timezone.utc),
        next_event_title="posture de mesure (aucune publication lue)",
        seconds_to_next=delai_s,
    )
    return macro_block(risque, posture_scale_s=echelle_s)


def comparer(reference: Path, courant: Path) -> dict[str, Any]:
    """Compare deux tables cellule par cellule, sur TOUTES les colonnes.

    La REGLE vit dans ``tools.arene_cellules`` (cle d'appariement, lecture du
    ndjson, denombrement, empreintes) ; ce harnais ne choisit que sa
    POLITIQUE -- toutes les colonnes, cellules presentes d'un seul cote
    comptees comme des ecarts -- et met le resultat en forme.
    """
    gauche, droite = arene.lire_ndjson(reference), arene.lire_ndjson(courant)
    noms: set[str] = set()
    for row in gauche + droite:
        noms |= set(row)
    colonnes = tuple(sorted(noms))
    politique = arene.comparer(
        arene.projeter(arene.indexer(gauche), colonnes),
        arene.projeter(arene.indexer(droite), colonnes),
        colonnes=colonnes,
        cellules_absentes_comptent=True,
    )
    return {
        "reference": str(reference),
        "courant": str(courant),
        "sha256_reference": arene.sha256_fichier(reference),
        "sha256_courant": arene.sha256_fichier(courant),
        "cellules_comparees": politique["cellules_comparees"],
        "cellules_identiques": politique["cellules_identiques"],
        "cellules_bougees": politique["cellules_bougees"],
        "colonnes_bougees": politique["colonnes_bougees"],
        "cellules_bougees_par_technique": politique["bougees_par_politique"],
        "exemples": [
            {"cle": cle.split("|"), "colonnes": list(champs)}
            for cle, champs, _, _ in politique["ecarts"][:20]
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="execution-adaptative",
        description="Mesure les techniques d'execution adaptative contre market (dry-run)",
    )
    parser.add_argument("--config", default=str(ROOT / "config" / "execution_backtest.json"))
    parser.add_argument(
        "--output", default=str(ROOT / "results" / "execution_adaptative")
    )
    parser.add_argument("--seed", type=int, default=14_082_026)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--macro-tension",
        type=_tension,
        default=0.0,
        help="posture d'execution [0,1] appliquee a tous les cas ; 0 = neutre (defaut)",
    )
    parser.add_argument(
        "--comparer",
        type=_table_lisible,
        default=None,
        help="table ndjson de reference a comparer cellule par cellule",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if config["execution"].get("live_enabled") is not False:
        raise SystemExit("refus : execution.live_enabled doit rester false")
    spec = MatrixSpec(
        policies=(TEMOIN, *ADAPTIVE_POLICIES),
        seed=args.seed,
        quick=args.quick,
        jobs=max(1, args.jobs),
    )
    politique_macro = load_policy()
    echelle_s = float(politique_macro.elevated_within_s)
    macro = bloc_posture(args.macro_tension, echelle_s=echelle_s)
    rows = run_matrix(spec, config, macro=macro)
    resultat = build_report(rows, seed=args.seed, quick=args.quick)
    resultat["config_fingerprint"] = _empreinte_config(config)
    # Posture REELLEMENT posee, relue sur le bloc du proprietaire : une
    # valeur declaree que le mecanisme refuse ne doit pas s'afficher comme
    # une mesure. L'echelle est ecrite a cote, parce que c'est elle qui
    # relie la tension demandee a un delai avant publication.
    resultat["posture_macro"] = (
        float(macro[MACRO_POSTURE_KEY]) if macro is not None else 0.0
    )
    resultat["posture_echelle_s"] = echelle_s
    sorties = ecrire(resultat, rows, Path(args.output))
    if args.comparer is not None:
        comparaison = comparer(Path(args.comparer), sorties["ndjson"])
        chemin = Path(args.output) / "comparaison_posture.json"
        chemin.write_text(
            json.dumps(comparaison, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        resultat["comparaison_posture"] = comparaison
        print(
            f"comparaison: cellules={comparaison['cellules_comparees']} "
            f"identiques={comparaison['cellules_identiques']} "
            f"bougees={comparaison['cellules_bougees']} "
            f"sha_reference={comparaison['sha256_reference'][:16]} "
            f"sha_courant={comparaison['sha256_courant'][:16]}"
        )
        print(f"colonnes_bougees={comparaison['colonnes_bougees']}")
        print(f"par_technique={comparaison['cellules_bougees_par_technique']}")
        print(f"comparaison: {chemin}")
    print(
        f"mode=backtest/dry-run live_enabled=false seed={args.seed} "
        f"techniques={len(ADAPTIVE_POLICIES)} scenarios_par_technique="
        f"{len(rows) // max(1, len(spec.policies))}"
    )
    for rang, name in enumerate(resultat["classement"], 1):
        stats = resultat["synthese"][name]
        print(
            f"{rang:2d}. {name:30s} delta={stats['delta_moyen']:+.4f} "
            f"z={stats['z']:+.2f} fill={stats['fill']:.1%} cost={stats['cost_bps']:6.3f}bps"
        )
    if resultat["independance"]["collisions"]:
        print("COLLISIONS: " + ", ".join(resultat["independance"]["collisions"]))
    print(f"posture_macro={resultat['posture_macro']}")
    print(
        f"independance={'OUI' if resultat['independance']['independantes'] else 'NON'} "
        f"comportements_distincts={len(resultat['classes_equivalence'])} "
        f"gagnants={len(resultat['gagnantes'])} "
        f"gagnants_distincts={resultat['gagnantes_distinctes']}"
    )
    if resultat["axes_inertes"]:
        print("AXES INERTES: " + ", ".join(resultat["axes_inertes"]))
    for cle, chemin in sorties.items():
        print(f"{cle}: {chemin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
