"""La selection par esperance mesuree survit-elle hors echantillon ?

CE QUI EST MESURE
-----------------
Le journal live reel est coupe par le TEMPS en deux fenetres disjointes. La
premiere (in-sample) classe les symboles par esperance mesuree ; la seconde
(out-of-sample), que le classement n'a jamais vue, le juge. C'est la seule
question qui decide si une cohorte choisie « au mesure » achete quelque chose :
sur la fenetre d'ou vient le choix, un classement gagne par construction.

La regle jugée ici est celle que `titanium/execution/demo_cohort.py` enonce :
tout symbole mesure positif en IS est retenu, tout symbole mesure negatif est
exclu, le reste va au non mesure. L'outil la rend EXECUTABLE et la confronte a
deux comparateurs :

  * tout          l'esperance OOS de l'ensemble — le comparateur naif, celui de
                  qui ne selectionne rien ;
  * le null       ``--tirages`` selections aleatoires de la MEME TAILLE dans le
                  MEME vivier, au seed de reference. Sans lui, une selection qui
                  perd moins que tout se lirait comme un gain : c'est
                  exactement l'erreur que ce lot met au jour.

Les trois compartiments de la regle sont chiffres separement dans la fenetre
OOS — retenus, exclus, non mesures — parce que le cout d'une cohorte se lit
la, et pas dans le classement qui l'a produite.

UN RAPPORT COMPLET N'EST PAS UN RAPPORT CONCLUANT
--------------------------------------------------
Trois etats produisent un document rempli qui ne dit rien : une fenetre de
jugement vide (``--part-is 1.0``), une regle qui ne retient aucun symbole
(``--min-n`` plus grand que le vivier), et un null trop court pour distinguer le
seuil du hasard (``--tirages 1`` rend ``p=0,0`` — donc « effet au-dela du
hasard » — sur des donnees de hasard). Dans ces trois cas l'outil ecrit
``AUCUN VERDICT`` et son motif : un verdict absent ne doit pas se lire comme un
verdict.

Les axes d'allocation (classe d'actif, cote, motif de sortie) sont rendus plus
bas, sans test : ce qui les rend lisibles est le SIGNE CONSERVE d'une fenetre a
l'autre, avec les effectifs des deux cotes. Un axe dont le signe se retourne
n'est pas un axe, c'est une fenetre.

CE QU'ELLE NE PERMET PAS DE CONCLURE
------------------------------------
Une esperance passee n'est pas un edge futur. Le test dit si la REGLE a battu le
hasard sur cet echantillon, pas si elle le battra au suivant. Le journal est
live, une seule place (Axi DEMO), et les effectifs par symbole sont petits : un
symbole a huit clotures est compte, pas juge. Le motif de sortie n'est pas un
levier d'allocation mais une CONSEQUENCE : un trade sort en `trailing` parce
qu'il a travaille, donc la ligne `trailing` est positive par construction et ne
se lit pas comme un edge. Rien ici ne modifie une cohorte, un seuil ni une
politique — l'outil mesure et rend le differentiel a l'operateur.

Le journal est VIVANT : chaque chiffre porte la fenetre et l'effectif affiches,
et deux executions a des instants differents ne donnent pas les memes totaux.
Citer un chiffre sans sa fenetre et son N n'est donc pas le reproduire.

    .venv\\Scripts\\python.exe -X utf8 tools/optimiser_allocation_cohorte.py
    .venv\\Scripts\\python.exe -X utf8 tools/optimiser_allocation_cohorte.py \\
        --cohorte EURUSD,AUDUSD,BTCUSD --json results/allocation_cohorte.json
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#: Le journal live. `results/` n'etant pas versionne, l'outil le dit s'il manque.
JOURNAL = RACINE / "results" / "trades.ndjson"

#: Part du journal qui sert a classer. Le reste juge.
PART_IS = 0.6

#: Effectif minimal dans une fenetre pour qu'une valeur soit classee ou jugee.
MIN_N = 8

#: Nombre de tirages du null.
TIRAGES = 1000

#: Le seed de reference des artefacts de cette campagne. Le nommer ici evite
#: qu'un « seed 7 » de test unitaire se relise un jour comme celui des mesures.
SEED = 14082026

#: Convention de RAPPORT, pas une politique : le p du null est rendu tel quel,
#: et cette borne ne sert qu'a formuler la phrase du verdict.
SEUIL_RAPPORT = 0.05

#: Plancher de tirages. Un null de `t` tirages ne sait pas exprimer un p plus fin
#: que `1/(t+1)` : sous ce plancher, la phrase du verdict porterait sur une
#: resolution trop grossiere pour distinguer ce seuil du hasard — et a `t=1`,
#: n'importe quelle donnee rend `p=0,0` ou `p=1,0`. Le plancher demande dix pas a
#: l'interieur du seuil, ce qui le derive de `SEUIL_RAPPORT` au lieu de le poser.
TIRAGES_MIN = int(10 / SEUIL_RAPPORT) - 1


def lire_journal(chemin: Path) -> list[dict]:
    """Clotures du journal, triees par instant de cloture. Lignes illisibles ecartees."""
    trades = []
    for ligne in chemin.read_text(encoding="utf-8", errors="replace").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            trade = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if not isinstance(trade, dict):
            continue
        if isinstance(trade.get("pnl_r"), (int, float)) and trade.get("closed_at"):
            trades.append(trade)
    trades.sort(key=lambda t: str(t["closed_at"]))
    return trades


def _champ(trade: dict, rang: int) -> str:
    """Le champ `rang` du contexte `SYMBOLE|cote|regime|timeframe`."""
    parties = str(trade.get("context", "")).split("|")
    return parties[rang] if len(parties) > rang else ""


def symbole(trade: dict) -> str:
    return _champ(trade, 0)


def cote(trade: dict) -> str:
    return _champ(trade, 1)


def classe(trade: dict) -> str:
    return str(trade.get("asset_class") or "?")


def sortie(trade: dict) -> str:
    return str(trade.get("exit_reason") or "?")


def esperance(trades: list[dict]) -> float | None:
    """Esperance en R, ou ``None`` sur un echantillon vide."""
    return sum(t["pnl_r"] for t in trades) / len(trades) if trades else None


def euros(trades: list[dict]) -> float:
    """Le meme resultat en euros : ``pnl_r`` x risque engage, quand il est connu."""
    return sum(t["pnl_r"] * float(t.get("risk_money") or 0.0) for t in trades)


def couper(trades: list[dict], part: float = PART_IS) -> tuple[list[dict], list[dict]]:
    """Deux fenetres disjointes, coupees par le TEMPS (le journal est deja trie)."""
    coupe = int(len(trades) * part)
    return trades[:coupe], trades[coupe:]


def classer(trades: list[dict], cle: Callable[[dict], str], min_n: int = MIN_N) -> list[tuple]:
    """Valeurs de l'axe classees par esperance, du meilleur au pire."""
    groupes: dict[str, list[dict]] = collections.defaultdict(list)
    for trade in trades:
        groupes[cle(trade)].append(trade)
    notes = [(v, esperance(ts), len(ts)) for v, ts in groupes.items() if len(ts) >= min_n]
    notes.sort(key=lambda x: -x[1])
    return notes


