#!/usr/bin/env python
"""Suivi d'une bascule : le R realise monte-t-il vraiment apres la decision ?

Une decision de trading se juge sur des trades CLOS, pas sur une intention.
Ce module lit le journal live (`results/trades.ndjson`, une ligne immuable par
position fermee) et compare le R realise avant et apres un instant de bascule.

Trois precautions, sans lesquelles la comparaison ment :

* **un plancher d'effectif.** Sous ``EFFECTIF_MIN`` trades apres la bascule, le
  verdict est INDECIS et rien d'autre. Cinq trades ne disent rien.
* **un temoin.** Ecarter le FX doit faire monter la moyenne globale sans rien
  changer aux classes restantes. Le module publie donc aussi le sous-ensemble
  HORS FX des deux cotes : si lui aussi bouge, c'est le marche qui a change, pas
  la decision.
* **une erreur type.** L'ecart est compare a l'erreur type de la difference. En
  dessous de deux erreurs types, on ecrit INDISTINGUABLE, pas "hausse".

Lecture seule. Aucun ordre, aucun seuil, aucune position touchee.

Usage :
    python tools/suivi_bascule.py
    python tools/suivi_bascule.py --bascule 2026-08-24T06:20:00+00:00
    python tools/suivi_bascule.py --json
    python tools/suivi_bascule.py --equite 1357.84
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

JOURNAL = RACINE / "results" / "trades.ndjson"
SORTIE = RACINE / "results" / "suivi_bascule.json"

#: Suspension du FX decidee le 24/08/2026, appliquee au redemarrage de la
#: boucle. C'est l'instant a partir duquel le journal change de regime.
BASCULE_FX = "2026-08-24T06:22:09+00:00"

#: Sous cet effectif apres la bascule, aucun verdict n'est rendu.
EFFECTIF_MIN = 20

#: Multiple d'erreur type au-dela duquel un ecart cesse d'etre du bruit.
SIGMA_VERDICT = 2.0

#: Risques par trade (part de l'equite) dont on chiffre le prix de la preuve.
#: Convention de rapport : la liste ne deplace aucun seuil, elle choisit les
#: lignes d'un tableau. Le risque REELLEMENT engage est mesure et ajoute a part.
RISQUES_PREUVE = (0.02, 0.01, 0.005, 0.002, 0.001)


def _instant(valeur: str) -> datetime | None:
    try:
        instant = datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)


def _famille(contexte: str) -> str:
    morceaux = str(contexte or "").split("|")
    return morceaux[2] if len(morceaux) > 2 else "?"


def charger(journal: Path = JOURNAL) -> list[dict]:
    """Trades clos exploitables, tries par instant de CLOTURE.

    L'ordre fait partie du contrat, pas du hasard du fichier : le prix de la
    preuve lit la premiere et la derniere cloture de la fenetre pour en tirer le
    rythme et la duree. Sur un journal aux lignes inversees, ces deux champs
    disparaissaient sans le dire — mesures, tous les autres champs identiques.
    Le tri est fait ici, une fois, comme dans `optimiser_allocation_cohorte`.
    """
    lignes: list[dict] = []
    try:
        brut = Path(journal).read_text(encoding="utf-8").splitlines()
    except OSError:
        return lignes
    for ligne in brut:
        if not ligne.strip():
            continue
        try:
            trade = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        instant = _instant(trade.get("closed_at"))
        try:
            r = float(trade.get("pnl_r"))
        except (TypeError, ValueError):
            continue
        if instant is None or not math.isfinite(r):
            continue
        lignes.append({
            "closed_at": instant,
            "pnl_r": r,
            "asset_class": str(trade.get("asset_class") or "?"),
            "famille": _famille(trade.get("context")),
            "context": trade.get("context") or "",
            "cost_r": trade.get("cost_r"),
            "risk_money": trade.get("risk_money"),
        })
    lignes.sort(key=lambda trade: trade["closed_at"])
    return lignes


def _cellule(trades: list[dict]) -> dict:
    valeurs = [t["pnl_r"] for t in trades]
    n = len(valeurs)
    if n == 0:
        return {"n": 0, "moyenne_r": None, "somme_r": 0.0, "erreur_type": None,
                "ecart_type": None}
    moyenne = sum(valeurs) / n
    if n > 1:
        variance = sum((v - moyenne) ** 2 for v in valeurs) / (n - 1)
        erreur = math.sqrt(variance / n)
        ecart_type = math.sqrt(variance)
    else:
        erreur = None
        ecart_type = None
    return {"n": n, "moyenne_r": round(moyenne, 4),
            "somme_r": round(sum(valeurs), 2),
            "erreur_type": round(erreur, 4) if erreur is not None else None,
            "ecart_type": round(ecart_type, 4) if ecart_type is not None else None}


def _ecart(avant: dict, apres: dict, *, effectif_min: int) -> dict:
    if avant["moyenne_r"] is None or apres["moyenne_r"] is None:
        return {"delta_r": None, "sigma": None, "verdict": "INDECIS"}
    delta = apres["moyenne_r"] - avant["moyenne_r"]
    e_avant = avant["erreur_type"] or 0.0
    e_apres = apres["erreur_type"] or 0.0
    erreur = math.sqrt(e_avant ** 2 + e_apres ** 2)
    # Erreur type nulle : les deux groupes sont parfaitement homogenes, il n'y
    # a plus d'incertitude a comparer. L'ecart est alors soit nul, soit certain.
    if erreur > 0:
        sigma = delta / erreur
    else:
        sigma = 0.0 if delta == 0 else math.copysign(float("inf"), delta)
    if apres["n"] < effectif_min or sigma is None:
        verdict = "INDECIS"
    elif sigma >= SIGMA_VERDICT:
        verdict = "HAUSSE"
    elif sigma <= -SIGMA_VERDICT:
        verdict = "BAISSE"
    else:
        verdict = "INDISTINGUABLE"
    # `sigma` infini (erreur type nulle) ne s'ecrit pas en JSON strict : le
    # verdict porte deja l'information, le chiffre devient None.
    lisible = (sigma is not None and math.isfinite(sigma))
    return {"delta_r": round(delta, 4),
            "sigma": round(sigma, 2) if lisible else None,
            "verdict": verdict}


def _bloc(avant: list[dict], apres: list[dict], *, effectif_min: int) -> dict:
    cellule_avant, cellule_apres = _cellule(avant), _cellule(apres)
    return {"avant": cellule_avant, "apres": cellule_apres,
            "ecart": _ecart(cellule_avant, cellule_apres,
                            effectif_min=effectif_min)}


def comparer(trades: list[dict], bascule: datetime, *,
             effectif_min: int = EFFECTIF_MIN) -> dict:
    """Avant / apres la bascule, en global, hors FX (temoin), et par classe."""
    avant = [t for t in trades if t["closed_at"] < bascule]
    apres = [t for t in trades if t["closed_at"] >= bascule]
    def hors_fx(groupe: list[dict]) -> list[dict]:
        return [t for t in groupe if t["asset_class"] != "fx"]

    classes = sorted({t["asset_class"] for t in trades})
    familles = sorted({t["famille"] for t in trades})
    return {
        "schema_version": 1,
        "mesure_le": datetime.now(timezone.utc).isoformat(),
        "bascule": bascule.isoformat(),
        "effectif_min": effectif_min,
        "global": _bloc(avant, apres, effectif_min=effectif_min),
        "hors_fx": _bloc(hors_fx(avant), hors_fx(apres),
                         effectif_min=effectif_min),
        "par_classe": {
            classe: _bloc([t for t in avant if t["asset_class"] == classe],
                          [t for t in apres if t["asset_class"] == classe],
                          effectif_min=effectif_min)
            for classe in classes},
        "par_famille": {
            famille: _bloc([t for t in avant if t["famille"] == famille],
                           [t for t in apres if t["famille"] == famille],
                           effectif_min=effectif_min)
            for famille in familles},
    }


def prix_de_la_preuve(trades: list[dict], bascule: datetime, *, equite: float,
                      seuil_sigma: float = SIGMA_VERDICT,
                      risques: tuple[float, ...] = RISQUES_PREUVE) -> dict:
    """Ce que coute la preuve : combien de clotures, combien de temps, combien d'euros.

    UNE SEULE BASE, enoncee ici et nulle part ailleurs — c'est ce qui manquait
    quand le meme tableau portait deux chiffres pour la meme ligne :

        n_requis x |esperance post-bascule| x risque par trade x equite

    ``n_requis`` est le nombre de clotures qui tranche l'ecart avant/apres a
    ``seuil_sigma`` erreurs types : ``(seuil_sigma x ecart-type post / ecart)**2``,
    arrondi au superieur. L'esperance post-bascule est ce que chaque cloture
    coute en R ; le risque par trade est la part d'equite engagee a chaque
    cloture. Rien d'autre n'entre dans la formule, donc deux lignes du tableau ne
    peuvent pas se contredire — chacune ne change que le risque.

    Le risque REELLEMENT engage est mesure, lui aussi : la moyenne de
    ``risk_money`` sur l'equite. C'est la ligne a lire pour savoir ce que la
    preuve coute aujourd'hui, parce que le plafond par trade n'est pas ce que la
    boucle engage en moyenne.
    """
    avant = [t for t in trades if t["closed_at"] < bascule]
    apres = [t for t in trades if t["closed_at"] >= bascule]
    cellule_avant, cellule_apres = _cellule(avant), _cellule(apres)
    bloc: dict = {
        "schema_version": 1,
        "equite": round(equite, 2),
        "n_observe": cellule_apres["n"],
        "ecart_r": None,
        "ecart_type_post": cellule_apres["ecart_type"],
        "seuil_sigma": seuil_sigma,
        "n_requis": None,
        "rythme_par_h": None,
        "duree_h": None,
        "risque_engage_pct": None,
        "lignes": [],
        "motif": None,
    }
    if not equite > 0:
        bloc["motif"] = "equite absente ou nulle : le prix de la preuve n'est pas chiffrable"
        return bloc

    ecart_type = cellule_apres["ecart_type"]
    esperance = cellule_apres["moyenne_r"]
    if cellule_avant["n"] == 0 or cellule_apres["n"] == 0:
        bloc["motif"] = ("une des deux fenetres est vide : l'ecart et l'effectif "
                         "requis ne sont pas calculables")
        return bloc
    if cellule_apres["n"] < 2:
        bloc["motif"] = ("dispersion non estimable : une seule cloture apres la "
                         "bascule, l'ecart-type en demande deux")
        return bloc
    ecart = esperance - cellule_avant["moyenne_r"]
    bloc["ecart_r"] = round(ecart, 4)
    if ecart == 0 or ecart_type == 0:
        bloc["motif"] = ("ecart ou ecart-type nul : l'effectif requis tend vers "
                         "l'infini, le prix n'est pas chiffrable")
        return bloc

    n_requis = math.ceil((seuil_sigma * ecart_type / abs(ecart)) ** 2)
    bloc["n_requis"] = n_requis
    if len(apres) > 1:
        heures = (apres[-1]["closed_at"] - apres[0]["closed_at"]).total_seconds() / 3600
        if heures > 0:
            rythme = len(apres) / heures
            bloc["rythme_par_h"] = round(rythme, 2)
            bloc["duree_h"] = round(n_requis / rythme, 1)

    engages = [float(t["risk_money"]) for t in apres
               if isinstance(t.get("risk_money"), (int, float))
               and float(t["risk_money"]) > 0]
    if engages:
        bloc["risque_engage_pct"] = round((sum(engages) / len(engages)) / equite, 6)

    def _ligne_prix(risque: float, source: str) -> dict:
        perte = n_requis * abs(esperance) * risque * equite
        return {"risque_pct": round(risque, 6),
                "perte_eur": round(perte, 2),
                "perte_pct_compte": round(perte / equite * 100, 1),
                "source_du_risque": source}

    bloc["lignes"] = [_ligne_prix(risque, "plafond") for risque in risques]
    if bloc["risque_engage_pct"]:
        bloc["lignes"].append(_ligne_prix(bloc["risque_engage_pct"], "engage_mesure"))
    return bloc


def _lignes_prix(bloc: dict) -> list[str]:
    """Le prix de la preuve, tel que l'operateur le lit — une seule mise en forme."""
    lignes = ["", f"prix de la preuve (equite {bloc['equite']:.2f} EUR)"]
    if bloc.get("motif"):
        lignes.append(f"  non chiffrable : {bloc['motif']}")
        return lignes
    lignes.append(f"  effectif requis {bloc['n_requis']} clotures (ecart "
                  f"{bloc['ecart_r']:+.4f} R, ecart-type post "
                  f"{bloc['ecart_type_post']} R, seuil {bloc['seuil_sigma']} sigma)"
                  f" ; observe {bloc['n_observe']}")
    if bloc.get("duree_h") is not None:
        lignes.append(f"  duree {bloc['duree_h']} h au rythme de "
                      f"{bloc['rythme_par_h']} clotures/h — independante de la "
                      f"taille du risque")
    lignes.append("  risque par trade      perte attendue de la preuve")
    for ligne in bloc["lignes"]:
        marque = ("   <- engage mesure"
                  if ligne["source_du_risque"] == "engage_mesure" else "")
        lignes.append(f"    {ligne['risque_pct'] * 100:6.2f} %   "
                      f"{ligne['perte_eur']:9.2f} EUR   "
                      f"({ligne['perte_pct_compte']:6.1f} % du compte){marque}")
    return lignes


