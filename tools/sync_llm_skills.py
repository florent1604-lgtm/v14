"""Synchronise le catalogue de skills V14 pour Claude, Codex et Hermes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".agents" / "skills"
TARGETS = {
    "claude": ROOT / ".claude" / "skills",
    "codex": ROOT / ".codex" / "skills",
    "hermes": ROOT / ".hermes" / "skills",
}
IGNORED_PARTS = {"__pycache__", ".pytest_cache"}


def files_under(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in IGNORED_PARTS for part in path.parts)
        and path.suffix != ".pyc"
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_catalog() -> list[Path]:
    if not SOURCE.is_dir():
        raise SystemExit(f"Catalogue canonique absent: {SOURCE}")
    skill_docs = sorted(SOURCE.rglob("SKILL.md"))
    if not skill_docs:
        raise SystemExit("Aucun SKILL.md dans le catalogue canonique")
    invalid = []
    for path in skill_docs:
        first_line = path.read_text(encoding="utf-8-sig").splitlines()[0:1]
        if first_line != ["---"]:
            invalid.append(path)
    if invalid:
        listed = "\n".join(str(path) for path in invalid)
        raise SystemExit(f"Frontmatter YAML absent:\n{listed}")
    return skill_docs


def sync_targets() -> None:
    source_files = files_under(SOURCE)
    for target in TARGETS.values():
        target.mkdir(parents=True, exist_ok=True)
        for relative, source_path in source_files.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)


def compare_target(target: Path) -> list[str]:
    source_files = files_under(SOURCE)
    target_files = files_under(target) if target.is_dir() else {}
    issues = []
    for relative in sorted(source_files.keys() - target_files.keys()):
        issues.append(f"manquant: {relative}")
    for relative in sorted(target_files.keys() - source_files.keys()):
        issues.append(f"en trop: {relative}")
    for relative in sorted(source_files.keys() & target_files.keys()):
        if sha256(source_files[relative]) != sha256(target_files[relative]):
            issues.append(f"different: {relative}")
    return issues


def write_manifest(skill_docs: list[Path]) -> Path:
    manifest_path = ROOT / "config" / "skills-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in skill_docs:
        text = path.read_text(encoding="utf-8-sig")
        name_line = next(
            (line for line in text.splitlines()[1:] if line.startswith("name:")),
            "name: unknown",
        )
        entries.append(
            {
                "name": name_line.split(":", 1)[1].strip(),
                "path": path.relative_to(SOURCE).as_posix(),
                "sha256": sha256(path),
            }
        )
    payload = {
        "version": 1,
        "source": "V12 .agents/skills plus collab-discipline, adapted for V14",
        "canonical_root": ".agents/skills",
        "platform_roots": {
            name: str(path.relative_to(ROOT)).replace("\\", "/")
            for name, path in TARGETS.items()
        },
        "skill_document_count": len(entries),
        "unique_skill_count": len({entry["name"] for entry in entries}),
        "skills": entries,
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="verifie les miroirs sans les modifier",
    )
    args = parser.parse_args()

    skill_docs = validate_catalog()
    if not args.check:
        sync_targets()
        write_manifest(skill_docs)

    failures = {}
    for name, target in TARGETS.items():
        issues = compare_target(target)
        if issues:
            failures[name] = issues

    if failures:
        for name, issues in failures.items():
            print(f"{name}: {len(issues)} anomalie(s)")
            for issue in issues[:20]:
                print(f"  - {issue}")
        return 1

    unique_names = set()
    for path in skill_docs:
        text = path.read_text(encoding="utf-8-sig")
        name_line = next(line for line in text.splitlines() if line.startswith("name:"))
        unique_names.add(name_line.split(":", 1)[1].strip())
    print(
        f"OK: {len(unique_names)} skills uniques, {len(skill_docs)} SKILL.md, "
        f"3 miroirs coherents"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
