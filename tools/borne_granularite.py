#!/usr/bin/env python
"""Borne a partir de laquelle une serie porte VRAIMENT son unite de temps.

Pourquoi ce fichier existe
--------------------------
Le fichier ``results/barres/H4/DJ30.fs.parquet`` declare ``timeframe = "H4"``
sur ses 11 881 lignes. Les 2 729 premieres, du 11/10/2008 au 20/11/2018, sont
en realite des barres JOURNALIERES. La metadonnee ment, et rien ne le signale.

Ce n'est pas un defaut de l'archiveur. Interroge sur novembre 2009,
``copy_rates_range`` rend les MEMES sept barres journalieres qu'on demande D1,
H4 ou H1 : le courtier n'a pas d'historique intraday si loin et sert la serie
journaliere sous l'etiquette demandee. Verifie le 22/08/2026 sur DJ30.fs.
EURNZD, lui, rend bien 36 barres H4 authentiques sur la meme fenetre — le
comportement depend du symbole, ce qui interdit toute regle globale.

Pourquoi c'est grave meme si rien n'est casse aujourd'hui
----------------------------------------------------------
Un ATR calcule sur un melange de barres 4 h et 24 h additionne deux echelles de
volatilite. Le resultat n'est pas bruite, il est faux.

Mesure du 23/08/2026 : **aucun calcul n'est atteint aujourd'hui**. La distance
de stop vient de l'ATR du LTF (``builder.py`` : ``"atr": atr_ltf``), donc
``r_unit``, la taille et ``cost_r`` ne touchent jamais le HTF. Et la fenetre
glissante de 400 barres HTF n'atteint la zone journaliere sur AUCUN des 148
symboles chargeables, parce que le M15 est plafonne a 100 000 barres et demarre
donc bien apres la bascule.

C'est exactement ce qui rend le defaut dangereux : il est inerte, donc invisible,
et le premier changement qui approfondit le LTF ou raccourcit la fenetre HTF le
reveille en silence. On mesure maintenant pour que ce jour-la, le garde-fou
existe deja.

Ce module MESURE, il ne filtre pas
-----------------------------------
Aucune purge, aucune reecriture, aucune barre inventee. Il publie une borne,
comme ``borne_barres_utiles.py`` publie ``index_premiere_utile``.

Il n'appartient PAS a ``FICHIERS_MOTEUR`` et n'importe rien qui en fasse
partie : le lancer ne perime aucun artefact et ne relance aucun rejeu.

Le critere
----------
Une barre est declaree trop grossiere si l'ecart qui la separe de la precedente
ET celui qui la separe de la suivante valent tous deux un multiple exact de
24 h. Exiger DEUX ecarts et non un seul est ce qui distingue une serie
journaliere d'un jour ferie isole : un pont produit un grand ecart unique, une
serie journaliere en produit une chaine.

La borne publiee est l'index de la premiere barre apres la DERNIERE barre
grossiere. Prendre la premiere barre fine ne suffirait pas : les deux
granularites s'entrelacent parfois sur la zone de transition.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

DOSSIER = RACINE / "results" / "barres"
SORTIE = RACINE / "results" / "bornes_granularite.json"

#: Pas nominal, en secondes. D1 et au-dela n'ont pas de granularite a verifier.
PAS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}

JOUR = 86400


def _temps(chemin: Path) -> np.ndarray:
    t = np.asarray([int(x) for x in pq.read_table(
        chemin, columns=["time_utc"]).to_pydict()["time_utc"]], dtype=np.int64)
    t.sort()
    return t


def borne(temps: np.ndarray) -> dict:
    """Index de la premiere barre a granularite fiable, et le detail."""
    n = int(temps.size)
    if n < 4:
        return {"index_premiere_fine": 0, "barres_grossieres": 0,
                "barres_totales": n, "part": 0.0,
                "premiere_date_fine": None, "derniere_date_grossiere": None}

    d = np.diff(temps)
    jour = (d % JOUR == 0) & (d >= JOUR)
    # Une barre i (1 <= i <= n-2) est grossiere si ses DEUX ecarts le sont.
    grossiere = np.zeros(n, dtype=bool)
    grossiere[1:-1] = jour[:-1] & jour[1:]
    total = int(grossiere.sum())

    if total == 0:
        idx = 0
        derniere = None
    else:
        idx = int(np.flatnonzero(grossiere)[-1]) + 1
        derniere = int(temps[np.flatnonzero(grossiere)[-1]])

    def _iso(ts):
        return (None if ts is None else
                datetime.fromtimestamp(int(ts), timezone.utc).isoformat())

    return {
        "index_premiere_fine": idx,
        "barres_grossieres": total,
        "barres_totales": n,
        "part": round(total / n, 6) if n else 0.0,
        "premiere_date_fine": _iso(temps[idx]) if idx < n else None,
        "derniere_date_grossiere": _iso(derniere),
    }


def mesurer(timeframes: list[str]) -> dict:
    symboles: dict[str, dict] = {}
    for tf in timeframes:
        dossier = DOSSIER / tf
        if not dossier.is_dir():
            continue
        for chemin in sorted(dossier.glob("*.parquet")):
            symboles.setdefault(chemin.stem, {})[tf] = borne(_temps(chemin))
    return {
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "critere": ("barre grossiere = ecart precedent ET suivant multiples "
                    "exacts de 86400 s ; deux ecarts exiges pour ne pas "
                    "confondre une serie journaliere avec un jour ferie isole"),
        "borne": ("index_premiere_fine = index suivant la DERNIERE barre "
                  "grossiere, car les granularites s'entrelacent a la "
                  "transition"),
        "note": ("MESURE seulement. Aucune barre supprimee, aucune valeur "
                 "recalculee. Le brut reste brut."),
        "timeframes": list(timeframes),
        "symboles": symboles,
    }


def _resume(rapport: dict) -> None:
    tfs = rapport["timeframes"]
    print(f"{'UT':<4} {'touches':>8} {'/ total':>8} {'barres grossieres':>19}"
          f" {'part max':>9}")
    print("-" * 54)
    for tf in tfs:
        lignes = [v[tf] for v in rapport["symboles"].values() if tf in v]
        touches = [x for x in lignes if x["barres_grossieres"]]
        total = sum(x["barres_grossieres"] for x in touches)
        pmax = max((x["part"] for x in touches), default=0.0)
        print(f"{tf:<4} {len(touches):>8} {len(lignes):>8} {total:>19} "
              f"{pmax * 100:>8.2f}%")
    print()
    print("=== dix plus fortes parts, toutes UT confondues ===")
    plat = [(s, tf, v[tf]) for s, v in rapport["symboles"].items() for tf in tfs
            if tf in v and v[tf]["barres_grossieres"]]
    for s, tf, x in sorted(plat, key=lambda z: -z[2]["part"])[:10]:
        fin = (x["derniere_date_grossiere"] or "")[:10]
        print(f"  {s:<14} {tf:<3} {x['barres_grossieres']:>6} barres "
              f"({x['part'] * 100:5.2f} %)  fiable a partir de l'index "
              f"{x['index_premiere_fine']:>6}  soit apres {fin}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--timeframes", nargs="*", default=list(PAS))
    ap.add_argument("--sortie", type=Path, default=SORTIE)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rapport = mesurer(args.timeframes)
    _resume(rapport)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(rapport, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"\nborne publiee : {args.sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