def _ligne(nom: str, bloc: dict) -> str:
    a, b, e = bloc["avant"], bloc["apres"], bloc["ecart"]
    return (f"  {nom:<16}"
            f"avant {a['n']:>4} @ {a['moyenne_r'] if a['moyenne_r'] is not None else 0:>+8.4f}"
            f"   apres {b['n']:>4} @ {b['moyenne_r'] if b['moyenne_r'] is not None else 0:>+8.4f}"
            f"   delta {e['delta_r'] if e['delta_r'] is not None else 0:>+8.4f}"
            f"   {e['verdict']}")


def resumer(rapport: dict) -> str:
    lignes = [f"suivi de bascule — {rapport['bascule']}",
              f"  plancher : {rapport['effectif_min']} trades clos apres la "
              f"bascule avant tout verdict", ""]
    lignes.append(_ligne("GLOBAL", rapport["global"]))
    lignes.append(_ligne("temoin hors FX", rapport["hors_fx"]))
    lignes.append("")
    for classe, bloc in rapport["par_classe"].items():
        lignes.append(_ligne(classe, bloc))
    lignes.append("")
    for famille, bloc in rapport["par_famille"].items():
        lignes.append(_ligne(famille, bloc))
    verdict = rapport["global"]["ecart"]["verdict"]
    if verdict == "INDECIS":
        lignes += ["", "Verdict differe : pas encore assez de trades clos "
                       "apres la bascule. Une moyenne sur quelques trades "
                       "decrit le hasard, pas la decision."]
    else:
        lignes += ["", "Rappel : le temoin hors FX doit rester stable. S'il "
                       "bouge autant que le global, c'est le marche qui a "
                       "change, pas la suspension."]
    if rapport.get("prix_preuve"):
        lignes += _lignes_prix(rapport["prix_preuve"])
    return "\n".join(lignes)


