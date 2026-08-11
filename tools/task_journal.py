"""CLI commun Claude/Codex/Hermes/Prime pour le journal de taches V14."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.collab_tasks import TaskError, create_task, snapshot, update_task  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")

    create = sub.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--description", default="")
    create.add_argument("--owner", default="team")
    create.add_argument("--priority", default="P2")
    create.add_argument("--status", default="backlog")
    create.add_argument("--actor", required=True)

    update = sub.add_parser("update")
    update.add_argument("--id", required=True)
    update.add_argument("--status")
    update.add_argument("--owner")
    update.add_argument("--priority")
    update.add_argument("--note", default="")
    update.add_argument("--actor", required=True)

    args = parser.parse_args()
    try:
        if args.command == "list":
            result = snapshot()
        elif args.command == "create":
            result = create_task(vars(args))
        else:
            changes = {key: value for key, value in vars(args).items()
                       if key in {"status", "owner", "priority", "note", "actor"}
                       and value is not None}
            result = update_task(args.id, changes)
    except TaskError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
