#!/usr/bin/env python
"""Porte de cout : mesurer, choisir un seuil, puis le juger hors echantillon.

Le rejeu publie pour chaque trade son cout en R (`cost_r = spread / r_unit`),
connu AVANT l'entree puisque la distance de stop l'est. Un actif cher n'est pas
un mauvais actif : c'est un actif dont le stop naturel est serre par rapport au
spread. La porte de cout refuse ces trades-la, quel que soit le symbole.

La regle de mesure est la meme que celle du rejeu : le seuil est choisi sur la
CALIBRATION seule, et juge sur la VERIFICATION, jamais utilisee pour choisir.
Un seuil choisi sur la verification ne mesurerait plus rien.

Lecture seule sur les artefacts. N'appartient pas a ``FICHIERS_MOTEUR``.

Usage :
    python tools/porte_cout.py                 # cache si besoin, puis mesure
    python tools/porte_cout.py --refaire-cache
    python tools/porte_cout.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

BRUT = RACINE / "results" / "rejeu_univers_brut"
CACHE = RACINE / "results" / "porte_cout_trades.parquet"
SORTIE = RACINE / "results" / "porte_cout.json"

#: Grille de seuils. Volontairement fixe et publiee : un seuil choisi apres
#: coup dans une grille ouverte n'est plus un seuil, c'est un resultat.
GRILLE = (0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30, 0.40, 0.60)

#: Sous cet effectif, une cellule n'est pas concluante.
EFFECTIF_MIN = 60

COLONNES = ("symbol", "asset_class", "split", "cost_r", "gross_r", "net_r",
            "r_unit")


def extraire(brut: Path = BRUT, cache: Path = CACHE) -> pd.DataFrame:
    """Extrait les seules colonnes utiles des trades bruts, une fois pour toutes.

    Les artefacts pesent 2,4 Go : relire l'integralite du JSON a chaque essai de
    seuil transformerait une mesure de trente secondes en corvee de dix minutes,
    et decouragerait justement le fait de reverifier.
    """
    lignes: list[tuple] = []
    for dossier in sorted(p for p in Path(brut).iterdir() if p.is_dir()):
        fichier = dossier / "trades.ndjson"
        if not fichier.is_file():
            continue
        with fichier.open("r", encoding="utf-8") as flux:
            for ligne in flux:
                if not ligne.strip():
                    continue
                try:
                    trade = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                lignes.append(tuple(trade.get(colonne) for colonne in COLONNES))
    trades = pd.DataFrame(lignes, columns=list(COLONNES))
    cache.parent.mkdir(parents=True, exist_ok=True)
    trades.to_parquet(cache, index=False)
    return trades


def charger(cache: Path = CACHE, brut: Path = BRUT,
            refaire: bool = False) -> pd.DataFrame:
    if refaire or not Path(cache).is_file():
        return extraire(brut, cache)
    return pd.read_parquet(cache)


def _cellule(serie: pd.Series) -> dict:
    valeurs = serie.to_numpy(dtype=float)
    if valeurs.size == 0:
        return {"n": 0, "esperance_r": None, "somme_r": None,
                "erreur_type": None}
    esperance = float(valeurs.mean())
    erreur = float(valeurs.std(ddof=1) / np.sqrt(valeurs.size)) \
        if valeurs.size > 1 else None
    return {"n": int(valeurs.size), "esperance_r": round(esperance, 4),
            "somme_r": round(float(valeurs.sum()), 1),
            "erreur_type": round(erreur, 4) if erreur is not None else None}


def courbe(trades: pd.DataFrame, grille=GRILLE) -> list[dict]:
    """Espérance nette par seuil, sur chaque segment."""
    lignes = []
    for seuil in (None, *grille):
        retenus = trades if seuil is None else trades[trades["cost_r"] < seuil]
        ligne = {"seuil": seuil,
                 "part_conservee": round(len(retenus) / max(len(trades), 1), 4)}
        for segment in ("calibration", "verification"):
            ligne[segment] = _cellule(
                retenus.loc[retenus["split"] == segment, "net_r"])
        lignes.append(ligne)
    return lignes


def choisir(lignes: list[dict], *, effectif_min: int = EFFECTIF_MIN,
            part_min: float = 0.0, critere: str = "esperance") -> dict | None:
    """Meilleur seuil AU SENS DE LA CALIBRATION SEULE.

    La verification n'entre pas dans le choix : elle sert a le juger. Un seuil
    choisi sur les deux segments ne se distingue plus d'un surajustement.

    Deux criteres, et le choix entre eux est un arbitrage, pas une mesure :

    * ``esperance`` maximise le R moyen par trade. La courbe etant monotone,
      il choisit presque toujours le point le plus serre de la grille -- une
      solution de coin, qui achete de la qualite avec du volume ;
    * ``somme`` maximise le R total accumule sur le segment. Il tient compte du
      fait qu'un systeme qui ne prend presque plus de trades ne gagne presque
      plus rien, meme avec une esperance flatteuse.
    """
    if critere not in ("esperance", "somme"):
        raise ValueError(f"critere inconnu: {critere}")
    cle = "esperance_r" if critere == "esperance" else "somme_r"
    candidats = [ligne for ligne in lignes
                 if ligne["seuil"] is not None
                 and ligne["calibration"]["n"] >= effectif_min
                 and ligne["part_conservee"] >= part_min
                 and ligne["calibration"][cle] is not None]
    if not candidats:
        return None
    return max(candidats, key=lambda ligne: ligne["calibration"][cle])


def mesurer(trades: pd.DataFrame, *, grille=GRILLE,
            part_min: float = 0.0, critere: str = "esperance") -> dict:
    globale = courbe(trades, grille)
    par_classe = {}
    for classe, sous in trades.groupby("asset_class"):
        lignes = courbe(sous, grille)
        par_classe[str(classe)] = {
            "n": int(len(sous)),
            "courbe": lignes,
            "choix": choisir(lignes, part_min=part_min, critere=critere),
        }
    return {
        "schema_version": 1,
        "mesure_le": datetime.now(timezone.utc).isoformat(),
        "trades": int(len(trades)),
        "symboles": int(trades["symbol"].nunique()),
        "grille": list(grille),
        "part_min": part_min,
        "critere": critere,
        "globale": {"courbe": globale,
                    "choix": choisir(globale, part_min=part_min,
                                     critere=critere)},
        "par_classe": par_classe,
    }


def _table(lignes: list[dict]) -> str:
    entete = (f"{'seuil':>7}{'part':>8}{'n calib':>10}{'net calib':>11}"
              f"{'n verif':>10}{'net VERIF':>11}{'somme VERIF':>13}")
    sorties = [entete]
    for ligne in lignes:
        seuil = "aucun" if ligne["seuil"] is None else f"{ligne['seuil']:.2f}"
        cal, ver = ligne["calibration"], ligne["verification"]
        sorties.append(
            f"{seuil:>7}{100 * ligne['part_conservee']:>7.1f}%"
            f"{cal['n']:>10}{cal['esperance_r'] or 0:>+11.4f}"
            f"{ver['n']:>10}{ver['esperance_r'] or 0:>+11.4f}"
            f"{ver['somme_r'] or 0:>+13.1f}")
    return "\n".join(sorties)


def resumer(rapport: dict) -> str:
    lignes = [f"porte de cout : {rapport['trades']} trades, "
              f"{rapport['symboles']} symboles",
              "", "univers entier", _table(rapport["globale"]["courbe"])]
    choix = rapport["globale"]["choix"]
    if choix:
        lignes += ["", f"  seuil retenu sur la CALIBRATION : {choix['seuil']:.2f}"
                       f" -> verification {choix['verification']['esperance_r']:+.4f}"
                       f" +/- {choix['verification']['erreur_type']:.4f}"
                       f" sur {choix['verification']['n']} trades"]
    for classe, bloc in sorted(rapport["par_classe"].items()):
        lignes += ["", f"classe {classe} ({bloc['n']} trades)",
                   _table(bloc["courbe"])]
        if bloc["choix"]:
            c = bloc["choix"]
            lignes.append(
                f"  seuil calibration {c['seuil']:.2f} -> verification "
                f"{c['verification']['esperance_r']:+.4f} sur "
                f"{c['verification']['n']} trades "
                f"({100 * c['part_conservee']:.1f}% conserves)")
    return "\n".join(lignes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refaire-cache", action="store_true")
    ap.add_argument("--part-min", type=float, default=0.0,
                    help="part minimale de trades conserves pour qu'un seuil "
                         "soit eligible")
    ap.add_argument("--critere", choices=("esperance", "somme"),
                    default="esperance")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sortie", type=Path, default=SORTIE)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    trades = charger(refaire=args.refaire_cache)
    rapport = mesurer(trades, part_min=args.part_min, critere=args.critere)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(json.dumps(rapport, ensure_ascii=False, indent=1) if args.json
          else resumer(rapport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
