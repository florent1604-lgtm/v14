"""Lance le tableau de bord FastAPI V14, local et sans autorité d'exécution."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8096)
    parser.add_argument("--reload", action="store_true", help="rechargement développement")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("le dashboard de trading doit rester lié à localhost")
    if args.port == 8095:
        parser.error("le port 8095 est réservé au dashboard historique")
    uvicorn.run(
        "titanium.web.live_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        access_log=False,
    )


if __name__ == "__main__":
    main()