def plancher_atteint(rapport: dict) -> bool:
    """Vrai des que le verdict global cesse d'etre differe faute d'effectif."""
    return rapport["global"]["apres"]["n"] >= rapport["effectif_min"]


def veiller(journal: Path, bascule: datetime, *, effectif_min: int,
            intervalle: float, sortie: Path, sortie_md: Path,
            max_h: float) -> dict:
    """Attend le plancher d'effectif, puis publie -- sans juger avant.

    Une bascule se regarde toutes les cinq minutes quand on est impatient, et
    c'est ainsi qu'on finit par lire du bruit comme un resultat. Cette veille
    ne rend son rapport qu'au plancher, ou a l'expiration du delai.
    """
    debut = time.time()
    while True:
        rapport = comparer(charger(journal), bascule,
                           effectif_min=effectif_min)
        apres = rapport["global"]["apres"]["n"]
        expire = (time.time() - debut) >= max_h * 3600
        horodatage = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{horodatage}] trades clos depuis la bascule : "
              f"{apres}/{effectif_min}"
              f"{' — delai expire' if expire else ''}", flush=True)
        if plancher_atteint(rapport) or expire:
            rapport["arret"] = "plancher" if plancher_atteint(rapport) else "delai"
            sortie.parent.mkdir(parents=True, exist_ok=True)
            sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=1),
                              encoding="utf-8")
            sortie_md.parent.mkdir(parents=True, exist_ok=True)
            sortie_md.write_text(resumer(rapport), encoding="utf-8")
            return rapport
        time.sleep(intervalle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--journal", type=Path, default=JOURNAL)
    ap.add_argument("--bascule", default=BASCULE_FX)
    ap.add_argument("--effectif-min", type=int, default=EFFECTIF_MIN)
    ap.add_argument("--sortie", type=Path, default=SORTIE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--equite", type=float, default=None,
                    help="equite du compte en EUR ; ajoute le prix de la preuve")
    ap.add_argument("--veiller", action="store_true",
                    help="attendre le plancher d'effectif, puis publier")
    ap.add_argument("--intervalle", type=float, default=600.0)
    ap.add_argument("--max-h", type=float, default=72.0)
    ap.add_argument("--sortie-md", type=Path,
                    default=RACINE / "collab" / "prime_agent" / "runs"
                    / "bascule-fx-20260824" / "suivi.md")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    bascule = _instant(args.bascule)
    if bascule is None:
        print(f"instant de bascule illisible : {args.bascule!r}")
        return 2
    if args.veiller:
        rapport = veiller(args.journal, bascule,
                          effectif_min=args.effectif_min,
                          intervalle=args.intervalle, sortie=args.sortie,
                          sortie_md=args.sortie_md, max_h=args.max_h)
        print(resumer(rapport))
        return 0
    trades = charger(args.journal)
    rapport = comparer(trades, bascule, effectif_min=args.effectif_min)
    if args.equite is not None:
        rapport["prix_preuve"] = prix_de_la_preuve(trades, bascule, equite=args.equite)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(json.dumps(rapport, ensure_ascii=False, indent=1) if args.json
          else resumer(rapport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
