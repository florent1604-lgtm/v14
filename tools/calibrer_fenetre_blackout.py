"""La fenetre de blackout 900 / 300 tient-elle au cout observe ?

CE QUI EST MESURE
-----------------
Le veto macro gele les entrees autour d'une publication : ``blackout_before_s``
avant, ``blackout_after_s`` apres (900 / 300 en production). Ces deux nombres
n'ont jamais ete calibres. Cet outil mesure le profil du cout d'execution
autour d'une ancre et derive la fenetre ou l'ecart a la ligne de base est reel
-- avant et apres SEPAREMENT, puisque le chiffre en place les distingue.

La ligne de base d'un symbole est la mediane de son spread par creneau
hebdomadaire (jour de la semaine x minute UTC). Les fenetres d'ancre sont
exclues du calcul de la ligne de base. Une publication isolee ne deplacerait
pas une mediane, mais une publication hebdomadaire fait que le creneau n'a
presque que des barres choquees : la mediane absorbe alors le choc et
l'ecart mesure s'effondre.

La grandeur qui derive la fenetre est la part des episodes anormaux --
la fraction des ancres ou le ratio depasse le seuil. Un pic isole ne
fabrique pas une fenetre, et un exces qui touche le bord de l'horizon
est signale tronque.

DEUX ANCRAGES
-------------
A -- sur le calendrier (``--calendrier``, defaut
``data/calendrier_macro.json``). Lecture par le MEME lecteur que la
production (``titanium.macro.sources``, fournisseur ``file``).

B -- sur les chocs synchronises (sans calendrier). Les minutes ou assez de
symboles depassent le seuil en meme temps. Ancrees toutes les 2x l'horizon
pour que les fenetres ne se chevauchent pas. Brute puis privee de
l'heure dominante.

    .venv\\Scripts\\python.exe -X utf8 tools/calibrer_fenetre_blackout.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

ARCHIVE = RACINE / "results" / "barres"
CALENDRIER_DEFAUT = RACINE / "data" / "calendrier_macro.json"
SEUIL_EXCES = 1.25
PART_MIN = 0.50
HORIZON_S = 3600
MIN_SYMBOLES = 6
MAX_ANCRES = 200
COLONNES = ("time_utc", "open", "high", "low", "close", "spread", "reconstruit")


@dataclass(frozen=True)
class Univers:
    presents: tuple[str, ...]
    absents: tuple[str, ...]


def univers_negocie(timeframe: str, archive: Path = ARCHIVE) -> Univers:
    from titanium.edge import ASSET_CLASSES

    voulus = sorted({s for c in ASSET_CLASSES.values() for s in c})
    dossier = archive / timeframe
    presents = [s for s in voulus if (dossier / f"{s}.parquet").is_file()]
    absents = [s for s in voulus if not (dossier / f"{s}.parquet").is_file()]
    return Univers(tuple(presents), tuple(absents))


def charger_series(symbole: str, timeframe: str, archive: Path = ARCHIVE) -> pd.DataFrame:
    brut = pd.read_parquet(archive / timeframe / f"{symbole}.parquet", columns=list(COLONNES))
    brut = brut[(~brut["reconstruit"]) & (brut["spread"] > 0)]
    instant = pd.to_datetime(brut["time_utc"], unit="s", utc=True)
    amplitude = (brut["high"] - brut["low"]) / brut["close"].where(brut["close"] != 0)
    series = pd.DataFrame({
        "t": instant.to_numpy(),
        "spread": brut["spread"].astype("float64").to_numpy(),
        "amplitude": pd.to_numeric(amplitude, errors="coerce").to_numpy(),
    })
    return series.dropna(subset=["t", "spread"]).reset_index(drop=True)


def creneaux(instant: pd.Series) -> pd.Series:
    h = pd.to_datetime(instant, utc=True)
    return h.dt.weekday * 1440 + h.dt.hour * 60 + h.dt.minute


def _masque_exclusion(series: pd.DataFrame, ancres: list[datetime]) -> np.ndarray:
    masque = np.zeros(len(series), dtype=bool)
    if not ancres:
        return masque
    temps = pd.DatetimeIndex(series["t"])
    for ancre in ancres:
        debut = ancre - timedelta(seconds=HORIZON_S)
        fin = ancre + timedelta(seconds=HORIZON_S)
        masque |= np.asarray((temps >= debut) & (temps <= fin), dtype=bool)
    return masque


def ratios(series: pd.DataFrame, ancres: list[datetime]) -> pd.DataFrame:
    cr = creneaux(series["t"])
    hors = ~_masque_exclusion(series, ancres)
    cadre = series.assign(creneau=cr)
    base_spread = cadre.loc[hors].groupby("creneau")["spread"].median()
    base_amplitude = cadre.loc[hors].groupby("creneau")["amplitude"].median()
    sortie = pd.DataFrame({
        "t": series["t"],
        "spread": series["spread"],
        "amplitude": series["amplitude"],
        "ratio_spread": series["spread"] / cr.map(base_spread),
        "ratio_amplitude": series["amplitude"] / cr.map(base_amplitude),
        "exclu": ~hors,
    })
    return sortie.replace([np.inf, -np.inf], np.nan).dropna(subset=["ratio_spread"])


def barre_s(series: pd.DataFrame, defaut: float = 300.0) -> float:
    if len(series) < 3:
        return defaut
    ecarts = pd.Series(series["t"]).diff().dt.total_seconds().dropna()
    ecarts = ecarts[ecarts > 0]
    return float(ecarts.median()) if len(ecarts) else defaut


def profil(series: pd.DataFrame, ancres: list[datetime], pas_s: float,
           seuil: float = SEUIL_EXCES) -> dict:
    if series.empty or not ancres:
        return {"par_offset": {}, "ancres": 0, "barre_s": pas_s}
    temps = pd.DatetimeIndex(series["t"])
    pas = max(1, int(round(pas_s)))
    grille = list(range(-HORIZON_S, HORIZON_S + pas, pas))
    po: dict[int, list[float]] = {o: [] for o in grille}
    poa: dict[int, list[float]] = {o: [] for o in grille}
    utiles = 0
    for ancre in ancres:
        positions: list[int | None] = []
        for o in grille:
            cible = ancre + timedelta(seconds=o)
            pos = int(temps.searchsorted(cible, side="right")) - 1
            if pos < 0 or pos >= len(temps):
                positions.append(None)
                continue
            ecart = abs((temps[pos] - cible).total_seconds())
            positions.append(pos if ecart <= max(pas_s, 1.0) else None)
        if all(p is None for p in positions):
            continue
        utiles += 1
        for o, pos in zip(grille, positions, strict=True):
            if pos is None:
                continue
            po[o].append(float(series["ratio_spread"].iloc[pos]))
            v = series["ratio_amplitude"].iloc[pos]
            if pd.notna(v):
                poa[o].append(float(v))
    resume = {}
    for o in grille:
        vals = po[o]
        if not vals:
            continue
        s = pd.Series(vals)
        amps = poa[o]
        resume[o] = {
            "n": len(vals),
            "part": float((s >= seuil).mean()),
            "median": float(s.median()),
            "p90": float(s.quantile(0.90)),
            "amplitude_median": float(pd.Series(amps).median()) if amps else None,
        }
    return {"par_offset": resume, "ancres": utiles, "barre_s": pas_s}


def fenetre_derivee(resume: dict, part_min: float = PART_MIN) -> dict:
    vide = {"debut_s": None, "fin_s": None, "tronque_avant": False,
            "tronque_apres": False, "raison": ""}
    bloc = resume.get("par_offset") or {}
    if not bloc:
        return {**vide, "raison": "aucune ancre exploitable"}

    exces = {offset for offset in bloc if bloc[offset]["part"] >= part_min}
    if not exces:
        return {**vide, "raison": f"aucun exces contigu (part >= {part_min:.2f})"}
    bord_gauche, bord_droit = min(bloc), max(bloc)

    debut = None
    for o in sorted((x for x in bloc if x <= 0), reverse=True):
        if o in exces:
            debut = o
        else:
            break

    apres = sorted(o for o in exces if o > 0)
    if not apres:
        fin, tronque_apres = 0, False
    elif apres[-1] == bord_droit:
        fin, tronque_apres = apres[-1], True
    else:
        fin = min(o for o in bloc if o > apres[-1])
        tronque_apres = False

    return {
        "debut_s": abs(debut) if debut is not None else 0,
        "fin_s": fin,
        "tronque_avant": debut == bord_gauche,
        "tronque_apres": tronque_apres,
        "raison": "",
    }


def mesurer(series: pd.DataFrame, ancres: list[datetime], seuil: float,
            part_min: float = PART_MIN) -> dict:
    if not ancres or series.empty:
        return {"ancres": 0, "fenetre": fenetre_derivee({}, part_min)}
    pas = barre_s(series)
    taux = ratios(series, ancres)
    pr = profil(taux, ancres, pas, seuil)
    return {
        "barre_s": pas,
        "ancres": pr["ancres"],
        "exclus_pct": round(100.0 * float(taux["exclu"].mean()), 2) if len(taux) else 0.0,
        "profil": pr["par_offset"],
        "fenetre": fenetre_derivee(pr, part_min),
    }


def ancres_calendrier(chemin: Path, impact_min: str) -> tuple[list[datetime], dict]:
    from titanium.macro.contracts import MacroImpact
    from titanium.macro.sources import FileMacroSource
    seuil = MacroImpact.parse(impact_min)
    calendrier = FileMacroSource(chemin).fetch()
    retenus = [e for e in calendrier.events if e.impact >= seuil]
    ancres = sorted({e.scheduled_at for e in retenus})
    return ancres, {
        "chemin": str(chemin), "evenements": len(calendrier.events),
        "retenus": len(retenus), "digest": calendrier.digest(),
        "impact_min": seuil.name,
    }


def _minutes_bloquees(exclure_heures: list[str]) -> set[int]:
    bloquees: set[int] = set()
    for plage in exclure_heures:
        debut, _, fin = plage.partition("-")
        h_d, m_d = (int(x) for x in debut.split(":"))
        h_f, m_f = (int(x) for x in fin.split(":"))
        d, a = h_d * 60 + m_d, h_f * 60 + m_f
        if a >= d:
            bloquees.update(range(d, a + 1))
        else:
            bloquees.update(range(d, 1440))
            bloquees.update(range(0, a + 1))
    return bloquees


def detecter_chocs(par_symbole: dict[str, pd.DataFrame], *, min_symboles: int,
                   seuil: float, bloquees: set[int] | None = None
                   ) -> tuple[list[datetime], dict]:
    bloquees = bloquees or set()
    comptes: Counter = Counter()
    for series in par_symbole.values():
        for instant in pd.DatetimeIndex(series.loc[series["ratio_spread"] >= seuil, "t"]):
            if instant.hour * 60 + instant.minute in bloquees:
                continue
            comptes[instant] += 1
    retenus = sorted(i for i, c in comptes.items() if c >= min_symboles)
    espacement_s = 2 * HORIZON_S
    chocs: list[pd.Timestamp] = []
    for instant in retenus:
        if chocs and (instant - chocs[-1]).total_seconds() < espacement_s:
            continue
        chocs.append(instant)
    if len(chocs) > MAX_ANCRES:
        chocs = sorted(sorted(chocs, key=lambda i: comptes[i], reverse=True)[:MAX_ANCRES])
    minutes_observees = max((len(s) for s in par_symbole.values()), default=0)
    part = len(retenus) / minutes_observees if minutes_observees else 0.0
    return [instant.to_pydatetime() for instant in chocs], {
        "minutes_synchronisees": len(retenus),
        "minutes_observees": int(minutes_observees),
        "part_minutes_synchronisees": round(part, 4),
        "sature": bool(minutes_observees and part > 0.10),
        "chocs": len(chocs),
        "espacement_min_s": espacement_s,
        "horloge": [[c, v] for c, v in
                    Counter(i.strftime("%H:%M") for i in chocs).most_common(8)],
        "min_symboles": min_symboles,
    }


def _heure_dominante(horloge: list[list]) -> str:
    if not horloge:
        return ""
    heures: Counter = Counter()
    for cle, compte in horloge:
        heures[int(cle.split(":")[0])] += compte
    heure = heures.most_common(1)[0][0]
    return f"{(heure - 1) % 24:02d}:30-{heure:02d}:30"


def _imprimer_profil(resultats: dict[str, dict], pas: int = 300) -> None:
    exploitables = {s: r for s, r in resultats.items() if r.get("profil")}
    if not exploitables:
        print("  aucun symbole avec assez d'ancres")
        return
    meilleur = max(exploitables, key=lambda s: len(exploitables[s]["profil"]))
    bloc = exploitables[meilleur]["profil"]
    print(f"  profil de {meilleur} -- part / spread / volatilite :")
    for o in sorted(bloc):
        if o % pas != 0:
            continue
        ligne = bloc[o]
        amp = ligne.get("amplitude_median")
        ta = f"{amp:.2f}" if amp is not None else "n/a"
        marque = "   <-- ancre" if o == 0 else ""
        print(f"    {o:+6d} s  n={ligne['n']:<5d} part={ligne['part']:.2f}  "
              f"median={ligne['median']:.2f}  p90={ligne['p90']:.2f}  "
              f"amplitude={ta}{marque}")


def _imprimer_fenetres(etiquette: str, resultats: dict[str, dict]) -> None:
    print(f"  branche {etiquette} :")
    derives = 0
    for symbole, resultat in sorted(resultats.items()):
        fen = resultat.get("fenetre") or {}
        if fen.get("debut_s") or fen.get("fin_s"):
            derives += 1
            suite = "  [tronque par l'horizon]" if (
                fen.get("tronque_avant") or fen.get("tronque_apres")
            ) else ""
            print(f"    {symbole:<11} avant {fen.get('debut_s')} s, "
                  f"apres {fen.get('fin_s')} s   "
                  f"(ancres={resultat.get('ancres')}, "
                  f"exclus={resultat.get('exclus_pct')} %){suite}")
    print(f"  -> {derives} symbole(s) sur {len(resultats)} montrent un exces contigu")


def main() -> int:
    an = argparse.ArgumentParser(
        description="Calibrer la fenetre de blackout macro sur le cout observe")
    an.add_argument("--timeframe", default="M5")
    an.add_argument("--archive", type=Path, default=ARCHIVE)
    an.add_argument("--calendrier", type=Path, default=CALENDRIER_DEFAUT)
    an.add_argument("--impact", default="HIGH")
    an.add_argument("--seuil", type=float, default=SEUIL_EXCES)
    an.add_argument("--part", type=float, default=PART_MIN)
    an.add_argument("--min-symboles", type=int, default=MIN_SYMBOLES)
    an.add_argument("--exclure-heures", nargs="*", default=[])
    an.add_argument("--symboles", nargs="*", default=None)
    an.add_argument("--json", type=Path, default=None)
    args = an.parse_args()

    from titanium.macro.policy import MacroPolicy

    pol = MacroPolicy()
    rapport: dict = {
        "timeframe": args.timeframe, "archive": str(args.archive),
        "seuil": args.seuil, "part_min": args.part, "horizon_s": HORIZON_S,
        "declare": {"blackout_before_s": pol.blackout_before_s,
                    "blackout_after_s": pol.blackout_after_s},
    }

    univers = univers_negocie(args.timeframe, args.archive)
    symboles = tuple(args.symboles) if args.symboles else univers.presents
    rapport["univers"] = {
        "negocies": len(univers.presents) + len(univers.absents),
        "presents": len(univers.presents), "absents": list(univers.absents),
        "mesures": list(symboles),
    }
    print("\n1. LES SYMBOLES QUE LA BOUCLE NEGOCIE")
    print("-" * 40)
    print(f"  archive : {args.archive}  ({args.timeframe})")
    print(f"  classes d'actifs de `titanium.edge` : "
          f"{len(univers.presents) + len(univers.absents)} symboles")
    print(f"  presents : {len(univers.presents)}")
    print(f"  absents : {len(univers.absents)}"
          + (f" ({', '.join(univers.absents)})" if univers.absents else ""))

    chargees: dict[str, pd.DataFrame] = {}
    for symbole in symboles:
        try:
            series = charger_series(symbole, args.timeframe, args.archive)
        except (FileNotFoundError, ValueError, KeyError) as exc:
            print(f"  {symbole} illisible : {exc}")
            continue
        if len(series) > 10:
            chargees[symbole] = series
    if chargees:
        debut = min(s["t"].iloc[0] for s in chargees.values())
        fin = max(s["t"].iloc[-1] for s in chargees.values())
        rapport["echantillon"] = {
            "barres": int(sum(len(s) for s in chargees.values())),
            "debut": str(debut), "fin": str(fin),
            "symboles_charges": len(chargees),
        }
        print(f"  echantillon : {sum(len(s) for s in chargees.values())} barres, "
              f"{debut:%Y-%m-%d} -> {fin:%Y-%m-%d}")

    print("\n2. BRANCHE A -- AUTOUR DE CHAQUE PUBLICATION DU CALENDRIER")
    print("-" * 58)
    ancres_a: list[datetime] = []
    resultats_a: dict[str, dict] = {}
    if not args.calendrier.is_file():
        print(f"  aucun calendrier a {args.calendrier}")
        print("  La branche A ne peut pas etre executee ici. Le lecteur est")
        print("  celui de la production (`titanium.macro.sources`, fournisseur")
        print("  `file`) : deposer le calendrier a ce chemin suffit.")
        rapport["branche_calendrier"] = {"present": False,
                                         "chemin": str(args.calendrier)}
    else:
        try:
            ancres_a, detail = ancres_calendrier(args.calendrier, args.impact)
        except (ValueError, OSError) as exc:
            detail = {"erreur": str(exc)}
            print(f"  calendrier illisible : {exc}")
        else:
            print(f"  {detail['evenements']} publications, {detail['retenus']} "
                  f"retenues (impact >= {detail['impact_min']})")
            print(f"  digest : {detail['digest'][:16]}")
            print(f"  ancres : {len(ancres_a)}")
        rapport["branche_calendrier"] = {"present": True, **detail}
    if ancres_a:
        resultats_a = {s: mesurer(v, ancres_a, args.seuil, args.part)
                       for s, v in chargees.items()}
        rapport["branche_calendrier"]["par_symbole"] = resultats_a
        _imprimer_profil(resultats_a)

    print("\n3. BRANCHE B -- AUTOUR DES CHOCS SYNCHRONISES (sans calendrier)")
    print("-" * 62)
    ps = {s: ratios(v, []) for s, v in chargees.items()}
    bloc_res = {}

    for etiquette, exclure in [("branche_chocs", []),
                               ("branche_chocs_filtree", [])]:
        chocs, detail = detecter_chocs(
            ps, min_symboles=args.min_symboles, seuil=args.seuil,
            bloquees=_minutes_bloquees(exclure or []))
        detail["heure_dominante"] = _heure_dominante(detail["horloge"])
        detail["exclure_heures"] = exclure
        rapport[etiquette] = detail
        print(f"  {etiquette} :")
        print(f"  minutes >= {detail['min_symboles']} symboles depassent "
              f"{args.seuil:.2f}x : {detail['minutes_synchronisees']} / "
              f"{detail['minutes_observees']} "
              f"({100 * detail['part_minutes_synchronisees']:.1f} %)")
        if detail["sature"]:
            print("  CRITERE SATURE : plus d'une minute sur dix est synchronisee.")
        for h, c in detail["horloge"]:
            print(f"    {h} UTC  x{c}")
        if chocs:
            r = {s: mesurer(v, chocs, args.seuil, args.part)
                 for s, v in chargees.items()}
            rapport[etiquette]["par_symbole"] = r
            _imprimer_profil(r)
        else:
            print("  aucune ancre")
        if not exclure and detail["chocs"] > 0:
            dominante = detail["heure_dominante"]
            if dominante:
                bloc_res["dominante"] = dominante
        elif not exclure and not detail["chocs"]:
            break

    # Deuxieme passe : heure dominante exclue
    if bloc_res.get("dominante") and not args.exclure_heures:
        dom = bloc_res["dominante"]
        print(f"\n  privee de l'heure dominante {dom} UTC")
        chocs2, detail2 = detecter_chocs(
            ps, min_symboles=args.min_symboles, seuil=args.seuil,
            bloquees=_minutes_bloquees([dom]))
        detail2["heure_dominante"] = dom
        rapport["branche_chocs_filtree"] = detail2
        print(f"  minutes synchronisees : {detail2['minutes_synchronisees']} / "
              f"{detail2['minutes_observees']} "
              f"({100 * detail2['part_minutes_synchronisees']:.1f} %)")
        for h, c in detail2["horloge"]:
            print(f"    {h} UTC  x{c}")
        if chocs2:
            r2 = {s: mesurer(v, chocs2, args.seuil, args.part)
                  for s, v in chargees.items()}
            rapport["branche_chocs_filtree"]["par_symbole"] = r2
            _imprimer_profil(r2)

    print("\n4. LA FENETRE DERIVEE, CONTRE LE 900 / 300 DECLARE")
    print("-" * 51)
    print(f"  declare : {pol.blackout_before_s:.0f} s avant, "
          f"{pol.blackout_after_s:.0f} s apres")
    print(f"  derivee sur part >= {args.part:.2f} au seuil {args.seuil:.2f}x")
    for etiq, r in [("calendrier", resultats_a),
                     ("chocs synchronises", rapport.get("branche_chocs", {}).get("par_symbole", {})),
                     ("chocs filtres", rapport.get("branche_chocs_filtree", {}).get("par_symbole", {}))]:
        if r:
            _imprimer_fenetres(etiq, r)
    if not any((resultats_a,)) and not rapport.get("branche_chocs", {}).get("par_symbole"):
        print("  aucune branche n'a produit d'ancres.")

    print("\n5. SI UNE FENETRE DOIT CHANGER, LA LIGNE EXACTE")
    print("-" * 48)
    print("  aucun parametre n'est deplace par cet outil.")
    print('  config/macro.json : "blackout_before_s": <v>, "blackout_after_s": <v>')
    print("  defaut : titanium/macro/policy.py ; empreinte : " + pol.fingerprint())

    print("\n6. CE QUE CETTE MESURE NE PERMET PAS DE CONCLURE")
    print("-" * 54)
    for ligne in (
        "Spread de barre, pas un ordre reel. Le spread d'une barre n'est pas",
        "  son maximum intra-barre : la fenetre derivee est un minorant.",
        "L'echantillon est celui de l'archive, sur la periode au 1.",
        "L'ancrage B est endogene (il selectionne le pic) : il minore la fenetre.",
        "Seule la branche A, avec un calendrier, peut nommer la cause.",
        "Une annee de regime ne fait pas une decennie.",
    ):
        print(f"  {ligne}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(rapport, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8")
        print(f"\nrapport : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
