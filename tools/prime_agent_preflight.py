"""Preflight Windows fail-closed pour Prime Agent V14.

Prime Agent 0.7.1 conserve des leases et des descripteurs de workers sur
disque. Une extinction brutale peut laisser un PID mort dans ces fichiers ; le
daemon tente alors de les reutiliser et Windows refuse le renommage atomique
avec ``EPERM``. Ce module ne tue aucun processus et ne supprime rien : il met
uniquement en quarantaine les artefacts dont l'identite de processus est
prouvee morte (PID absent ou PID reutilise avec une autre date de demarrage).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WINDOWS_TO_DOTNET_TICKS = 504_911_232_000_000_000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_CANDIDATE_PID = re.compile(r"\.candidate-(\d+)-")


def process_start_id(pid: int) -> str | None:
    """Retourne l'identite de demarrage Prime, ou ``None`` si le PID est mort."""

    if pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return None
        return f"pid:{pid}"

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        from ctypes import wintypes

        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        filetime = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return f"win:{filetime + WINDOWS_TO_DOTNET_TICKS}"
    finally:
        kernel32.CloseHandle(handle)


def owner_is_alive(
    owner: dict[str, Any],
    lookup: Callable[[int], str | None] = process_start_id,
) -> bool:
    """Verifie le PID et sa date de demarrage pour eviter les PID reutilises."""

    try:
        pid = int(owner["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    actual = lookup(pid)
    if actual is None:
        return False
    expected = str(owner.get("processStartId") or "")
    return not expected or expected == actual


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _unique_destination(directory: Path, name: str) -> Path:
    candidate = directory / name
    index = 1
    while candidate.exists():
        candidate = directory / f"{name}.{index}"
        index += 1
    return candidate


def _quarantine(path: Path, destination: Path, *, dry_run: bool) -> Path:
    target = _unique_destination(destination, path.name)
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    return target


def quarantine_stale_leases(
    agent_dir: Path,
    *,
    lookup: Callable[[int], str | None] = process_start_id,
    stamp: str | None = None,
    dry_run: bool = False,
) -> list[tuple[Path, Path]]:
    """Isole les leases morts ; un owner illisible reste en place fail-closed."""

    leases = agent_dir / "session-leases"
    if not leases.is_dir():
        return []
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = agent_dir / "quarantine" / stamp / "session-leases"
    moved: list[tuple[Path, Path]] = []

    for lease in sorted(leases.glob("*.lock")):
        owner = _read_json(lease / "owner.json")
        if owner is None or owner_is_alive(owner, lookup):
            continue
        moved.append((lease, _quarantine(lease, destination, dry_run=dry_run)))

    for candidate in sorted(leases.glob("*.lock.candidate-*")):
        match = _CANDIDATE_PID.search(candidate.name)
        if match is None or lookup(int(match.group(1))) is not None:
            continue
        moved.append((candidate, _quarantine(candidate, destination, dry_run=dry_run)))
    return moved


def quarantine_stale_workers(
    agent_dir: Path,
    *,
    lookup: Callable[[int], str | None] = process_start_id,
    stamp: str | None = None,
    dry_run: bool = False,
) -> list[tuple[Path, Path]]:
    """Isole les descripteurs de workers morts et leurs journaux associes."""

    workers_root = agent_dir / "daemon-workers"
    if not workers_root.is_dir():
        return []
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    moved: list[tuple[Path, Path]] = []

    for supervisor in sorted(path for path in workers_root.iterdir() if path.is_dir()):
        if ".stale-" in supervisor.name:
            continue
        for descriptor in sorted(supervisor.glob("*.json")):
            metadata = _read_json(descriptor)
            if not metadata or "workerId" not in metadata or "pid" not in metadata:
                continue
            if owner_is_alive(metadata, lookup):
                continue
            worker_id = str(metadata["workerId"])
            destination = agent_dir / "quarantine" / stamp / "daemon-workers" / supervisor.name
            for artifact in sorted(supervisor.glob(f"{worker_id}*")):
                moved.append((artifact, _quarantine(artifact, destination, dry_run=dry_run)))
    return moved


def select_reusable_session(payload: dict[str, Any], cwd: Path) -> str | None:
    """Choisit une session saine de la meme racine, meme lorsqu'elle est au repos.

    Prime met ``isSessionActive`` a false pendant ``needs_input``. Le worker reste
    alors attachable et ``ready`` ; exiger ce drapeau creerait un second worker.
    """

    expected = os.path.normcase(os.path.abspath(str(cwd)))
    candidates: list[dict[str, Any]] = []
    for session in payload.get("sessions", []):
        if not isinstance(session, dict):
            continue
        actual = os.path.normcase(os.path.abspath(str(session.get("cwd") or "")))
        if actual != expected:
            continue
        if session.get("lifecycle") != "live":
            continue
        if str(session.get("workerState") or "").lower() not in {"ready", "working"}:
            continue
        session_id = str(session.get("id") or "")
        if not re.fullmatch(r"[0-9a-f]{8,64}", session_id):
            continue
        candidates.append(session)
    if not candidates:
        return None
    candidates.sort(key=lambda item: str(item.get("lastActivityAt") or ""), reverse=True)
    return str(candidates[0]["id"])


def list_sessions(timeout: float = 8.0) -> dict[str, Any]:
    executable = shutil.which("prime-agent") or shutil.which("prime-agent.cmd")
    if executable is None:
        raise RuntimeError("prime-agent introuvable dans le PATH")
    completed = subprocess.run(
        [executable, "list", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        summary = detail[-1] if detail else f"code {completed.returncode}"
        raise RuntimeError(f"etat Prime indisponible: {summary}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("reponse JSON Prime invalide") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("reponse JSON Prime inattendue")
    return payload


def _default_agent_dir() -> Path:
    configured = os.environ.get("PRIME_AGENT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".prime" / "agent"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-dir", type=Path, default=_default_agent_dir())
    subparsers = parser.add_subparsers(dest="command", required=True)
    repair = subparsers.add_parser("repair", help="quarantaine les artefacts orphelins")
    repair.add_argument("--dry-run", action="store_true")
    active = subparsers.add_parser("active", help="retourne une session reutilisable")
    active.add_argument("--cwd", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "repair":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        moved = quarantine_stale_leases(args.agent_dir, stamp=stamp, dry_run=args.dry_run)
        moved += quarantine_stale_workers(args.agent_dir, stamp=stamp, dry_run=args.dry_run)
        action = "detecte" if args.dry_run else "mis en quarantaine"
        print(f"Prime preflight: {len(moved)} artefact(s) orphelin(s) {action}.")
        for source, target in moved:
            print(f"  {source.name} -> {target}")
        return 0

    try:
        session_id = select_reusable_session(list_sessions(), args.cwd)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Prime preflight bloque: {exc}", file=sys.stderr)
        print("__ERROR__")
        return 3
    print(session_id or "__NONE__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