def null_esperances(
    oos: list[dict],
    vivier: list[str],
    taille: int,
    tirages: int = TIRAGES,
    seed: int = SEED,
) -> list[float]:
    """Esperance OOS de `tirages` selections aleatoires de `taille` symboles."""
    par_symbole: dict[str, list[dict]] = collections.defaultdict(list)
    for trade in oos:
        par_symbole[symbole(trade)].append(trade)
    rng = random.Random(seed)
    trie = sorted(vivier)
    taille = min(taille, len(trie))
    if taille <= 0:
        return []
    resultats = []
    for _ in range(tirages):
        trades = [t for s in rng.sample(trie, taille) for t in par_symbole.get(s, ())]
        valeur = esperance(trades)
        if valeur is not None:
            resultats.append(valeur)
    return resultats


def queue_haute(valeurs: list[float], observe: float) -> float | None:
    """Part du null au moins aussi bonne que l'observe."""
    if not valeurs:
        return None
    return sum(1 for v in valeurs if v >= observe) / len(valeurs)


def motif_non_jugeable(selection: dict) -> str | None:
    """Pourquoi le verdict NE PEUT PAS etre rendu, ou ``None`` s'il peut l'etre.

    Proprietaire unique de cette decision : l'impression et le JSON la lisent, et
    aucun des deux ne la refait. Un null court reste chiffre — ses nombres sont
    vrais — mais il ne conclut pas.
    """
    if not selection["retenus"]:
        return ("aucun symbole retenu : la regle ne selectionne rien dans cette "
                "fenetre de classement — elargir la fenetre (--part-is) ou "
                "abaisser --min-n")
    if selection["oos_retenus"]["n"] == 0:
        return ("la fenetre de jugement ne contient aucun trade des symboles "
                "retenus : augmenter la part de jugement (--part-is)")
    tirages = selection["null"]["tirages"]
    if tirages < TIRAGES_MIN:
        return (f"null court : {tirages} tirage(s), plancher {TIRAGES_MIN} — "
                f"sans lui la resolution vaut 1/(t+1) et ne separe pas "
                f"{SEUIL_RAPPORT} du hasard (relancer avec --tirages {TIRAGES_MIN})")
    return None


