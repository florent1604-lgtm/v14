#!/usr/bin/env python
"""Barre de progression du rejeu de l'univers (lecture seule).

Le rejeu tourne en lots paralleles pendant des heures et n'ecrit qu'un JSON
par symbole termine. Sans cette vue, l'avancement ne se lit qu'en comptant des
fichiers a la main, et un lot mort ressemble a un lot lent.

Usage :
    python tools/rejeu_progression.py               # instantane
    python tools/rejeu_progression.py --suivre      # rafraichi jusqu'a Ctrl-C
    python tools/rejeu_progression.py --suivre --intervalle 30
    python tools/rejeu_progression.py --json        # sortie machine
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
ARCHIVE = RACINE / "results" / "barres"
DEST = RACINE / "results" / "rejeu_univers"
LOGS = (RACINE / "collab" / "prime_agent" / "runs" / "strategie-entree-20260819"
        / "rejeu_univers_logs")

# Un lot est declare silencieux au-dela de ce delai sans ecriture dans son log.
# Un symbole prend environ une heure : deux heures de silence, c'est anormal.
SILENCE_ALERTE_S = 2 * 3600


def inventaire(ltf: str = "M15") -> list[str]:
    dossier = ARCHIVE / ltf
    if not dossier.is_dir():
        return []
    return sorted(p.stem for p in dossier.glob("*.parquet"))


def faits() -> dict[str, dict]:
    if not DEST.is_dir():
        return {}
    sorties: dict[str, dict] = {}
    for chemin in DEST.glob("*.json"):
        try:
            sorties[chemin.stem] = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sorties[chemin.stem] = {}
    return sorties


def _symbole_en_cours(chemin: Path) -> str:
    """Dernier symbole annonce par un log, "" si le lot est entre deux symboles."""
    try:
        brut = chemin.read_text(encoding="utf-8", errors="replace")
        lignes = [x for x in brut.splitlines() if x.strip()]
    except OSError:
        return ""
    for ligne in reversed(lignes):
        if ligne.endswith("en cours..."):
            return ligne.split()[0]
        if " deja fait" in ligne or " n=" in ligne or " ECHEC " in ligne:
            return ""
    return ""


def lots_actifs() -> list[dict]:
    """Lots REELLEMENT en vie, apparies a leur log par le descripteur ouvert.

    Se fier a la fraicheur des logs est faux : les relances successives laissent
    des logs recents dont le processus a ete tue, et un lot legitime peut rester
    muet une heure entiere pendant qu'il calcule un symbole. La seule preuve de
    vie est la table des processus ; le fichier de sortie redirige est encore
    ouvert par le processus, ce qui donne l'appariement exact log <-> lot.
    """
    try:
        import psutil
    except ImportError:  # vue degradee, annoncee comme telle
        return [{"nom": "?", "symbole": "", "age_s": 0.0, "pid": 0,
                 "silencieux": False, "log": "psutil absent"}]

    maintenant = time.time()
    par_log: dict[str, dict] = {}
    orphelins: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmdline = proc.info["cmdline"] or []
            if not any("rejeu_univers.py" in str(a) for a in cmdline):
                continue
            if "python" not in (proc.info["name"] or "").lower():
                continue
            journal = ""
            try:
                for ouvert in proc.open_files():
                    chemin = Path(ouvert.path)
                    if LOGS not in chemin.parents:
                        continue
                    # Le meme processus tient aussi son flux d'erreur ouvert :
                    # le lot se lit dans la sortie standard, pas dans le .err.
                    if ".err" in chemin.name:
                        journal = journal or str(chemin)
                        continue
                    journal = str(chemin)
                    break
            except (psutil.AccessDenied, OSError):
                pass
            args = [a for a in cmdline if a.startswith("--part")] or cmdline[-2:]
            entree = {
                "pid": proc.info["pid"],
                "age_s": round(maintenant - proc.info["create_time"], 1),
                "args": " ".join(str(a) for a in args)[:40],
            }
            if journal:
                chemin = Path(journal)
                entree.update({
                    "nom": chemin.stem,
                    "log": str(chemin.relative_to(RACINE)),
                    "symbole": _symbole_en_cours(chemin),
                    "muet_s": round(maintenant - chemin.stat().st_mtime, 1),
                })
                # Deux processus partagent le meme log (le python du venv est un
                # relais) : un seul lot, celui qui a le plus tourne.
                garde = par_log.get(journal)
                if garde is None or entree["age_s"] > garde["age_s"]:
                    par_log[journal] = entree
            else:
                entree.update({"nom": f"pid{proc.info['pid']}", "log": "", "symbole": "",
                               "muet_s": 0.0})
                orphelins.append(entree)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    lots = list(par_log.values())
    vus = {(x["args"], x["age_s"]) for x in lots}
    for x in orphelins:  # lot sans log identifie et non deja compte
        if (x["args"], x["age_s"]) not in vus:
            lots.append(x)
    for lot in lots:
        lot["silencieux"] = lot.get("muet_s", 0.0) > SILENCE_ALERTE_S
    return sorted(lots, key=lambda x: (x["nom"], x["pid"]))


def barre(part: float, largeur: int = 44) -> str:
    plein = int(round(part * largeur))
    return "#" * plein + "-" * (largeur - plein)


def etat(ltf: str = "M15") -> dict:
    univers = inventaire(ltf)
    sorties = faits()
    connus = [s for s in univers if s in sorties]
    restants = [s for s in univers if s not in sorties]
    lots = lots_actifs()
    en_cours = sorted({x["symbole"] for x in lots if x["symbole"] and x["symbole"] not in sorties})

    # Regime COURANT et non moyenne de l'histoire : la duree par symbole depend
    # du nombre de lots qui se partagent la machine, et ce nombre a change en
    # cours de route. On ne garde donc que les derniers resultats ecrits.
    recents = sorted(DEST.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:12]
    durees = [sorties[x.stem].get("secondes") for x in recents
              if isinstance(sorties.get(x.stem, {}).get("secondes"), (int, float))]
    duree_med = sorted(durees)[len(durees) // 2] if durees else 0.0
    parallelisme = max(1, len([x for x in lots if not x["silencieux"]]))
    eta_s = (len(restants) / parallelisme) * duree_med if duree_med else 0.0

    dernier = ""
    if sorties:
        chemin = max(DEST.glob("*.json"), key=lambda p: p.stat().st_mtime)
        horodatage = datetime.fromtimestamp(chemin.stat().st_mtime, tz=timezone.utc)
        dernier = horodatage.isoformat(timespec="seconds")

    positifs = 0
    positifs_deux_tiers = 0
    for d in sorties.values():
        ver = (d.get("verification") or {}).get("esperance_r")
        cal = (d.get("calibration") or {}).get("esperance_r")
        if isinstance(ver, (int, float)) and ver > 0:
            positifs += 1
            if isinstance(cal, (int, float)) and cal > 0:
                positifs_deux_tiers += 1

    fin = datetime.now(timezone.utc) + timedelta(seconds=eta_s)
    return {
        "ltf": ltf,
        "univers": len(univers),
        "faits": len(connus),
        "restants": len(restants),
        "part": (len(connus) / len(univers)) if univers else 0.0,
        "en_cours": en_cours,
        "lots": lots,
        "duree_mediane_s": round(duree_med, 1),
        "parallelisme": parallelisme,
        "eta_s": round(eta_s),
        "fin_estimee": fin.isoformat(timespec="seconds") if eta_s else "",
        "dernier_resultat_utc": dernier,
        "positifs_verification": positifs,
        "positifs_calibration_et_verification": positifs_deux_tiers,
        "symboles_restants": restants,
    }


def rendu(e: dict) -> str:
    heures = e["eta_s"] / 3600
    lignes = [
        f"REJEU UNIVERS  {e['ltf']}  {datetime.now().strftime('%d/%m %H:%M:%S')}",
        f"[{barre(e['part'])}] {e['part'] * 100:5.1f}%   {e['faits']}/{e['univers']} symboles"
        f"   restants {e['restants']}",
        f"lots actifs {e['parallelisme']}   mediane/symbole {e['duree_mediane_s'] / 60:.0f} min"
        + (f"   fin estimee dans {heures:.1f} h ({e['fin_estimee'][11:16]}Z)"
           if e["eta_s"] else ""),
        f"dernier resultat ecrit : {e['dernier_resultat_utc'] or '-'}",
        f"positifs : {e['positifs_calibration_et_verification']}/{e['faits']}"
        f" en calibration ET verification"
        f"  ({e['positifs_verification']} en verification seule)",
        "",
    ]
    for lot in e["lots"]:
        marque = "  SILENCIEUX" if lot["silencieux"] else ""
        symbole = lot["symbole"] or "(rien en cours)"
        lignes.append(f"  {lot['nom']:12} {symbole:12} depuis {lot['age_s'] / 60:5.0f} min{marque}")
    return "\n".join(lignes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ltf", default="M15")
    ap.add_argument("--suivre", action="store_true", help="rafraichir jusqu'a Ctrl-C")
    ap.add_argument("--intervalle", type=float, default=60.0)
    ap.add_argument("--json", action="store_true", help="sortie machine")
    args = ap.parse_args()

    if args.json:
        print(json.dumps(etat(args.ltf), indent=1, ensure_ascii=False))
        return 0
    if not args.suivre:
        print(rendu(etat(args.ltf)))
        return 0
    try:
        while True:
            e = etat(args.ltf)
            print("\033[2J\033[H" + rendu(e), flush=True)
            if e["restants"] == 0:
                print("\nTERMINE.", flush=True)
                return 0
            time.sleep(args.intervalle)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
