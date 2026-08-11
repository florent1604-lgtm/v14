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
MOTIF = re.compile(r"tools[\\/](live_demo|dashboard|analystes)\.py")


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


def racines() -> dict:
    """``{service: [pid racine, …]}``. Une racine = une instance réelle."""
    procs = _processus()
    pertinents = {}
    for p in procs:
        m = MOTIF.search(p.get("CommandLine") or "")
        if m:
            pertinents[p["ProcessId"]] = (m.group(1), p.get("ParentProcessId"))

    out: dict = {s: [] for s in SERVICES}
    for pid, (service, parent) in pertinents.items():
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

    if len(r.get("live_demo") or []) > 1:
        print()
        print("  ⚠️  PLUSIEURS BOUCLES ARMEES sur le meme compte.")
        print("      L'idempotence est par processus : elles peuvent envoyer")
        print("      le meme ordre et doubler la position.")
        print("      Corriger : ARRETER_V14.bat puis DEMARRER_V14.bat")

    return 1 if souci else 0


if __name__ == "__main__":
    raise SystemExit(main())
