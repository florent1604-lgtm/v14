"""Les services de V14 tournent-ils, et en un seul exemplaire ?

    python tools/etat_services.py

Code de sortie : 0 = sain, 1 = doublon ou service manquant.

Pourquoi compter les RACINES et non les processus
--------------------------------------------------
Le `python.exe` du venv est un **lanceur-relais** : il démarre un second
processus (l'interpréteur système) qui fait le travail. Un service apparaît
donc toujours en DEUX processus, parent et enfant.

Compter les processus fait donc voir un doublon là où il n'y en a pas — et
inversement, masque un vrai doublon derrière un chiffre pair. Le 08/08/2026,
trois boucles armées tournaient simultanément et le comptage naïf en
annonçait deux.

C'est loin d'être anodin : l'idempotence par barre est **par processus**.
Deux boucles armées sur le même compte peuvent envoyer le même ordre au même
instant et doubler la position, exactement le défaut qui avait triplé le
risque sur AUDUSD le 07/08.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVICES = ("live_demo", "dashboard", "analystes")

#: Collecteurs de données de marché. Ils sont suivis à part des services, et
#: pour une raison précise : leur arrêt n'est pas une panne de V14, alors qu'un
#: service arrêté l'est. Les mêler ferait échouer `DEMARRER_V14.bat` à chaque
#: fois qu'une collecte est volontairement à l'arrêt.
#:
#: Le DOUBLON, lui, est aussi grave ici qu'ailleurs : deux enregistreurs de
#: carnet écrivent deux fois les mêmes différentiels dans le même fichier
#: append-only, et une archive dédoublée ne se répare pas — les numéros de
#: séquence deviennent ininterprétables.
COLLECTEURS = ("enregistreur_quotes", "enregistreur_carnet_binance")

MOTIF = re.compile(
    r"tools[\\/](live_demo|dashboard|analystes"
    r"|enregistreur_quotes|enregistreur_carnet_binance)\.py")


def _processus() -> list:
    """Processus Python et leur parent. Rend [] si le scan échoue."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId,ParentProcessId,CommandLine | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        d = json.loads(out) if out else []
        return [d] if isinstance(d, dict) else d
    except Exception:  # noqa: BLE001
        return []


def racines(noms: tuple[str, ...] = SERVICES) -> dict:
    """``{service: [pid racine, …]}``. Une racine = une instance réelle.

    ``noms`` vaut les trois services par défaut : le tableau de bord et
    `DEMARRER_V14.bat` lisent ce retour et jugent la santé de V14 dessus.
    Y glisser les collecteurs changerait leur verdict sans qu'ils l'aient
    demandé — d'où le paramètre plutôt qu'une constante élargie.
    """
    procs = _processus()
    pertinents = {}
    for p in procs:
        m = MOTIF.search(p.get("CommandLine") or "")
        if m:
            pertinents[p["ProcessId"]] = (m.group(1), p.get("ParentProcessId"))

    out: dict = {s: [] for s in noms}
    for pid, (service, parent) in pertinents.items():
        if service not in out:
            continue
        # Un processus dont le PARENT est lui aussi un processus du service
        # est un relais, pas une instance.
        if parent not in pertinents:
            out[service].append(pid)
    return out


def main() -> int:
    r = racines()
    souci = False
    print("services :")
    for s in SERVICES:
        pids = sorted(r.get(s) or [])
        if not pids:
            print(f"  {s:<12} ARRETE")
            souci = True
        elif len(pids) == 1:
            print(f"  {s:<12} ok        pid {pids[0]}")
        else:
            print(f"  {s:<12} DOUBLON   {len(pids)} instances {pids}")
            souci = True

    c = racines(COLLECTEURS)
    print()
    print("collecteurs de marche :")
    for s in COLLECTEURS:
        pids = sorted(c.get(s) or [])
        if not pids:
            # Une collecte a l'arret n'est pas une panne de V14 : on le dit
            # sans faire echouer le demarrage.
            print(f"  {s:<28} arrete")
        elif len(pids) == 1:
            print(f"  {s:<28} ok        pid {pids[0]}")
        else:
            print(f"  {s:<28} DOUBLON   {len(pids)} instances {pids}")
            print("      Deux collecteurs ecrivent le meme fichier append-only :")
            print("      l'archive se dedouble et ne se repare pas.")
            print("      Corriger : fermer une des fenetres de collecte.")
            # Le code de sortie appartient aux TROIS SERVICES et a eux seuls.
            # `DEMARRER_V14.bat` s'arrete quand ce script rend 0 ("ils tournent
            # deja"). Faire echouer le diagnostic pour un doublon de collecteur
            # ferait croire au .bat que V14 est a l'arret : il lancerait une
            # SECONDE boucle armee sur le meme compte. C'est exactement
            # l'incident du 08/08/2026, et un archiveur en double ne vaut pas
            # ce risque.

    if len(r.get("live_demo") or []) > 1:
        print()
        print("  ⚠️  PLUSIEURS BOUCLES ARMEES sur le meme compte.")
        print("      L'idempotence est par processus : elles peuvent envoyer")
        print("      le meme ordre et doubler la position.")
        print("      Corriger : ARRETER_V14.bat puis DEMARRER_V14.bat")

    return 1 if souci else 0


if __name__ == "__main__":
    raise SystemExit(main())
