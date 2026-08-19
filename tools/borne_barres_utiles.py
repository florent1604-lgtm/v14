"""Borne de debut utilisable par symbole et timeframe, dans l'archive de barres.

Pourquoi ce fichier existe
--------------------------
L'archive de barres du courtier contient des barres RECONSTRUITES : EURUSD H4
commence au 1971-01-03, alors que l'euro n'existe pas avant 1999, avec
``high == low`` et ``tick_volume == 1`` sur les 300 premieres barres. Sur les
54,6 millions de barres archivees, 4,13 % sont plates.

Ce n'est pas cosmetique. ``_swings`` retient un extremum par EGALITE : sur une
serie plate, CHAQUE barre interieure est a la fois swing haut et swing bas.
Trois cents barres plates produisent trois cents faux swings, l'ATR y vaut zero
et toute normalisation en R explose. Un walk-forward qui demarre a l'index 0
s'entraine donc sur de la fiction, sans qu'aucune erreur ne soit levee.

Ce module MESURE, il ne filtre pas. Le brut reste brut : aucune purge, aucune
reecriture de l'archive. Il produit la borne a partir de laquelle chaque serie
devient utilisable, pour que le consommateur parte de la plutot que du debut.

Le critere, arbitre par Prime le 19/08/2026
-------------------------------------------
Le marqueur de reconstruction n'est PAS ``high == low`` seul : une vraie barre
M1 illiquide peut legitimement etre plate. C'est la CONJONCTION
``high == low ET tick_volume <= 1``.

« Disparait durablement » demande une definition operatoire, faute de quoi une
seule barre plate tardive et legitime repousserait la borne jusqu'a la fin de
la serie. Retenue ici : une barre est CONTAMINEE si elle porte la conjonction
ET si la densite locale de conjonction, sur une fenetre glissante centree,
depasse un seuil. La borne est la derniere barre contaminee, plus une. Une
barre plate isolee au milieu de vraies barres n'atteint pas la densite et ne
deplace donc pas la borne.

Usage
-----
    python tools/borne_barres_utiles.py                    # M15 et H1
    python tools/borne_barres_utiles.py --timeframes M15 H1 D1
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
ARCHIVE = RACINE / "results" / "barres"
SORTIE = RACINE / "results" / "bornes_barres_utiles.json"

#: Demi-largeur de la fenetre de densite, en barres.
FENETRE = 100
#: Densite de conjonction au-dela de laquelle la zone est jugee reconstruite.
SEUIL_DENSITE = 0.10
#: Une borne posterieure a cette date rend le symbole inexploitable en
#: walk-forward : trop peu d'historique utile en amont des trades mesures.
LIMITE_UTILISABLE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def borne_utile(high, low, volume) -> tuple[int, int]:
    """Rend ``(index de la premiere barre utile, nombre de barres rejetees)``."""
    conjonction = (high == low) & (volume <= 1)
    n = conjonction.size
    if n == 0 or not conjonction.any():
        return 0, 0
    if conjonction.all():
        return n, n

    # Densite locale par somme glissante, en O(n) plutot qu'en O(n*fenetre).
    cumul = np.concatenate(([0], np.cumsum(conjonction.astype(np.int64))))
    debut = np.maximum(np.arange(n) - FENETRE, 0)
    fin = np.minimum(np.arange(n) + FENETRE + 1, n)
    densite = (cumul[fin] - cumul[debut]) / (fin - debut)

    contaminees = np.flatnonzero(conjonction & (densite > SEUIL_DENSITE))
    if contaminees.size == 0:
        return 0, 0
    borne = int(contaminees[-1]) + 1
    return borne, borne


def mesurer(timeframe: str) -> dict:
    dossier = ARCHIVE / timeframe
    if not dossier.is_dir():
        return {}
    out: dict[str, dict] = {}
    for chemin in sorted(dossier.glob("*.parquet")):
        symbole = chemin.stem
        try:
            t = pq.read_table(chemin, columns=["time_utc", "high", "low", "tick_volume"])
        except Exception as e:  # noqa: BLE001 — un fichier illisible ne stoppe pas la mesure
            out[symbole] = {"erreur": f"{type(e).__name__}: {e}"}
            continue
        ts = t.column("time_utc").to_numpy()
        high = t.column("high").to_numpy()
        low = t.column("low").to_numpy()
        vol = t.column("tick_volume").to_numpy()
        borne, rejetees = borne_utile(high, low, vol)
        if borne >= ts.size:
            out[symbole] = {"premiere_date_utile": None, "barres_rejetees": int(rejetees),
                            "barres_totales": int(ts.size), "inexploitable": True,
                            "motif": "serie entierement reconstruite"}
            continue
        date = datetime.fromtimestamp(int(ts[borne]), tz=timezone.utc)
        inexploitable = bool(date > LIMITE_UTILISABLE)
        # Deux causes tres differentes menent a une borne tardive, et les
        # confondre induirait en erreur : une serie CONTAMINEE a des barres
        # reconstruites en tete, une serie COURTE commence simplement tard
        # parce que l'instrument est recent. Les deux sont inutilisables en
        # walk-forward, mais seule la premiere est un defaut de donnee.
        motif = None
        if inexploitable:
            motif = "serie_contaminee" if rejetees > 0 else "serie_courte"
        out[symbole] = {
            "premiere_date_utile": date.isoformat(),
            "barres_rejetees": int(rejetees),
            "barres_totales": int(ts.size),
            "barres_utiles": int(ts.size - borne),
            "inexploitable": inexploitable,
            "motif": motif,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--timeframes", nargs="*", default=["M15", "H1"])
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    resultat: dict[str, dict] = {}
    for tf in args.timeframes:
        mesures = mesurer(tf)
        print(f"{tf} : {len(mesures)} symboles", flush=True)
        for symbole, m in mesures.items():
            resultat.setdefault(symbole, {})[tf] = m

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps({
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "critere": "high == low ET tick_volume <= 1",
        "fenetre_densite": FENETRE,
        "seuil_densite": SEUIL_DENSITE,
        "limite_utilisable": LIMITE_UTILISABLE.isoformat(),
        "symboles": resultat,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nrapport : {SORTIE}")

    suspects = sorted(
        (s, tf, m["premiere_date_utile"])
        for s, par_tf in resultat.items()
        for tf, m in par_tf.items()
        if m.get("inexploitable") or m.get("premiere_date_utile") is None
    )
    print(f"\nSYMBOLES INEXPLOITABLES (borne posterieure a "
          f"{LIMITE_UTILISABLE:%Y-%m-%d}) : {len(suspects)}")
    for s, tf, d in suspects:
        motif = resultat[s][tf].get("motif") or "serie_entierement_reconstruite"
        print(f"  {s:<14} {tf:<4} {str(d)[:10]:<12} {motif}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
