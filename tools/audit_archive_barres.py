#!/usr/bin/env python
"""Audit de conformite de l'archive de barres, source par source.

Pourquoi cet outil existe
-------------------------
`tools/audit_rejeu_artefacts.py` verifie les ARTEFACTS produits par le rejeu.
Personne ne verifiait les DONNEES qui les alimentent. Les deux pannes du
22/08/2026 venaient toutes deux de la source, pas du moteur :

- une barre DJ30.fs du 23/11/2009 portant ``low = 0.00``, authentique cote
  courtier, qui a arrete un rejeu de 149 symboles a 34 ;
- ``USDUSC``, archive vide (5 lignes, 5 reconstruites), qui l'aurait arrete a
  nouveau vers 130 si Prime ne l'avait pas sorti du lot a la main.

Aucune des deux n'etait detectable autrement qu'en tombant dessus.

Ce qu'il mesure, et la distinction qui compte
---------------------------------------------
Chaque defaut est compte deux fois : sur le FICHIER entier, et sur la FENETRE
que le rejeu lit reellement. Un defaut hors fenetre est une curiosite ; un
defaut dans la fenetre change un resultat. Confondre les deux fait soit paniquer
pour rien, soit passer a cote.

La fenetre du LTF est le fichier depuis sa borne utile. Celle du HTF est la
portee du LTF moins la marge de prechauffe, comme dans `rejeu_univers`.

Lecture seule. N'ecrit que son rapport.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.data.archive_barres import (  # noqa: E402
    ArchiveQualiteError,
    charger_barres,
    inventaire,
    resume,
)

DOSSIER = RACINE / "results" / "barres"

#: Pas nominal de chaque unite de temps, en secondes.
PAS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}

#: Marge de prechauffe du HTF, alignee sur `tools/rejeu_univers.py`.
MARGE_PRECHAUFFE_J = 1826


def _colonnes(chemin: Path) -> dict:
    t = pq.read_table(chemin, columns=["time_utc", "open", "high", "low",
                                       "close", "spread", "reconstruit"])
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
        "reconstruit": np.asarray(d["reconstruit"], dtype=bool)[ordre],
        "trie_a_l_origine": bool(np.all(np.diff(temps) > 0)),
    }


def _defauts(c: dict, debut: int, pas: int) -> dict:
    """Compte les defauts sur la tranche ``[debut:]`` de la serie."""
    t = c["time"][debut:]
    o, h, lo, cl = (c[k][debut:] for k in ("open", "high", "low", "close"))
    sp, rec = c["spread"][debut:], c["reconstruit"][debut:]
    n = int(t.size)
    if n == 0:
        return {"barres": 0, "vide": True}

    finies = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(cl)
    positives = (o > 0) & (h > 0) & (lo > 0) & (cl > 0)
    coherentes = (lo <= h) & (o >= lo) & (o <= h) & (cl >= lo) & (cl <= h)
    ohlc_ko = int(np.sum(~(finies & positives & coherentes)))

    d = np.diff(t)
    doublons = int(np.sum(d == 0))
    recul = int(np.sum(d < 0))

    # Barre a granularite trop grossiere : encadree de deux ecarts multiples
    # exacts du jour. Ce critere exclut les ponts et jours feries isoles, qui
    # produisent un seul grand ecart et non deux.
    grossiere = 0
    if pas < 86400 and d.size >= 2:
        jour = (d % 86400 == 0) & (d >= 86400)
        grossiere = int(np.sum(jour[:-1] & jour[1:]))

    return {
        "barres": n,
        "vide": False,
        "ohlc_invalides": ohlc_ko,
        "horodatage_duplique": doublons,
        "horodatage_en_recul": recul,
        "barres_grossieres": grossiere,
        "reconstruites": int(np.sum(rec)),
        "spread_negatif": int(np.sum(sp < 0)),
        "spread_nul": int(np.sum(sp == 0)),
        "premiere": int(t[0]),
        "derniere": int(t[-1]),
    }


def auditer(timeframes: list[str], ltf: str = "M15") -> dict:
    univers = sorted(inventaire(ltf))
    rapport: dict[str, dict] = {}

    # Portee du LTF par symbole : elle borne la fenetre utile du HTF.
    debut_ltf: dict[str, int] = {}
    for sym in univers:
        p = DOSSIER / ltf / f"{sym}.parquet"
        if not p.is_file():
            continue
        borne = int((resume(sym, ltf) or {}).get("index_premiere_utile", 0))
        t = np.asarray([int(x) for x in pq.read_table(
            p, columns=["time_utc"]).to_pydict()["time_utc"]], dtype=np.int64)
        t.sort()
        if t.size > borne:
            debut_ltf[sym] = int(t[borne])

    for tf in timeframes:
        pas = PAS[tf]
        lignes = {}
        for sym in univers:
            p = DOSSIER / tf / f"{sym}.parquet"
            if not p.is_file():
                lignes[sym] = {"absent": True}
                continue
            c = _colonnes(p)
            borne = int((resume(sym, tf) or {}).get("index_premiere_utile", 0))

            fichier = _defauts(c, 0, pas)
            # Fenetre lue par le rejeu : depuis la borne utile pour le LTF ;
            # pour un HTF, en plus depuis la portee du LTF moins la prechauffe.
            debut = borne
            if tf != ltf and sym in debut_ltf:
                seuil = debut_ltf[sym] - MARGE_PRECHAUFFE_J * 86400
                debut = max(borne, int(np.searchsorted(c["time"], seuil, "left")))
            fenetre = _defauts(c, debut, pas)

            lignes[sym] = {
                "absent": False,
                "trie": c["trie_a_l_origine"],
                "fichier": fichier,
                "fenetre": fenetre,
            }
        rapport[tf] = lignes
    return rapport


def _synthese(rapport: dict) -> None:
    cles = ("ohlc_invalides", "horodatage_duplique", "horodatage_en_recul",
            "barres_grossieres", "spread_negatif")
    print(f"{'UT':<4} {'symb':>5} {'absents':>8} {'vides':>6} {'non tries':>10}"
          f" {'OHLC ko':>9} {'doublons':>9} {'recul':>7} {'grossieres':>11}"
          f" {'spread<0':>9}")
    print("-" * 88)
    for portee in ("fichier", "fenetre"):
        print(f"  -- portee : {portee.upper()}")
        for tf, lignes in rapport.items():
            absents = sum(1 for v in lignes.values() if v.get("absent"))
            reels = [v for v in lignes.values() if not v.get("absent")]
            vides = sum(1 for v in reels if v[portee].get("vide"))
            non_tries = sum(1 for v in reels if not v["trie"])
            c = Counter()
            for v in reels:
                for k in cles:
                    if v[portee].get(k):
                        c[k] += 1
            print(f"{tf:<4} {len(reels):>5} {absents:>8} {vides:>6} {non_tries:>10}"
                  f" {c['ohlc_invalides']:>9} {c['horodatage_duplique']:>9}"
                  f" {c['horodatage_en_recul']:>7} {c['barres_grossieres']:>11}"
                  f" {c['spread_negatif']:>9}")
        print()


def _detail(rapport: dict, portee: str) -> None:
    print(f"=== symboles porteurs d'un defaut BLOQUANT dans la {portee} ===")
    trouve = False
    for tf, lignes in rapport.items():
        for sym, v in sorted(lignes.items()):
            if v.get("absent"):
                print(f"  {sym:<14} {tf:<3} ABSENT")
                trouve = True
                continue
            f = v[portee]
            if f.get("vide"):
                print(f"  {sym:<14} {tf:<3} FENETRE VIDE")
                trouve = True
                continue
            for k in ("ohlc_invalides", "horodatage_duplique",
                      "horodatage_en_recul", "spread_negatif"):
                if f.get(k):
                    print(f"  {sym:<14} {tf:<3} {k} = {f[k]}")
                    trouve = True
    if not trouve:
        print("  aucun")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--timeframes", nargs="*",
                    default=["M15", "H4", "H1", "D1"])
    ap.add_argument("--ltf", default="M15")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--chargeables", action="store_true",
                    help="tente charger_barres sur chaque couple (plus lent)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rapport = auditer(args.timeframes, ltf=args.ltf)
    _synthese(rapport)
    _detail(rapport, "fenetre")

    if args.chargeables:
        print()
        print("=== couples refuses par charger_barres ===")
        refus = 0
        for tf in args.timeframes:
            for sym in sorted(inventaire(args.ltf)):
                try:
                    charger_barres(sym, tf, fraicheur_max_s=None)
                except ArchiveQualiteError as e:
                    print(f"  {sym:<14} {tf:<3} {e}")
                    refus += 1
                except Exception as e:
                    print(f"  {sym:<14} {tf:<3} {type(e).__name__}: {e}")
                    refus += 1
        print(f"  total : {refus}")

    if args.json:
        args.json.write_text(json.dumps(rapport, indent=2), encoding="utf-8")
        print(f"\nrapport JSON : {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
