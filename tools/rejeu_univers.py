"""Rejeu hors ligne de la porte d'entree sur tout l'univers archive.

Protocole PREENREGISTRE avant le balayage, opposable a l'auteur
--------------------------------------------------------------
Chercher en boucle jusqu'a depasser un seuil sur un meme jeu de donnees est une
machine a sur-ajustement : 149 actifs font 149 occasions de trouver un gagnant
par hasard. Les regles ci-dessous sont donc fixees AVANT de lancer, et le script
ne connait aucun moyen de les contourner.

1. La metrique de decision est l'**esperance en R apres couts**, pas le winrate.
   A R:R 2, l'equilibre est a 33 % de winrate ; un winrate flatteur ne paie rien.
2. **Plancher de 60 clotures** par cellule. Sous ce seuil, la cellule est
   rapportee comme non concluante, meme gagnante.
3. **Hors-echantillon strict** : la coupure temporelle est fixee ici a 2/3 de la
   periode. Le dernier tiers ne sert a aucune selection ; il ne fait que
   confirmer ou infirmer.
4. La correction de multiplicite (Benjamini-Hochberg) s'applique a l'analyse de
   la sortie, sur l'ensemble des actifs testes.
5. Une decouverte reste une hypothese : PAPER ONLY.

Ce que le script fait
---------------------
Il LIT l'archive de barres v2 et les specifications figees. Il n'ouvre pas MT5,
ne passe aucun ordre, n'ecrit rien hors de ``results/rejeu_univers``.

Le spread applique est le **spread median archive de la periode**, converti en
prix par le ``point`` du symbole — et non le spread du jour du backtest, qui
etait la simplification du pilote.

Usage
-----
    python tools/rejeu_univers.py --part 0 --sur 6
    python tools/rejeu_univers.py --symboles XAUUSD BTCUSD
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.data.archive_barres import (  # noqa: E402
    charger_barres,
    inventaire,
)

DEST = RACINE / "results" / "rejeu_univers"
SPECIFICATIONS = RACINE / "results" / "barres" / "_specifications.json"

#: Plancher de clotures sous lequel une cellule n'est pas concluante.
CLOTURES_MIN = 60

#: Part de la periode reservee a la calibration. Le reste ne sert qu'a verifier.
PART_CALIBRATION = 2.0 / 3.0


def specifications() -> dict:
    if not SPECIFICATIONS.is_file():
        return {}
    return json.loads(SPECIFICATIONS.read_text(encoding="utf-8"))


def spread_median_prix(ltf, spec: dict) -> float:
    """Spread median de la periode, en unites de prix.

    L'archive porte le spread en points, barre par barre. Utiliser le spread du
    jour du backtest — ce que faisait le pilote — flatte les periodes calmes et
    punit les periodes tendues, dans les deux cas a tort.
    """
    point = float(spec.get("point") or 0.0)
    if not point or "spread" not in ltf.columns or ltf.empty:
        return 0.0
    return float(ltf["spread"].median()) * point


def _stats(trades: list) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "esperance_r": 0.0, "winrate": 0.0,
                "profit_factor": None, "concluant": False}
    pnls = [t.pnl_r for t in trades]
    gains = sum(p for p in pnls if p > 0)
    pertes = -sum(p for p in pnls if p < 0)
    return {
        "n": n,
        "esperance_r": round(sum(pnls) / n, 6),
        "ecart_type_r": round((sum((p - sum(pnls) / n) ** 2 for p in pnls)
                               / max(1, n - 1)) ** 0.5, 6),
        "winrate": round(sum(1 for p in pnls if p > 0) / n, 6),
        "profit_factor": round(gains / pertes, 4) if pertes else None,
        "somme_r": round(sum(pnls), 4),
        "concluant": n >= CLOTURES_MIN,
    }


def rejouer_symbole(symbole: str, ltf_tf: str, htf_tf: str, barres: int | None,
                    pas: int) -> dict:
    from titanium.backtest import rejouer

    t0 = time.time()
    ltf = charger_barres(symbole, ltf_tf, barres)
    htf = charger_barres(symbole, htf_tf)
    spec = specifications().get(symbole, {})
    spread = spread_median_prix(ltf, spec)

    res = rejouer(symbole, ltf, htf, spread=spread, pas=pas)
    trades = sorted(res.trades, key=lambda t: t.bar_entree)

    # Coupure TEMPORELLE, pas par nombre de trades : une periode agitee ne doit
    # pas voler du temps a la periode de verification.
    debut, fin = ltf.index[0], ltf.index[-1]
    coupure = debut + (fin - debut) * PART_CALIBRATION
    coupure_txt = coupure.isoformat()
    calibration = [t for t in trades if str(t.bar_entree) < coupure_txt]
    verification = [t for t in trades if str(t.bar_entree) >= coupure_txt]

    return {
        "symbole": symbole,
        "ltf": ltf_tf,
        "htf": htf_tf,
        "barres_ltf": int(len(ltf)),
        "debut": str(debut),
        "fin": str(fin),
        "coupure": coupure_txt,
        "spread_points": (float(ltf["spread"].median())
                          if "spread" in ltf.columns else None),
        "spread_prix": spread,
        "n_enter": int(res.n_enter),
        "barres_evaluees": int(res.barres_evaluees),
        "erreurs": int(res.erreurs),
        "global": _stats(trades),
        "calibration": _stats(calibration),
        "verification": _stats(verification),
        "secondes": round(time.time() - t0, 1),
        "ecrit_le": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symboles", nargs="*", default=None)
    ap.add_argument("--ltf", default="M15")
    ap.add_argument("--htf", default="H4")
    ap.add_argument("--barres", type=int, default=0, help="0 = toute la profondeur")
    ap.add_argument("--pas", type=int, default=1)
    ap.add_argument("--part", type=int, default=0, help="index du lot")
    ap.add_argument("--sur", type=int, default=1, help="nombre de lots")
    ap.add_argument("--refaire", action="store_true",
                    help="rejoue meme si la sortie existe deja")
    args = ap.parse_args()

    symboles = args.symboles or sorted(inventaire(args.ltf))
    lot = [s for i, s in enumerate(symboles) if i % args.sur == args.part]
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"lot {args.part + 1}/{args.sur} : {len(lot)} symboles", flush=True)

    for symbole in lot:
        cible = DEST / f"{symbole}.json"
        if cible.is_file() and not args.refaire:
            print(f"{symbole:12} deja fait", flush=True)
            continue
        try:
            sortie = rejouer_symbole(symbole, args.ltf, args.htf,
                                     args.barres or None, args.pas)
        except Exception as e:  # un symbole qui casse n'arrete pas le lot
            print(f"{symbole:12} ECHEC {type(e).__name__}: {e}", flush=True)
            continue
        provisoire = cible.with_suffix(".json.tmp")
        provisoire.write_text(json.dumps(sortie, indent=1), encoding="utf-8")
        provisoire.replace(cible)
        g, v = sortie["global"], sortie["verification"]
        print(f"{symbole:12} n={g['n']:5} esp {g['esperance_r']:+.4f} R  "
              f"win {g['winrate'] * 100:4.1f}%  | verif n={v['n']:4} "
              f"esp {v['esperance_r']:+.4f} R  [{sortie['secondes']}s]",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
