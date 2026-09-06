from pathlib import Path


def test_every_index_pass_keeps_canonical_skill_catalog():
    root = Path(__file__).resolve().parents[1]
    script = (root / "tools/gitnexus_team.ps1").read_text(encoding="utf-8")
    invocations = [
        line.strip()
        for line in script.splitlines()
        if line.strip().startswith('& node ".gitnexus\\run.cjs" analyze')
    ]
    assert len(invocations) == 2
    assert all("--skip-skills" in line for line in invocations)
