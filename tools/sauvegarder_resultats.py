r"""Sauvegarde tournante des donnees de mesure V14.

    .venv\Scripts\python.exe tools\sauvegarder_resultats.py
    .venv\Scripts\python.exe tools\sauvegarder_resultats.py --keep 48
    .venv\Scripts\python.exe tools\sauvegarder_resultats.py --verifier

`results/` est ignore par git : sans cet instantane, une purge efface la
collecte sans recours. Le script ne modifie jamais la source.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.sauvegarde import (  # noqa: E402
    RETENTION_PAR_DEFAUT,
    SauvegardeError,
    instantanes,
    sauvegarder,
    verifier,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(RACINE / "results"))
    parser.add_argument("--destination", default=str(RACINE / "sauvegardes"))
    parser.add_argument("--keep", type=int, default=RETENTION_PAR_DEFAUT)
    parser.add_argument(
        "--verifier",
        action="store_true",
        help="ne sauvegarde rien : relit le dernier instantane et compare les empreintes",
    )
    args = parser.parse_args()

    try:
        if args.verifier:
            complets = instantanes(Path(args.destination))
            if not complets:
                print(json.dumps({"error": "aucun instantane complet"},
                                 ensure_ascii=False))
                return 2
            rapport = verifier(complets[-1])
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
            return 0 if rapport["ok"] else 1

        rapport = sauvegarder(
            Path(args.source), Path(args.destination), retention=args.keep)
    except SauvegardeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
