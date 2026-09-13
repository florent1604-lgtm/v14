"""Supervision locale, sans MT5 ni ordre : diagnostic et reprise publique bornee."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.collecteur_microstructure import (  # noqa: E402
    SYMBOLS,
    collection_health,
    collector_lock,
)

NAMES = ("collecteur_microstructure", "enregistreur_quotes", "enregistreur_carnet_binance")
PUBLIC = NAMES[0]
STATE_DIR = ROOT / "results" / "supervision_collecteurs"
MAX_STARTS = 3
MIN_FREE_BYTES = 1024**3


def process_inventory(root: Path = ROOT) -> dict[str, list[int]]:
    """Refuse toute reprise si un processus Python ne peut etre identifie."""
    matched = {}
    for proc in psutil.process_iter():
        try:
            if proc.name().lower() not in ("python.exe", "pythonw.exe", "python", "python3"):
                continue
            args = proc.cmdline()
            if not args:
                raise RuntimeError("PYTHON_COMMAND_UNREADABLE")
            for arg in args[1:]:
                path = Path(arg)
                if path.name not in {f"{name}.py" for name in NAMES}:
                    continue
                script = path if path.is_absolute() else Path(proc.cwd()) / path
                if script.resolve() == (root / "tools" / path.name).resolve():
                    matched[proc.pid] = (path.stem, proc.ppid())
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as exc:
            raise RuntimeError("PROCESS_INVENTORY_UNAVAILABLE") from exc
    result: dict[str, list[int]] = {name: [] for name in NAMES}
    for pid, (name, parent) in matched.items():
        if parent not in matched or matched[parent][0] != name:
            result[name].append(pid)
    return result


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def read_attempts(path: Path, now: float) -> list[float]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "v14.collector-restarts.v1":
        raise ValueError("INVALID_RESTART_STATE")
    attempts = data["attempts"]
    if not isinstance(attempts, list) or any(
        isinstance(t, bool) or not isinstance(t, (float, int))
        or not math.isfinite(t) or t < 0 or t > now for t in attempts
    ):
        raise ValueError("INVALID_RESTART_TIMESTAMPS")
    if attempts != sorted(attempts):
        raise ValueError("UNSORTED_RESTART_TIMESTAMPS")
    return [t for t in attempts if now - t < 3600]


def restart_decision(pids: list[int], attempts: list[float], now: float,
                     free_bytes: int) -> str:
    if len(pids) > 1:
        return "DUPLICATE_NO_ACTION"
    if pids:
        return "RUNNING_NO_ACTION"
    if free_bytes < MIN_FREE_BYTES:
        return "LOW_DISK_NO_ACTION"
    if len(attempts) >= MAX_STARTS:
        return "RESTART_BUDGET_EXHAUSTED"
    delay = min(900, 60 * 2 ** max(0, len(attempts) - 1))
    if attempts and now - attempts[-1] < delay:
        return "RESTART_BACKOFF"
    return "START_ALLOWED"


def start_public(state_dir: Path = STATE_DIR) -> int:
    """Commande fixe, jamais de lanceur metier ou de commande fournie par un fichier."""
    interpreter = ROOT / ".venv" / "Scripts" / "python.exe"
    if not interpreter.is_file():
        raise RuntimeError("VENV_MISSING")
    log = state_dir / f"public_{time.time_ns()}.log"
    with log.open("ab") as output:
        child = subprocess.Popen(
            [str(interpreter), "-X", "utf8", str(ROOT / "tools/collecteur_microstructure.py")],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    return child.pid


def supervise(*, apply: bool = False, state_dir: Path = STATE_DIR,
              now: float | None = None) -> dict:
    now = time.time() if now is None else now
    report = {"schema": "v14.collector-supervision.v1", "observed_ms": now * 1000,
              "status": "WAIT", "action": "NONE", "trading_control": False}
    # Le verrou couvre inventaire, reservation durable et lancement.
    with collector_lock(state_dir):
        try:
            inventory = process_inventory()
            report["processes"] = inventory
            report["microstructure"] = collection_health(
                SYMBOLS, ROOT / "results/microstructure", now_ms=now * 1000,
            )
            report["archive_collectors"] = {
                name: {"process_count": len(inventory[name]), "data_quality": "NOT_CHECKED",
                       "restart": "MANUAL_ONLY"} for name in NAMES[1:]
            }
            attempts = read_attempts(state_dir / "restarts.json", now)
            free = shutil.disk_usage(ROOT).free
            report["free_bytes"] = free
            decision = restart_decision(inventory[PUBLIC], attempts, now, free)
            report["action"] = decision
            if decision == "START_ALLOWED" and apply:
                # Reserver AVANT Popen : meme un crash ne cree pas de boucle de reprises.
                atomic_json(state_dir / "restarts.json", {
                    "schema": "v14.collector-restarts.v1", "attempts": [*attempts, now],
                })
                report["started_pid"] = start_public(state_dir)
                report["action"] = "STARTED_AWAITING_DATA"
            elif decision == "START_ALLOWED":
                report["action"] = "STOPPED_OBSERVE_ONLY"
            if (decision == "RUNNING_NO_ACTION"
                    and free >= MIN_FREE_BYTES
                    and report["microstructure"]["status"] == "OK"
                    and all(len(pids) <= 1 for pids in inventory.values())):
                report["status"] = "OK_PUBLIC_ONLY"
        except Exception as exc:  # noqa: BLE001 - echec ferme, pas de details sensibles
            report.update(status="WAIT", action="ERROR_NO_RETRY", error=type(exc).__name__)
        atomic_json(state_dir / "health.json", report)
        with (state_dir / "events.ndjson").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, allow_nan=False) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-public", action="store_true",
                        help="reprise bornee du seul collecteur public, sans MT5")
    parser.add_argument("--watch", action="store_true", help="controle toutes les 60 secondes")
    args = parser.parse_args(argv)
    # Un second verrou couvre la duree du daemon, pas seulement chaque passe.
    try:
        with collector_lock(STATE_DIR / "runner"):
            while True:
                report = supervise(apply=args.apply_public)
                if sys.stdout is not None:
                    print(json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)
                if not args.watch:
                    return 0 if report["status"] == "OK_PUBLIC_ONLY" else 1
                time.sleep(60)
    except (OSError, ValueError):
        return 2  # verrou ou stockage indisponible : ne rien lancer


if __name__ == "__main__":
    raise SystemExit(main())
