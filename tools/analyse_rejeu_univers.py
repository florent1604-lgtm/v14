"""Classement par actif du rejeu d'univers, et correlations reelles entre actifs.

Ce que ce module repond, et ce qu'il refuse de repondre
--------------------------------------------------------
Il repond a deux questions mesurables :

* **quels actifs portent une esperance positive** au rejeu, et cette esperance
  survit-elle du segment de calibration au segment global ;
* **quels actifs bougent ensemble**, calcule sur les barres archivees et non
  sur la poignee de clotures live — 295 clotures reparties sur 57 symboles ne
  permettent aucune correlation credible, l'archive de 54,6 M barres si.

Il refuse la troisieme : « la meilleure politique d'execution par actif ». Les
15 politiques de ``titanium/execution_sim`` sont **synthetiques**
(``data_fidelity = synthetic_l1``), sans barre historique ni symbole reel : il
n'existe aujourd'hui aucun couplage entre une politique et un actif nomme. Le
produire serait inventer un resultat.

Garde-fous
----------
Toute lecture de barres part de la **borne utile** de
``results/bornes_barres_utiles.json`` et jamais de l'index 0 : 4,13 % de
l'archive est reconstruite (``high == low``), et sur une zone plate ``_swings``
voit un extremum a chaque barre, l'ATR vaut zero et la normalisation en R
explose.

Les correlations sont calculees sur les **rendements**, pas sur les prix : deux
series de prix tendancielles correlent a 0,99 sans rien partager. Et seules les
paires ayant assez de barres communes sont rapportees.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from tools import epoque_rejeu  # noqa: E402

REJEU = RACINE / "results" / "rejeu_univers"
BARRES = RACINE / "results" / "barres"
BORNES = RACINE / "results" / "bornes_barres_utiles.json"
SORTIE = RACINE / "results" / "analyse_rejeu.json"

#: Barres communes minimales pour qu'une correlation soit rapportee.
MIN_COMMUN = 500
#: Au-dela, deux actifs sont traites comme un seul pari.
SEUIL_REDONDANCE = 0.80


#: Empreinte du moteur : deux artefacts d'empreintes differentes ne se
#: comparent pas. Un classement qui melange deux generations de moteur est un
#: artefact de version, pas une mesure.
REJEU_BRUT = RACINE / "results" / "rejeu_univers_brut"
#: Sous ce nombre d'artefacts a l'epoque courante, un classement n'est pas
#: publiable : il decrirait l'ordre d'arrivee des lots, pas l'univers.
SYMBOLES_MIN = 30


def empreinte_moteur_courante() -> str:
    """Empreinte du moteur present sur disque."""
    return epoque_rejeu.empreinte_courante()


def empreinte_moteur_artefact(symbole: str) -> str:
    """Empreinte scellee dans le manifeste brut; vide si l'artefact n'en a pas."""
    return epoque_rejeu.empreinte_artefact(REJEU_BRUT, symbole)


def epoque_de_reference(epoque: str) -> str:
    """Generation servant de filtre, selon le mot-cle ou l'empreinte donnee.

    ``dominante`` est un mode de DIAGNOSTIC, jamais un defaut : sur un dossier
    vivant, la majorite peut basculer d'une generation a l'autre pendant un
    backfill, et le classement publierait alors un changement de cohorte comme
    un changement de performance (AMEND 3 de la revue Codex, hub offset 649).
    La voie publiable est une empreinte exacte.
    """
    if epoque == "toutes":
        return ""
    if epoque == "courante":
        return empreinte_moteur_courante()
    if epoque == "dominante":
        return epoque_rejeu.epoque_reference(REJEU_BRUT)[0]
    valeur = str(epoque).strip().lower()
    if len(valeur) == 64 and all(c in "0123456789abcdef" for c in valeur):
        return valeur
    raise ValueError(
        "epoque doit valoir 'courante', 'dominante', 'toutes' ou une empreinte")


def charger_rejeu(*, epoque: str = "courante") -> tuple[list[dict], dict]:
    """Resultats du rejeu, un dict par symbole, et le tri par epoque.

    ``results/rejeu_univers`` est un dossier vivant : un backfill le remplit
    symbole par symbole pendant des heures, et les resumes de la generation
    precedente y restent jusqu'a leur reecriture. Les lire tous revient a
    classer des actifs mesures par des moteurs differents. On ne garde donc
    que les artefacts d'UNE generation : celle du moteur present sur disque
    par defaut, ou celle qu'une empreinte explicite designe.
    """
    reference = epoque_de_reference(epoque)
    courante = empreinte_moteur_courante()
    out, ecartes = [], {}
    for chemin in sorted(REJEU.glob("*.json")):
        try:
            o = json.loads(chemin.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — un fichier en cours d'ecriture n'arrete rien
            continue
        cal, glo = o.get("calibration") or {}, o.get("global") or {}
        if not isinstance(glo.get("esperance_r"), (int, float)):
            continue
        symbole = o.get("symbole") or chemin.stem
        empreinte = empreinte_moteur_artefact(symbole)
        if reference and empreinte != reference:
            ecartes[symbole] = empreinte[:16] if empreinte else "sans_manifeste"
            continue
        out.append({
            "symbole": symbole,
            "empreinte_moteur": empreinte or None,
            "esp_cal": cal.get("esperance_r"),
            "n_cal": cal.get("n"),
            "esp_glo": glo["esperance_r"],
            "n_glo": glo.get("n"),
            "winrate": glo.get("winrate"),
            "pf": glo.get("profit_factor"),
            "ecart_type": glo.get("ecart_type_r"),
        })
    tri = {
        "empreinte_moteur_courante": courante[:16],
        "epoque_retenue": reference[:16] if reference else "toutes",
        "arbre_correspond_au_corpus": bool(reference) and reference == courante,
        "retenus": len(out),
        "ecartes": len(ecartes),
        "epoques_ecartees": sorted(set(ecartes.values())),
        "epoque_demandee": epoque,
        # Une generation choisie a la majorite d'un dossier vivant n'est pas
        # une cohorte scellee : la lecture reste diagnostique, jamais metier.
        "statut": "ANALYSIS_PARTIAL" if epoque in ("dominante", "toutes")
                  else "MESURE",
        "usage": ("diagnostic seulement : generation choisie a la majorite "
                  "d'un dossier vivant" if epoque == "dominante" else
                  "diagnostic seulement : generations melangees"
                  if epoque == "toutes" else
                  "cohorte d'une generation unique et declaree"),
    }
    return out, tri


def bornes() -> dict:
    if not BORNES.is_file():
        return {}
    return json.loads(BORNES.read_text(encoding="utf-8")).get("symboles", {})


def rendements(symbole: str, tf: str, depuis_index: int) -> tuple[np.ndarray, np.ndarray]:
    """``(horodatages, rendements logarithmiques)`` a partir de la borne utile."""
    chemin = BARRES / tf / f"{symbole}.parquet"
    if not chemin.is_file():
        return np.array([]), np.array([])
    t = pq.read_table(chemin, columns=["time_utc", "close"])
    ts = t.column("time_utc").to_numpy()[depuis_index:]
    close = t.column("close").to_numpy()[depuis_index:].astype(float)
    if close.size < 2:
        return np.array([]), np.array([])
    # Rendements log : additifs, et insensibles au niveau de prix — deux series
    # tendancielles correlent a 0,99 en niveau sans rien partager.
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.diff(np.log(close))
    ok = np.isfinite(r)
    return ts[1:][ok], r[ok]


def correlations(symboles: list[str], tf: str, b: dict) -> list[tuple[str, str, float, int]]:
    series: dict[str, dict[int, float]] = {}
    for s in symboles:
        idx = 0
        m = (b.get(s) or {}).get(tf) or {}
        if isinstance(m.get("barres_rejetees"), int):
            idx = m["barres_rejetees"]
        ts, r = rendements(s, tf, idx)
        if r.size >= MIN_COMMUN:
            series[s] = dict(zip(ts.tolist(), r.tolist(), strict=True))
    noms = sorted(series)
    paires = []
    for i, a in enumerate(noms):
        for c in noms[i + 1:]:
            communs = series[a].keys() & series[c].keys()
            if len(communs) < MIN_COMMUN:
                continue
            cle = sorted(communs)
            x = np.fromiter((series[a][k] for k in cle), float)
            y = np.fromiter((series[c][k] for k in cle), float)
            sx, sy = x.std(), y.std()
            if sx == 0 or sy == 0:
                continue
            rho = float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))
            if math.isfinite(rho):
                paires.append((a, c, round(rho, 4), len(cle)))
    return paires


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tf", default="H1", help="timeframe des correlations")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument(
        "--epoque", default="courante",
        help="'courante' (defaut) exige le moteur present sur disque; une "
             "empreinte de 64 hexa epingle une generation precise et c'est la "
             "voie publiable quand l'arbre de travail a bouge; 'dominante' "
             "prend la generation majoritaire d'un dossier vivant et 'toutes' "
             "melange les generations: ces deux modes sont diagnostiques et "
             "publient ANALYSIS_PARTIAL",
    )
    ap.add_argument(
        "--min-symboles", type=int, default=SYMBOLES_MIN,
        help="plancher de symboles sous lequel le classement n'est pas publie",
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    R, tri = charger_rejeu(epoque=args.epoque)
    print(f"rejeu : {len(R)} symboles termines a l'epoque {args.epoque} "
          f"(generation retenue {tri['epoque_retenue']}, arbre de travail "
          f"{tri['empreinte_moteur_courante']})")
    if tri["statut"] == "ANALYSIS_PARTIAL":
        print(f"  ANALYSIS_PARTIAL — {tri['usage']}. Ne pas publier ce "
              "classement comme une mesure; epingler une empreinte exacte "
              "pour cela.")
    if not tri["arbre_correspond_au_corpus"] and tri["epoque_retenue"] != "toutes":
        print("  NOTE: l'arbre de travail n'est pas la generation mesuree. "
              "L'ecart est permis, il est publie.")
    if tri["ecartes"]:
        print(f"  {tri['ecartes']} artefacts d'une AUTRE generation ecartes "
              f"{tri['epoques_ecartees']} — un backfill est probablement en cours")
    print()
    if len(R) < args.min_symboles:
        print(f"REFUS: {len(R)} symboles a l'epoque courante, plancher "
              f"{args.min_symboles}. Un classement sur cet echantillon "
              "decrirait l'ordre des lots, pas l'univers. Attendre la fin du "
              "backfill, ou forcer avec --min-symboles.")
        return 2

    positifs = [r for r in R if r["esp_glo"] > 0]
    esp = [r["esp_glo"] for r in R]
    print(f"esperance globale : mediane {st.median(esp):+.4f}R | "
          f"positifs {len(positifs)}/{len(R)}")

    survivants = [r for r in R
                  if r["esp_glo"] > 0 and isinstance(r["esp_cal"], (int, float))
                  and r["esp_cal"] > 0]
    print(f"positifs EN CALIBRATION ET EN GLOBAL : {len(survivants)}/{len(R)}"
          "   <- les seuls defendables\n")

    print(f"{'symbole':<14}{'calib':>10}{'global':>10}{'ecart':>9}{'win':>8}{'PF':>7}{'n':>7}")
    for r in sorted(survivants, key=lambda x: -x["esp_glo"])[:args.top]:
        print(f"{r['symbole']:<14}{r['esp_cal']:>+10.4f}{r['esp_glo']:>+10.4f}"
              f"{r['esp_glo'] - r['esp_cal']:>+9.4f}{100 * (r['winrate'] or 0):>7.1f}%"
              f"{r['pf'] or 0:>7.2f}{r['n_glo'] or 0:>7}")

    noms = [r["symbole"] for r in survivants]
    print(f"\ncorrelations {args.tf} entre les {len(noms)} survivants "
          f"(rendements log, depuis la borne utile)")
    paires = correlations(noms, args.tf, bornes())
    forts = [p for p in paires if abs(p[2]) >= SEUIL_REDONDANCE]
    print(f"  paires evaluees : {len(paires)} | "
          f"redondantes (|rho| >= {SEUIL_REDONDANCE}) : {len(forts)}")
    for a, c, rho, n in sorted(forts, key=lambda p: -abs(p[2]))[:15]:
        print(f"    {a:<12} {c:<12} rho {rho:+.3f}  sur {n} barres")

    # Les paires redondantes se transitent : si A~B et B~C, les trois portent le
    # meme pari meme si A et C ne se croisent pas au-dela du seuil. On regroupe
    # donc par composantes connexes — c'est le nombre de GRAPPES, et non le
    # nombre d'actifs, qui borne le risque reellement diversifie.
    parent = {s: s for s in noms}

    def racine(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, c, _, _ in forts:
        ra, rc = racine(a), racine(c)
        if ra != rc:
            parent[ra] = rc
    grappes: dict[str, list[str]] = {}
    for s in noms:
        grappes.setdefault(racine(s), []).append(s)
    groupes = sorted(grappes.values(), key=len, reverse=True)
    print(f"\n  -> {len(noms)} actifs se reduisent a {len(groupes)} paris independants")
    for g in groupes:
        if len(g) > 1:
            meilleur = max(g, key=lambda s: next(
                r["esp_glo"] for r in survivants if r["symbole"] == s))
            print(f"    grappe de {len(g)} : {', '.join(sorted(g))}"
                  f"   -> garder {meilleur}")

    SORTIE.write_text(json.dumps({
        "grappes": groupes,
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "timeframe_correlation": args.tf,
        "epoque": tri,
        "rejeu": R,
        "survivants": [r["symbole"] for r in survivants],
        "paires_redondantes": [
            {"a": a, "b": c, "rho": rho, "barres": n} for a, c, rho, n in forts],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nrapport : {SORTIE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