def profil_de_compte(trades: list[dict]) -> dict:
    """Un lot de trades en une ligne : effectif, R, PF, euros."""
    gains = [t["pnl_r"] for t in trades if t["pnl_r"] > 0]
    pertes = [t["pnl_r"] for t in trades if t["pnl_r"] < 0]
    total = sum(t["pnl_r"] for t in trades)
    return {
        "n": len(trades),
        "symboles": len({symbole(t) for t in trades}),
        "debut": str(trades[0]["closed_at"])[:19] if trades else "",
        "fin": str(trades[-1]["closed_at"])[:19] if trades else "",
        "total_r": round(total, 4),
        "esperance_r": round(total / len(trades), 6) if trades else None,
        "pf": round(sum(gains) / abs(sum(pertes)), 4) if pertes else None,
        "euros": round(euros(trades), 2),
    }


def compartiment(oos: list[dict], valeurs: set[str]) -> dict:
    """Ce qu'une categorie de symboles a REELLEMENT fait dans la fenetre OOS."""
    trades = [t for t in oos if symbole(t) in valeurs]
    return {
        "symboles": len(valeurs),
        "n": len(trades),
        "esperance_r": round(esperance(trades), 4) if trades else None,
        "total_r": round(sum(t["pnl_r"] for t in trades), 4),
        "euros": round(euros(trades), 2),
    }


def tableau_axe(
    is_: list[dict],
    oos: list[dict],
    cle: Callable[[dict], str],
    min_n: int = MIN_N,
) -> list[dict]:
    """Chaque valeur de l'axe : effectif et esperance dans les DEUX fenetres."""
    groupes_is: dict[str, list[dict]] = collections.defaultdict(list)
    groupes_oos: dict[str, list[dict]] = collections.defaultdict(list)
    for trade in is_:
        groupes_is[cle(trade)].append(trade)
    for trade in oos:
        groupes_oos[cle(trade)].append(trade)
    lignes = []
    for valeur in set(groupes_is) | set(groupes_oos):
        a, b = groupes_is.get(valeur, []), groupes_oos.get(valeur, [])
        e_is, e_oos = esperance(a), esperance(b)
        jugeable = len(a) >= min_n and len(b) >= min_n
        lignes.append({
            "valeur": valeur,
            "n_is": len(a), "esperance_is": round(e_is, 4) if e_is is not None else None,
            "n_oos": len(b), "esperance_oos": round(e_oos, 4) if e_oos is not None else None,
            "euros_oos": round(euros(b), 2),
            "signe_stable": bool(jugeable and e_is is not None and e_oos is not None
                                 and (e_is > 0) == (e_oos > 0)),
        })
    lignes.sort(key=lambda ligne: -(ligne["esperance_is"] or -99.0))
    return lignes


