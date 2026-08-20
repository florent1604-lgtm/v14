"""Structure de correlation de l'univers : blocs, representants, avance-retard.

Pourquoi ce fichier existe
--------------------------
V14 traite jusqu'a 149 symboles comme autant de paris independants. Ils ne le
sont pas : EURUSD, GBPUSD et AUDUSD sont trois expressions du meme dollar, et
prendre les trois est un seul trade a triple taille — un risque de concentration
qui ne se voit dans aucun compteur de position.

Deux questions distinctes, souvent confondues :

1. **Qui bouge ensemble ?** Correlation contemporaine. Elle sert au RISQUE
   (ne pas empiler trois fois le meme pari) et a la LISIBILITE (un representant
   par bloc suffit a lire le marche).
2. **Qui bouge en premier ?** Avance-retard. Elle seule serait exploitable comme
   signal, et c'est la plus fragile : sur un flux de courtier, une correlation
   decalee d'une barre est le plus souvent une cotation qui trainait, pas une
   information. Le module la mesure et la rapporte AVEC son piege.

Il LIT l'archive de barres v2. Aucun ordre, aucun parametre de strategie.

Usage
-----
    python tools/correlations_univers.py --timeframe H1
    python tools/correlations_univers.py --timeframe M15 --depuis 2025-01-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.data.archive_barres import charger_barres, inventaire  # noqa: E402

DEST = RACINE / "results" / "correlations"

#: En deca, une correlation mesuree sur des heures d'ouverture differentes ne
#: veut rien dire : deux actifs qui ne cotent jamais ensemble n'ont pas de
#: correlation, ils ont un artefact.
OBSERVATIONS_MIN = 500

#: Seuil de regroupement. 0,7 en valeur absolue : au-dela, deux actifs partagent
#: la moitie de leur variance et empiler les deux double l'exposition.
SEUIL_BLOC = 0.7

#: Au-dela, les deux series sont pratiquement le meme actif.
SEUIL_REDONDANCE = 0.9


def matrice_rendements(timeframe: str = "H1", symboles=None, depuis=None):
    """Rendements logarithmiques alignes sur l'horodatage UTC.

    Les barres sont deja en UTC dans l'archive v2 ; c'est ce qui rend cet
    alignement licite. En v1, le decalage constant datait les barres d'hiver une
    heure trop tot et melangeait donc des heures differentes sous la meme
    etiquette.
    """
    import numpy as np
    import pandas as pd

    series = {}
    for symbole in (symboles or sorted(inventaire(timeframe))):
        try:
            df = charger_barres(symbole, timeframe)
        except Exception:  # noqa: BLE001
            continue
        if depuis is not None:
            df = df[df.index >= depuis]
        if len(df) < OBSERVATIONS_MIN:
            continue
        cloture = df["close"].astype(float)
        cloture = cloture[cloture > 0]
        series[symbole] = np.log(cloture).diff()
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="all")


def blocs_correlation(corr, seuil: float = SEUIL_BLOC) -> list[list[str]]:
    """Regroupe les symboles dont la correlation absolue depasse le seuil.

    Regroupement par liaison moyenne : un bloc n'est pas une chaine de proches
    voisins, sinon deux actifs sans rapport se retrouveraient ensemble par
    transitivite.
    """
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    noms = list(corr.columns)
    if len(noms) < 2:
        return [noms] if noms else []
    m = corr.to_numpy(dtype=float, copy=True)
    m = np.nan_to_num(m, nan=0.0)
    distance = 1.0 - np.abs(m)
    np.fill_diagonal(distance, 0.0)
    distance = (distance + distance.T) / 2.0
    liens = linkage(squareform(distance, checks=False), method="average")
    etiquettes = fcluster(liens, t=1.0 - seuil, criterion="distance")
    groupes: dict[int, list[str]] = {}
    for nom, e in zip(noms, etiquettes, strict=False):
        groupes.setdefault(int(e), []).append(nom)
    return sorted(groupes.values(), key=len, reverse=True)


def representant(bloc: list[str], corr) -> str:
    """Le symbole le plus central du bloc : celui qui resume le mieux les autres.

    C'est lui qu'on lit pour savoir ou va le bloc, et lui qu'on garde si l'on
    veut une seule ligne d'exposition au lieu de cinq.
    """
    if len(bloc) == 1:
        return bloc[0]
    sous = corr.loc[bloc, bloc].abs()
    moyennes = (sous.sum(axis=1) - 1.0) / (len(bloc) - 1)
    return str(moyennes.idxmax())


def paires_redondantes(corr, seuil: float = SEUIL_REDONDANCE) -> list[tuple]:
    """Paires si liees que les traiter separement double une meme exposition."""
    noms = list(corr.columns)
    out = []
    for i, a in enumerate(noms):
        for b in noms[i + 1:]:
            r = corr.at[a, b]
            if r == r and abs(r) >= seuil:
                out.append((a, b, round(float(r), 4)))
    return sorted(out, key=lambda t: -abs(t[2]))


def facteur_marche(rendements, n_composantes: int = 3) -> dict:
    """Composantes principales des rendements : le mouvement commun du marche.

    La premiere composante est ce que « le marche » fait ; la charge d'un actif
    dessus dit a quel point il en est un bon thermometre.
    """
    import numpy as np

    # Trois pieges ici, et l'ordre des filtres est ce qui les evite.
    # 1. Remplir les NaN par zero ferait passer une seance fermee pour une
    #    seance sans mouvement.
    # 2. Juger la couverture d'une colonne sur TOUTES les barres eliminerait
    #    tout le FX, qui ne cote que 24 h sur 5 : la couverture mediane de
    #    l'univers est de 0,71, seule la crypto passerait un seuil de 0,80, et
    #    le premier facteur decrirait le bitcoin en se faisant passer pour le
    #    marche. Mesure du 20/08/2026 : le filtre naif ne laissait que 29
    #    symboles, tous cotes en continu.
    # 3. On restreint donc d'abord aux barres de SEANCE — celles ou au moins la
    #    moitie de l'univers cote — puis on juge la couverture des colonnes
    #    dans ces barres-la, ou elle est proche de 100 %.
    seance = rendements[rendements.notna().mean(axis=1) >= 0.5]
    if seance.empty:
        return {}
    x = seance.dropna(axis=1, thresh=int(0.8 * len(seance)))
    x = x.dropna(axis=0, how="any")
    if x.shape[1] < 2 or len(x) < OBSERVATIONS_MIN:
        return {}
    centre = x - x.mean()
    ecart = centre.std(ddof=0).replace(0.0, np.nan)
    reduit = (centre / ecart).dropna(axis=1, how="all").fillna(0.0)
    _, valeurs, vecteurs = np.linalg.svd(reduit.to_numpy(), full_matrices=False)
    variance = (valeurs ** 2) / max(1, (reduit.shape[0] - 1))
    part = variance / variance.sum()
    charges = {"barres_communes": int(len(reduit)),
               "symboles": int(reduit.shape[1])}
    for k in range(min(n_composantes, vecteurs.shape[0])):
        charges[f"CP{k + 1}"] = {
            "part_variance": round(float(part[k]), 4),
            "charges": {nom: round(float(v), 4) for nom, v
                        in sorted(zip(reduit.columns, vecteurs[k], strict=False),
                                  key=lambda kv: -abs(kv[1]))[:20]},
        }
    return charges


def avance_retard(rendements, couples, decalage_max: int = 4) -> list[dict]:
    """Correlation croisee a decalage : qui bouge avant qui.

    ``decalage`` positif signifie que ``a`` precede ``b``. Le seuil de bruit
    ``1,96 / racine(n)`` est indicatif : sur des milliers de paires testees, il
    faut une correction de multiplicite avant de croire une valeur isolee.
    """
    out = []
    for a, b in couples:
        if a not in rendements.columns or b not in rendements.columns:
            continue
        paire = rendements[[a, b]].dropna()
        n = len(paire)
        if n < OBSERVATIONS_MIN:
            continue
        x, y = paire[a], paire[b]
        profil = {}
        for k in range(-decalage_max, decalage_max + 1):
            r = x.corr(y.shift(-k)) if k >= 0 else x.shift(k).corr(y)
            if r == r:
                profil[k] = round(float(r), 4)
        if not profil:
            continue
        meilleur = max(profil, key=lambda k: abs(profil[k]))
        contemporain = profil.get(0, 0.0)
        out.append({
            "avance": a, "retard": b, "n": int(n),
            "profil": profil,
            "meilleur_decalage": int(meilleur),
            "correlation_meilleure": profil[meilleur],
            "correlation_contemporaine": contemporain,
            "gain_sur_contemporain": round(abs(profil[meilleur])
                                           - abs(contemporain), 4),
            "seuil_bruit": round(1.96 / (n ** 0.5), 4),
        })
    return sorted(out, key=lambda d: -d["gain_sur_contemporain"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--timeframe", default="H1")
    ap.add_argument("--depuis", default=None, help="AAAA-MM-JJ")
    ap.add_argument("--symboles", nargs="*", default=None)
    ap.add_argument("--seuil", type=float, default=SEUIL_BLOC)
    ap.add_argument("--decalage-max", type=int, default=4)
    args = ap.parse_args()

    import pandas as pd

    depuis = (pd.Timestamp(args.depuis, tz="UTC") if args.depuis else None)
    rendements = matrice_rendements(args.timeframe, args.symboles, depuis)
    if rendements.empty:
        print("aucune serie exploitable.")
        return 1
    print(f"{rendements.shape[1]} symboles, {rendements.shape[0]} barres "
          f"{args.timeframe}", flush=True)

    corr = rendements.corr(min_periods=OBSERVATIONS_MIN)
    blocs = blocs_correlation(corr, args.seuil)
    reps = {representant(b, corr): b for b in blocs}

    couples = []
    for bloc in blocs:
        if len(bloc) < 2:
            continue
        chef = representant(bloc, corr)
        couples += [(chef, autre) for autre in bloc if autre != chef]
    chefs = list(reps)
    couples += [(a, b) for i, a in enumerate(chefs) for b in chefs[i + 1:]]

    sortie = {
        "timeframe": args.timeframe,
        "depuis": str(depuis) if depuis is not None else None,
        "symboles": list(rendements.columns),
        "barres": int(rendements.shape[0]),
        "seuil_bloc": args.seuil,
        "blocs": [{"representant": representant(b, corr), "membres": b,
                   "taille": len(b)} for b in blocs],
        "paires_redondantes": paires_redondantes(corr)[:60],
        "facteurs": facteur_marche(rendements),
        "avance_retard": avance_retard(rendements, couples,
                                       args.decalage_max)[:60],
        "ecrit_le": datetime.now(timezone.utc).isoformat(),
    }
    DEST.mkdir(parents=True, exist_ok=True)
    cible = DEST / f"structure_{args.timeframe}.json"
    cible.write_text(json.dumps(sortie, indent=1), encoding="utf-8")
    corr.to_csv(DEST / f"correlation_{args.timeframe}.csv")

    print(f"\n{len(blocs)} blocs au seuil {args.seuil}")
    for b in sortie["blocs"][:15]:
        print(f"  [{b['taille']:>3}] {b['representant']:<12} "
              f"{', '.join(b['membres'][:8])}"
              f"{' ...' if b['taille'] > 8 else ''}")
    cp1 = sortie["facteurs"].get("CP1", {})
    if cp1:
        print(f"\nCP1 = {cp1['part_variance'] * 100:.1f} % de la variance ; "
              f"meilleurs thermometres : "
              f"{', '.join(list(cp1['charges'])[:8])}")
    print(f"\necrit : {cible}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
