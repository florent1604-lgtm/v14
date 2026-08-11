from __future__ import annotations

import json
import subprocess
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
COLLAB_URL = "http://127.0.0.1:8770/mcp"
HERMES_URL = "http://127.0.0.1:8766/mcp"


def test_claude_and_codex_share_v12_collaboration_endpoints() -> None:
    claude = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    with (ROOT / ".codex" / "config.toml").open("rb") as handle:
        codex = tomllib.load(handle)["mcp_servers"]

    assert claude["collab_hub"] == {"type": "http", "url": COLLAB_URL}
    assert codex["collab_hub"] == {"url": COLLAB_URL}
    assert claude["hermes"] == {"type": "http", "url": HERMES_URL}
    assert codex["hermes"] == {"url": HERMES_URL}


def test_fallback_bus_reuses_v12_and_rejects_secrets(tmp_path: Path) -> None:
    script = ROOT / "tools" / "collab_bus.mjs"
    bus_file = tmp_path / "bus.ndjson"
    env = {"COLLAB_BUS_FILE": str(bus_file)}

    import os

    full_env = os.environ.copy()
    full_env.update(env)
    sent = subprocess.run(
        [
            "node",
            str(script),
            "send",
            "--from",
            "codex",
            "--to",
            "claude",
            "--task",
            "V14-TEST",
            "--content",
            "Objectif: test local. Livrable: accusé de réception.",
        ],
        cwd=ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sent.returncode == 0, sent.stderr
    assert json.loads(sent.stdout)["to"] == "claude"

    refused = subprocess.run(
        [
            "node",
            str(script),
            "send",
            "--from",
            "codex",
            "--to",
            "hermes",
            "--content",
            "api_key=valeur_interdite",
        ],
        cwd=ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 1
    assert "secret" in refused.stderr.lower()


def test_launcher_only_manages_hermes_and_collab_hub() -> None:
    source = (ROOT / "tools" / "collab_services.ps1").read_text(encoding="utf-8")

    assert 'Name = "hermes-mcp"' in source
    assert "Port = 8766" in source
    assert 'Name = "collab-hub"' in source
    assert "Port = 8770" in source
    assert "titanium-mcp" not in source
    assert "gitnexus-write-gate" not in source
