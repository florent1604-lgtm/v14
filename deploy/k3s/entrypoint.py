"""Select exactly one bounded container role."""

from __future__ import annotations

import os
import sys


def command_for_role(role: str) -> list[str]:
    port = os.getenv("PORT", "8080")
    commands = {
        "api": [
            sys.executable,
            "-m",
            "uvicorn",
            "titanium.web.live_app:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--workers",
            "1",
        ],
        "deepseek-worker": [sys.executable, "-X", "utf8", "tools/analystes.py"],
        "mt5-demo-adapter": [
            sys.executable,
            "-m",
            "uvicorn",
            "titanium.web.mt5_adapter:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--workers",
            "1",
        ],
    }
    try:
        return commands[role]
    except KeyError as exc:
        raise ValueError(f"role K3s inconnu: {role}") from exc


def main() -> None:
    command = command_for_role(os.getenv("TITANIUM_ROLE", ""))
    os.execv(command[0], command)


if __name__ == "__main__":
    main()