def mesurer(
    trades: list[dict],
    *,
    part_is: float = PART_IS,
    min_n: int = MIN_N,
    tirages: int = TIRAGES,
    seed: int = SEED,
    cohorte: list[str] | None = None,
) -> dict:
    """Le rapport complet, sans rien modifier : tout est calcule, rien n'est ecrit."""
    is_, oos = couper(trades, part_is)
    classement = classer(is_, symbole, min_n)
    vivier = [v for v, _, _ in classement]
    retenus = [v for v, e, _ in classement if e > 0]
    exclus = [v for v, e, _ in classement if e <= 0]
    # Les trois compartiments doivent COUVRIR le journal : un symbole qui
    # n'apparait que dans la fenetre de jugement ne doit pas disparaitre du
    # rapport faute d'avoir ete vu dans celle du classement.
    non_mesures = sorted({symbole(t) for t in trades} - set(vivier))

    selection = [t for t in oos if symbole(t) in set(retenus)]
    attendu = esperance(selection)
    # UN SEUL endroit decide de la taille du null : celle de la selection.
    # Une taille de vivier rendrait les tirages identiques, donc p=1,0 par
    # construction, et un avantage enorme passerait pour du hasard.
    taille = len(retenus)
    nul = null_esperances(oos, vivier, taille, tirages=tirages, seed=seed)
    p = queue_haute(nul, attendu) if attendu is not None else None
    nul_trie = sorted(nul)

    rapport = {
        "journal": profil_de_compte(trades),
        "coupe": {
            "part_is": part_is,
            "is": profil_de_compte(is_),
            "oos": profil_de_compte(oos),
        },
        "selection": {
            "axe": "symbole",
            "regle": "tout symbole mesure positif en IS est retenu, les mesures negatifs exclus",
            "min_n": min_n,
            "vivier": vivier,
            "classement_is": [{"valeur": v, "esperance_is": round(e, 4), "n_is": n}
                              for v, e, n in classement],
            "retenus": retenus,
            "exclus": exclus,
            "non_mesures": non_mesures,
            "oos_retenus": compartiment(oos, set(retenus)),
            "oos_exclus": compartiment(oos, set(exclus)),
            "oos_non_mesures": compartiment(oos, set(non_mesures)),
            "oos_tout": compartiment(oos, {symbole(t) for t in oos}),
            "null": {
                "tirages": len(nul), "seed": seed, "taille": taille,
                "mediane": round(nul_trie[len(nul_trie) // 2], 4) if nul_trie else None,
                "p90": round(nul_trie[int(len(nul_trie) * 0.9)], 4) if nul_trie else None,
                "p": round(p, 4) if p is not None else None,
            },
        },
        "axes": {
            nom: tableau_axe(is_, oos, cle, min_n)
            for nom, cle in (("classe", classe), ("cote", cote), ("sortie", sortie))
        },
    }

    motif = motif_non_jugeable(rapport["selection"])
    rapport["selection"]["jugement"] = {"possible": motif is None, "motif": motif}

    if cohorte is not None:
        retenue = set(cohorte)
        bloc = compartiment(trades, retenue)
        bloc["inconnus_du_journal"] = sorted(retenue - {symbole(t) for t in trades})
        bloc["oos"] = compartiment(oos, retenue)
        rapport["cohorte"] = bloc
    return rapport


def _signe(valeur: float | None) -> str:
    if valeur is None:
        return "  n/a "
    return f"{valeur:+.4f}"


def _imprimer(rapport: dict) -> None:
    journal = rapport["journal"]
    print(f"JOURNAL  {journal['debut']} -> {journal['fin']}   n={journal['n']}"
          f"   {journal['symboles']} symboles")
    print(f"         total {journal['total_r']:+.2f} R   esperance {_signe(journal['esperance_r'])} R"
          f"   PF {journal['pf']}   {journal['euros']:+.2f} EUR")

    coupe = rapport["coupe"]
    print(f"\nCOUPE PAR LE TEMPS  (part IS {coupe['part_is']})")
    for nom in ("is", "oos"):
        bloc = coupe[nom]
        print(f"  {nom.upper():4} n={bloc['n']:4}  {bloc['debut']} -> {bloc['fin']}"
              f"   esperance {_signe(bloc['esperance_r'])} R")

    sel = rapport["selection"]
    print(f"\nSELECTION PAR SYMBOLE  vivier {len(sel['vivier'])} (n>={sel['min_n']} en IS)")
    print("  classement IS : " + ", ".join(
        f"{c['valeur']} {c['esperance_is']:+.3f} ({c['n_is']})" for c in sel["classement_is"][:10]))
    print(f"  regle : {sel['regle']}  ->  {len(sel['retenus'])} retenus,"
          f" {len(sel['exclus'])} exclus, {len(sel['non_mesures'])} non mesures")
    for nom, etiquette in (("oos_retenus", "RETENUS (mesures positifs)"),
                           ("oos_exclus", "EXCLUS  (mesures negatifs)"),
                           ("oos_non_mesures", "NON MESURES (n trop petit)"),
                           ("oos_tout", "TOUT (aucune selection)")):
        bloc = sel[nom]
        print(f"  {etiquette:<28} OOS n={bloc['n']:4}  esperance"
              f" {_signe(bloc['esperance_r'])} R   total {bloc['total_r']:+8.2f} R"
              f"   {bloc['euros']:+9.2f} EUR")
    nul = sel["null"]
    print(f"  null {nul['tirages']} tirages de {len(sel['retenus'])} symboles (seed {nul['seed']}) :"
          f" mediane {_signe(nul['mediane'])} | p90 {_signe(nul['p90'])} | p={nul['p']}")
    jugement = sel["jugement"]
    if not jugement["possible"]:
        print(f"  AUCUN VERDICT : {jugement['motif']}")
    elif nul["p"] is not None:
        verdict = ("AUCUN effet mesurable : la selection ne se distingue pas du hasard"
                   if nul["p"] > SEUIL_RAPPORT
                   else "effet au-dela du hasard sur cet echantillon")
        print(f"  VERDICT : {verdict} (p={nul['p']} contre {SEUIL_RAPPORT})")

    for nom, lignes in rapport["axes"].items():
        print(f"\nAXE {nom.upper()}")
        for ligne in lignes:
            stable = "signe stable" if ligne["signe_stable"] else "signe NON stable"
            print(f"  {ligne['valeur']:<14} IS {_signe(ligne['esperance_is'])} (n={ligne['n_is']:3})"
                  f"   OOS {_signe(ligne['esperance_oos'])} (n={ligne['n_oos']:3})"
                  f"   {ligne['euros_oos']:9.2f} EUR   {stable}")

    cohorte = rapport.get("cohorte")
    if cohorte:
        print(f"\nCOHORTE FOURNIE  {cohorte['symboles']} symboles   trades {cohorte['n']}"
              f"   esperance {_signe(cohorte['esperance_r'])} R   {cohorte['euros']:+.2f} EUR")
        oos = cohorte["oos"]
        print(f"  OOS n={oos['n']}  esperance {_signe(oos['esperance_r'])} R"
              f"   inconnus du journal : {cohorte['inconnus_du_journal'] or 'aucun'}")


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        description="Mesure si la selection de symboles par esperance survit hors echantillon.")
    analyseur.add_argument("--journal", type=Path, default=JOURNAL)
    analyseur.add_argument("--part-is", type=float, default=PART_IS)
    analyseur.add_argument("--min-n", type=int, default=MIN_N)
    analyseur.add_argument("--tirages", type=int, default=TIRAGES,
                           help="tirages du null ; sous {TIRAGES_MIN} aucun verdict n'est rendu")
    analyseur.add_argument("--seed", type=int, default=SEED)
    analyseur.add_argument("--cohorte", default="",
                           help="liste de symboles separes par des virgules, a juger telle quelle")
    analyseur.add_argument("--json", type=Path, default=None,
                           help="ecrit le rapport complet a ce chemin")
    args = analyseur.parse_args(argv)

    if not args.journal.exists():
        print(f"journal introuvable : {args.journal}", file=sys.stderr)
        return 2
    trades = lire_journal(args.journal)
    if not trades:
        print(f"journal vide ou illisible : {args.journal}", file=sys.stderr)
        return 2

    cohorte = [s.strip() for s in args.cohorte.split(",") if s.strip()] or None
    rapport = mesurer(trades, part_is=args.part_is, min_n=args.min_n,
                      tirages=args.tirages, seed=args.seed, cohorte=cohorte)
    rapport["journal"]["chemin"] = str(args.journal)
    _imprimer(rapport)

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nrapport ecrit : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
