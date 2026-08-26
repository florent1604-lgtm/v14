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


def build_policy_identity(
    *,
    entry_policy: str,
    execution_mode: str,
    config: Mapping[str, Any],
    root: Path,
    code_paths: Sequence[str | Path],
) -> dict[str, str]:
    """Calcule une identite sans lire Git, l'environnement ou des secrets."""
    policy = str(entry_policy).strip().upper()
    mode = str(execution_mode).strip()
    if not policy or not mode:
        raise ValueError("entry_policy et execution_mode sont obligatoires")
    if not isinstance(config, Mapping) or not config:
        raise ValueError("projection de configuration vide")

    root = Path(root).resolve()
    inventory: dict[str, str] = {}
    for raw_path in code_paths:
        path = Path(raw_path)
        path = path if path.is_absolute() else root / path
        path = path.resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("fichier de code hors depot") from exc
        if not path.is_file():
            raise ValueError(f"fichier de code absent: {relative}")
        inventory[relative] = _sha256(path.read_bytes())
    if not inventory:
        raise ValueError("inventaire de code vide")

    config_sha256 = _sha256(_canonical_bytes(dict(config)))
    code_sha256 = _sha256(_canonical_bytes(inventory))
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
