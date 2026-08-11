"""Arrete les services de V14, arbre de processus compris.

    python tools/arreter_services.py

Pourquoi tuer l'ARBRE et non le processus
------------------------------------------
Le `python.exe` du venv est un **lanceur-relais** : il demarre un second
processus, l'interpreteur systeme, qui fait le travail. Tuer le parent seul
laisse l'enfant **orphelin et vivant** — il continue de trader, et il compte
desormais comme une racine independante.

Constate le 08/08/2026 : chaque tentative d'arret creait une nouvelle racine
au lieu d'en supprimer une, et trois boucles armees ont fini par tourner en
parallele sur le meme compte.

L'ordre compte
--------------
La boucle d'abord : elle seule peut ouvrir des positions. Le tableau de bord
en dernier, pour qu'il reste disponible pendant l'arret.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Ordre d'arret : le plus dangereux en premier.
ORDRE = ("live_demo", "analystes", "dashboard")
MOTIF = re.compile(r"tools[\\/](live_demo|dashboard|analystes)\.py")


def _scanner() -> list:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        d = json.loads(out) if out else []
        return [d] if isinstance(d, dict) else d
    except Exception:  # noqa: BLE001
        return []


def _tuer(pids) -> None:
    """`Stop-Process -Force` sur chaque pid. Plus fiable que `taskkill`.

    `taskkill /F /T` a echoue a supprimer certains arbres le 08/08/2026 ;
    `Stop-Process` les a tous eus. On passe tous les pids d'un coup, parent
    et enfant, pour qu'aucun orphelin ne survive.
    """
    if not pids:
        return
    liste = ",".join(str(p) for p in pids)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Stop-Process -Id {liste} -Force -ErrorAction SilentlyContinue"],
        capture_output=True, timeout=60)


def main() -> int:
    procs = _scanner()
    par_service: dict = {}
    for p in procs:
        m = MOTIF.search(p.get("CommandLine") or "")
        if m:
            par_service.setdefault(m.group(1), []).append(p["ProcessId"])

    if not par_service:
        print("  [--]  aucun service en cours")
        return 0

    for service in ORDRE:
        pids = par_service.get(service) or []
        if not pids:
            print(f"  [--]  {service:<12} deja arrete")
            continue
        _tuer(pids)
        print(f"  [ok]  {service:<12} {len(pids)} processus supprime(s)")

    # Verification : un arret qui laisse des survivants n'est pas un arret.
    time.sleep(3)
    restants = [p["ProcessId"] for p in _scanner()
                if MOTIF.search(p.get("CommandLine") or "")]
    if restants:
        print(f"  ...   {len(restants)} survivant(s), seconde passe")
        _tuer(restants)
        time.sleep(3)
        restants = [p["ProcessId"] for p in _scanner()
                    if MOTIF.search(p.get("CommandLine") or "")]

    if restants:
        print(f"  [!]   {len(restants)} processus resistent : {restants}")
        print("        Les fermer a la main avant tout redemarrage.")
        return 1

    print("  [ok]  plus aucun processus V14")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
