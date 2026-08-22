"""Les parties timing et zone de l'affinage M5 relèvent-elles l'espérance ?

Mesure hors rejeu. Les artefacts bruts portent déjà l'issue de chaque trade
(`gross_r`, `net_r`) et l'horodatage de décision ; les barres M5 sont archivées.
On peut donc rejouer l'affinage a posteriori sur chaque trade, sans relancer le
moteur — 49 minutes par symbole autrement, et huit cœurs sont déjà pris.

Ce que la mesure vaut, et ce qu'elle ne vaut pas
------------------------------------------------
Elle répond à : « parmi les trades DÉJÀ pris, ceux que l'affinage aurait
confirmés se comportent-ils mieux ? » — c'est un test de pouvoir discriminant.

Elle ne répond PAS à : « l'affinage aurait-il fait entrer à un meilleur prix ? »
La partie 3 (zone FVG) déplace le point d'entrée, ce qui change l'issue du trade ;
seul un vrai rejeu peut le mesurer. Ici la zone n'est donc évaluée que comme
filtre, pas comme repositionnement.

Anti-lookahead : seules les barres M5 dont l'ouverture précède STRICTEMENT
`decision_at` sont fournies à l'affinage.

Lecture seule. N'écrit rien hors de son rapport.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics as st
import sys
from datetime import datetime, timezone

import numpy as np
import pyarrow.parquet as pq

RACINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from titanium.features.entry_refine import affiner  # noqa: E402

FENETRE_M5 = 80          # barres fournies à l'affinage
MIN_EFFECTIF = 40        # sous ce seuil on n'affiche pas de moyenne


def _epoch(txt: str) -> float:
    return datetime.fromisoformat(txt).replace(tzinfo=timezone.utc).timestamp() \
        if "+" not in txt and "Z" not in txt else datetime.fromisoformat(
            txt.replace("Z", "+00:00")).timestamp()


def charger_m5(symbole: str):
    p = RACINE / "results" / "barres" / "M5" / f"{symbole}.parquet"
    if not p.is_file():
        return None
    t = pq.read_table(p, columns=["time_utc", "open", "high", "low", "close",
                                  "tick_volume", "spread"])
    d = t.to_pydict()
    temps = np.asarray([int(x) for x in d["time_utc"]], dtype=np.int64)
    ordre = np.argsort(temps, kind="stable")
    return {
        "time": temps[ordre],
        "open": np.asarray(d["open"], dtype=float)[ordre],
        "high": np.asarray(d["high"], dtype=float)[ordre],
        "low": np.asarray(d["low"], dtype=float)[ordre],
        "close": np.asarray(d["close"], dtype=float)[ordre],
        "spread": np.asarray(d["spread"], dtype=float)[ordre],
    }


def fenetre(m5, fin_exclue: int):
    """Barres M5 ouvertes STRICTEMENT avant `fin_exclue`. DataFrame ou None."""
    import pandas as pd
    i = int(np.searchsorted(m5["time"], fin_exclue, side="left"))
    debut = max(0, i - FENETRE_M5)
    if i - debut < 12:
        return None
    return pd.DataFrame({
        "open": m5["open"][debut:i],
        "high": m5["high"][debut:i],
        "low": m5["low"][debut:i],
        "close": m5["close"][debut:i],
        "tick_volume": np.ones(i - debut),
    })


def _bloc(nom: str, groupe: list[dict], reference: list[dict]) -> str:
    if len(groupe) < MIN_EFFECTIF:
        return f"  {nom:<28} n={len(groupe):>5}   (effectif insuffisant)"
    brut = st.mean(x["gross_r"] for x in groupe)
    net = st.mean(x["net_r"] for x in groupe)
    win = sum(1 for x in groupe if x["net_r"] > 0) / len(groupe) * 100
    ecart = ""
    if reference and len(reference) >= MIN_EFFECTIF:
        autre = [x["net_r"] for x in reference]
        ici = [x["net_r"] for x in groupe]
        d = net - st.mean(autre)
        # Erreur type de la différence de deux moyennes indépendantes (Welch).
        se = math.sqrt(st.pvariance(ici) / len(ici)
                       + st.pvariance(autre) / len(autre))
        sigma = d / se if se > 0 else float("nan")
        ecart = f"   écart {d:+.4f} ± {se:.4f}  ({sigma:+.1f}σ)"
    return (f"  {nom:<28} n={len(groupe):>5}   brut {brut:+.4f}   "
            f"net {net:+.4f}   win {win:4.1f}%{ecart}")


def analyser(symbole: str, split: str) -> dict | None:
    brut = RACINE / "results" / "rejeu_univers_brut" / symbole / "trades.ndjson"
    if not brut.is_file():
        print(f"  {symbole} : pas d'artefact brut")
        return None
    m5 = charger_m5(symbole)
    if m5 is None:
        print(f"  {symbole} : pas de barres M5")
        return None

    couverture = (int(m5["time"][0]), int(m5["time"][-1]))
    evalues, hors_couverture, sans_fenetre = [], 0, 0

    with brut.open(encoding="utf-8") as fh:
        for ligne in fh:
            if not ligne.strip():
                continue
            tr = json.loads(ligne)
            if tr.get("split") != split:
                continue
            ts = int(_epoch(tr["decision_at"]))
            if not couverture[0] <= ts <= couverture[1]:
                hors_couverture += 1
                continue
            df = fenetre(m5, ts)
            if df is None:
                sans_fenetre += 1
                continue
            prix = float(tr["prix_entree"])
            # L'ATR de référence est reconstruit depuis l'unité de risque du
            # trade : r_unit = sl_mult x ATR, et le moteur utilise 1.5 ATR.
            atr = float(tr["r_unit"]) / 1.5
            a = affiner(symbole, int(tr["side"]), df, atr_ref=atr,
                        prix_ref=prix, sl_mult_base=1.5)
            evalues.append({
                "gross_r": tr["gross_r"], "net_r": tr["net_r"],
                "cost_r": tr["cost_r"],
                "confirme": a.confirmation_micro,
                "zone": a.dans_la_zone,
                "score": a.score,
            })

    if not evalues:
        print(f"  {symbole} : aucun trade évaluable "
              f"(hors couverture M5 : {hors_couverture})")
        return None

    print(f"\n=== {symbole}  —  split {split} ===")
    print(f"  évalués {len(evalues)}   hors couverture M5 {hors_couverture}"
          f"   fenêtre trop courte {sans_fenetre}")
    tous = evalues
    print(_bloc("tous", tous, []))
    print()
    oui = [x for x in tous if x["confirme"]]
    non = [x for x in tous if not x["confirme"]]
    print(_bloc("timing confirmé (BOS/rejet)", oui, non))
    print(_bloc("timing non confirmé", non, []))
    print()
    zo = [x for x in tous if x["zone"]]
    nz = [x for x in tous if not x["zone"]]
    print(_bloc("dans une FVG M5 ouverte", zo, nz))
    print(_bloc("hors zone", nz, []))
    print()
    for seuil in (0.4, 0.6):
        haut = [x for x in tous if x["score"] >= seuil]
        bas = [x for x in tous if x["score"] < seuil]
        print(_bloc(f"score >= {seuil:.1f}", haut, bas))
    return {"symbole": symbole, "n": len(evalues), "trades": tous}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symboles", nargs="*",
                    default=["BTC-JPY", "BRENT.fs", "BNB-USD", "AUS200"])
    ap.add_argument("--split", default="verification",
                    choices=["calibration", "verification"])
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    resultats = [r for s in args.symboles if (r := analyser(s, args.split))]
    if len(resultats) < 2:
        return 0

    print("\n" + "=" * 68)
    print(f"=== AGRÉGÉ sur {len(resultats)} symboles, split {args.split} ===")
    tous = [t for r in resultats for t in r["trades"]]
    print(_bloc("tous", tous, []))
    oui = [x for x in tous if x["confirme"]]
    print(_bloc("timing confirmé", oui, [x for x in tous if not x["confirme"]]))
    zo = [x for x in tous if x["zone"]]
    print(_bloc("dans une FVG M5 ouverte", zo, [x for x in tous if not x["zone"]]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
