"""Identite reproductible de la politique qui produit une decision d'entree.

Le journal ne stocke jamais la configuration en clair : il porte seulement
des empreintes de fichiers et de valeurs non secretes. Toute variation ouvre
automatiquement une nouvelle epoque de politique.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

IDENTITY_SCHEMA_VERSION = "v14.execution-policy-identity.v1"
CODE_SNAPSHOT_SCHEMA_VERSION = "v14.execution-code-snapshot.v1"
IDENTITY_FIELDS = (
    "entry_policy",
    "execution_mode",
    "policy_epoch",
    "config_sha256",
    "code_sha256",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def snapshot_code_identity(
    *, root: Path, code_sources: Sequence[str | Path],
) -> dict[str, Any]:
    """Fige au démarrage l'inventaire versionné des sources de décision."""
    root = Path(root).resolve()
    files: set[Path] = set()
    for raw_path in code_sources:
        path = Path(raw_path)
        path = path if path.is_absolute() else root / path
        path = path.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("source de code hors depot") from exc
        if path.is_dir():
            files.update(item.resolve() for item in path.rglob("*.py"))
        elif path.is_file():
            files.add(path)
        else:
            raise ValueError(f"source de code absente: {path}")
    if not files:
        raise ValueError("inventaire de code vide")
    inventory = {
        path.relative_to(root).as_posix(): _sha256(path.read_bytes())
        for path in sorted(files)
    }
    return {
        "schema_version": CODE_SNAPSHOT_SCHEMA_VERSION,
        "code_sha256": _sha256(_canonical_bytes(inventory)),
        "inventory": inventory,
    }


def build_policy_identity(
    *,
    entry_policy: str,
    execution_mode: str,
    config: Mapping[str, Any],
    base_code_snapshot: Mapping[str, Any],
) -> dict[str, str]:
    """Combine le snapshot de démarrage et la config sans relire le disque."""
    policy = str(entry_policy).strip().upper()
    mode = str(execution_mode).strip()
    if not policy or not mode:
        raise ValueError("entry_policy et execution_mode sont obligatoires")
    if not isinstance(config, Mapping) or not config:
        raise ValueError("projection de configuration vide")

    if base_code_snapshot.get("schema_version") != CODE_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("snapshot de code incompatible")
    code_sha256 = str(base_code_snapshot.get("code_sha256") or "").lower()
    if len(code_sha256) != 64 or any(c not in "0123456789abcdef" for c in code_sha256):
        raise ValueError("snapshot de code invalide")

    config_sha256 = _sha256(_canonical_bytes(dict(config)))
    epoch_payload = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "entry_policy": policy,
        "execution_mode": mode,
        "config_sha256": config_sha256,
        "code_sha256": code_sha256,
    }
    return {
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "entry_policy": policy,
        "execution_mode": mode,
        "policy_epoch": _sha256(_canonical_bytes(epoch_payload))[:16],
        "config_sha256": config_sha256,
        "code_sha256": code_sha256,
    }
