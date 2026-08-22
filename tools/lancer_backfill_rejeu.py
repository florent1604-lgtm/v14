#!/usr/bin/env python
"""Lance le backfill du rejeu univers en plusieurs lots detaches.

Chaque lot est un processus independant qui parcourt sa part de l'univers.
Le decoupage est celui de ``tools/rejeu_univers.py`` (``--part``/``--sur``),
donc reprendre apres une coupure revient a relancer la meme commande : un
symbole dont le couple resume+brut est deja valide pour l'empreinte du moteur
courante est saute.

Les processus sont detaches volontairement : le backfill dure des heures et ne
doit pas mourir avec le terminal ou l'agent qui l'a lance.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
LOGS_DEFAUT = (RACINE / "collab" / "prime_agent" / "runs"
               / "strategie-entree-20260819" / "rejeu_univers_logs")
SENTINELLE = RACINE / "results" / "rejeu_univers_brut" / "_RUN_FAILED.json"

# Windows : le lot doit survivre a la fermeture du terminal parent.
DETACHE = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0


def lancer(lots: int, *, ltf: str, htf: str, pas: int, barres: int,
           prefixe: str, logs: Path, symboles: list[str] | None,
           refaire: bool) -> list[int]:
    logs.mkdir(parents=True, exist_ok=True)
    python = RACINE / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)

    pids: list[int] = []
    for part in range(lots):
        base = [
            str(python), "-X", "utf8", str(RACINE / "tools" / "rejeu_univers.py"),
            "--ltf", ltf, "--htf", htf, "--pas", str(pas),
            "--barres", str(barres), "--part", str(part), "--sur", str(lots),
        ]
        if refaire:
            base.append("--refaire")
        if symboles:
            base += ["--symboles", *symboles]
        # Les deux descripteurs sont fermes cote parent des le lancement : le
        # processus fils herite de sa propre copie, et un parent qui garde le
        # fichier ouvert empeche toute rotation ou tout nettoyage du journal.
        with (logs / f"{prefixe}_lot{part}.log").open("a", encoding="utf-8") as sortie, \
                (logs / f"{prefixe}_lot{part}.err.log").open("a", encoding="utf-8") as erreur:
            sortie.write(f"# lancement {datetime.now(timezone.utc).isoformat()}\n")
            sortie.flush()
            processus = subprocess.Popen(
                base, cwd=str(RACINE), stdout=sortie, stderr=erreur,
                stdin=subprocess.DEVNULL, creationflags=DETACHE)
        pids.append(processus.pid)
        print(f"lot {part + 1}/{lots} pid={processus.pid}", flush=True)
    return pids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lots", type=int, default=8)
    ap.add_argument("--ltf", default="M15")
    ap.add_argument("--htf", default="H4")
    ap.add_argument("--pas", type=int, default=1)
    ap.add_argument("--barres", type=int, default=0, help="0 = toute la profondeur")
    ap.add_argument("--prefixe", default="backfill_v3")
    ap.add_argument("--logs", type=Path, default=LOGS_DEFAUT)
    ap.add_argument("--symboles", nargs="*", default=None)
    ap.add_argument("--refaire", action="store_true")
    ap.add_argument(
        "--effacer-sentinelle", action="store_true",
        help="supprime _RUN_FAILED.json avant de lancer; sans elle, un echec "
             "precedent non examine bloque le lancement",
    )
    args = ap.parse_args()
    if args.lots < 1:
        ap.error("--lots doit etre >= 1")

    if SENTINELLE.is_file():
        if not args.effacer_sentinelle:
            print(f"ARRET: sentinelle presente {SENTINELLE}\n"
                  f"{SENTINELLE.read_text(encoding='utf-8')[:500]}", flush=True)
            return 2
        SENTINELLE.unlink()
        print("sentinelle effacee", flush=True)

    lancer(args.lots, ltf=args.ltf, htf=args.htf, pas=args.pas,
           barres=args.barres, prefixe=args.prefixe, logs=args.logs,
           symboles=args.symboles, refaire=args.refaire)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
