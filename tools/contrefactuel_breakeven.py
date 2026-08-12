"""Contrefactuel du seuil de breakeven, borne et honnete.

POURQUOI CET OUTIL
------------------
Au 12/08/2026, 35 clotures live : esperance -0,3834 R, PF 0,361. La cause n'est
pas le taux de reussite du signal mais la GESTION : 21 trades sur 35 sortent en
phase `init`, c'est-a-dire au stop plein, alors que **9 d'entre eux etaient
deja montes en territoire favorable** (jusqu'a +0,79 R) avant de revenir. Le
breakeven est arme a +0,80 R : ils sont passes juste dessous.

CE QUE CET OUTIL PROUVE — ET CE QU'IL NE PROUVE PAS
---------------------------------------------------
Pour un trade sorti en `init`, la sortie au stop est FINALE : tout maximum
favorable journalise (`mfe_r`) a donc necessairement ete atteint AVANT. Si le
breakeven avait ete arme a un seuil inferieur ou egal a ce maximum, le trade
serait sorti autour de zero au lieu de -1 R. **Ce gain-la est une borne
inferieure sure**, au tampon de spread pres.

L'effet inverse ne se calcule PAS ici : un breakeven plus bas peut aussi
couper un trade qui serait devenu gagnant. Le journal donne `mae_r` mais pas sa
position dans le temps ; savoir si le creux precede ou suit le pic exige de
rejouer les barres. L'outil affiche donc separement le nombre de trades
gagnants EXPOSES a ce risque, sans pretendre chiffrer la perte.

Aucun seuil n'est modifie par ce script. Il mesure, il n'agit pas.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
EXCURSIONS = RACINE / "results" / "excursions.ndjson"

#: Ce qu'un trade sorti au breakeven rapporte reellement, mesure sur les 8
#: sorties `breakeven` du journal : le SL est place a l'entree plus un tampon,
#: donc legerement positif. Prudent : on ne credite pas mieux que l'observe.
GAIN_BREAKEVEN_R = 0.05


@dataclass
class Trade:
    symbole: str
    mae_r: float
    mfe_r: float
    pnl_r: float
    sortie: str
    contexte: str


def charger(chemin: Path) -> list[Trade]:
    trades = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if not ligne.strip():
            continue
        try:
            d = json.loads(ligne)
            trades.append(Trade(
                symbole=str(d.get("symbol", "")),
                mae_r=float(d.get("mae_r") or 0.0),
                mfe_r=float(d.get("mfe_r") or 0.0),
                pnl_r=float(d.get("pnl_r") or 0.0),
                sortie=str(d.get("exit_reason", "")),
                contexte=str(d.get("context", "")),
            ))
        except (ValueError, TypeError):
            continue        # une ligne illisible est sautee, jamais fatale
    return trades


def esperance(trades: list[Trade]) -> float:
    return sum(t.pnl_r for t in trades) / len(trades) if trades else 0.0


def profit_factor(pnls: list[float]) -> float:
    gains = sum(x for x in pnls if x > 0)
    pertes = abs(sum(x for x in pnls if x < 0))
    return gains / pertes if pertes else float("inf")


def contrefactuel(trades: list[Trade], seuil: float) -> dict:
    """Rejoue le journal avec un breakeven arme a `seuil`."""
    pnls, sauves, exposes = [], [], 0
    for t in trades:
        if t.sortie == "init" and t.mfe_r >= seuil:
            pnls.append(GAIN_BREAKEVEN_R)     # borne inferieure sure
            sauves.append(t)
        else:
            pnls.append(t.pnl_r)
        # Un gagnant dont le creux depasse le nouveau seuil AURAIT PU etre
        # coupe, selon l'ordre creux/pic que le journal ne dit pas.
        if t.pnl_r > GAIN_BREAKEVEN_R and t.mfe_r >= seuil and -t.mae_r > 0:
            exposes += 1
    return {
        "seuil": seuil,
        "esperance": sum(pnls) / len(pnls) if pnls else 0.0,
        "pf": profit_factor(pnls),
        "sauves": sauves,
        "exposes": exposes,
        "total_r": sum(pnls),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--journal", default=str(EXCURSIONS))
    ap.add_argument("--seuils", default="0.3,0.4,0.5,0.6,0.7,0.8")
    args = ap.parse_args()

    chemin = Path(args.journal)
    if not chemin.exists():
        print(f"journal introuvable : {chemin}")
        return 2
    trades = charger(chemin)
    if not trades:
        print("journal vide")
        return 2

    inits = [t for t in trades if t.sortie == "init"]
    perdus_apres_gain = [t for t in inits if t.mfe_r > 0]
    print(f"trades          : {len(trades)}")
    print(f"sorties au stop : {len(inits)} "
          f"({len(inits) / len(trades):.0%}), dont {len(perdus_apres_gain)} "
          f"apres etre passees en territoire favorable")
    print(f"esperance reelle: {esperance(trades):+.4f} R  "
          f"PF {profit_factor([t.pnl_r for t in trades]):.3f}")
    print()
    print(f"{'BE arme a':>10} {'esperance':>11} {'PF':>7} {'stops evites':>13} "
          f"{'gagnants exposes':>17}")
    reel = esperance(trades)
    for seuil in [float(x) for x in args.seuils.split(",")]:
        r = contrefactuel(trades, seuil)
        marque = "  <- actuel" if abs(seuil - 0.8) < 1e-9 else ""
        print(f"{seuil:>10.2f} {r['esperance']:>+11.4f} {r['pf']:>7.3f} "
              f"{len(r['sauves']):>13} {r['exposes']:>17}{marque}")
    print()
    print("Lecture : la colonne esperance est une BORNE SUPERIEURE de ce que")
    print("le seuil aurait donne, puisque le cout des gagnants coupes n'est pas")
    print("chiffre. La colonne 'gagnants exposes' dit combien de trades")
    print("gagnants pourraient l'absorber. Trancher exige de rejouer les barres.")
    print()
    print(f"esperance actuelle {reel:+.4f} R -- aucun seuil n'a ete modifie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
