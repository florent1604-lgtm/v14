from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / ".agents" / "skills"
MIRRORS = (
    ROOT / ".claude" / "skills",
    ROOT / ".codex" / "skills",
    ROOT / ".hermes" / "skills",
)


def _files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_catalog_contains_all_v12_skills_with_valid_frontmatter() -> None:
    documents = sorted(CANONICAL.rglob("SKILL.md"))
    names = set()
    for document in documents:
        lines = document.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == "---", document
        name = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("name:"))
        names.add(name)

    assert len(documents) == 48
    assert len(names) == 48
    assert {"trading-team", "collab-discipline"} <= names


def test_claude_codex_and_hermes_mirrors_match_canonical() -> None:
    expected = _files(CANONICAL)
    expected_hashes = {relative: _hash(path) for relative, path in expected.items()}

    for mirror in MIRRORS:
        actual = _files(mirror)
        assert set(actual) == set(expected), mirror
        assert {relative: _hash(path) for relative, path in actual.items()} == expected_hashes


def test_platform_instructions_keep_v14_safety_authority() -> None:
    documents = (
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "collab" / "HERMES_LIMITES.md",
    )
    for document in documents:
        text = document.read_text(encoding="utf-8").lower()
        assert ".env" in text
        assert "ordre reel" in text or "ordre sur un compte reel" in text
        assert "skill" in text
