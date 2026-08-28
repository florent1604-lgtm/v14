"""Memoire centrale causale directement lue par le moteur V14.

Ce module ne connait ni MT5, ni ordre, ni taille, ni stop. Il transporte des
faits et propositions scelles entre organes, avec idempotence et chaine de
hash. Toute anomalie est fail-closed et journalisee separement.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from titanium.organism.contracts import DecisionIdentity, digest

SCHEMA_VERSION = 1
PROPOSAL_FIELDS = frozenset({
    "decision_ref", "context_digest", "symbol", "side", "bar_time",
    "model_version", "prompt_version", "evidence_digest", "action",
    "confidence", "summary", "sources", "rendered_at",
})


class CentralMemory:
    """Journal SQLite/WAL append-only commun a la boucle et au cortex."""

    def __init__(self, path: Path, alerts_path: Path | None = None):
        self.path = Path(path)
        self.alerts_path = Path(alerts_path or self.path.with_suffix(".alerts.ndjson"))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                decision_ref TEXT NOT NULL,
                symbol TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_decision "
            "ON events(decision_ref, kind, seq)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_symbol "
            "ON events(symbol, kind, seq)"
        )
        return db

    def append(self, kind: str, decision_ref: str, symbol: str,
               payload: Mapping[str, Any]) -> bool:
        """Ajoute un evenement idempotent; False signifie deja present."""
        payload_dict = dict(payload)
        payload_sha = digest(payload_dict)
        event_id = digest({"kind": kind, "decision_ref": decision_ref,
                           "payload_sha256": payload_sha})
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(previous[0]) if previous else "GENESIS"
            event_hash = digest({
                "event_id": event_id, "kind": kind,
                "decision_ref": decision_ref, "symbol": symbol,
                "payload_sha256": payload_sha, "previous_hash": previous_hash,
                "created_at": created_at,
            })
            cursor = db.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, kind, decision_ref, symbol, payload_json,
                    payload_sha256, previous_hash, event_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, kind, decision_ref, symbol,
                 json.dumps(payload_dict, ensure_ascii=False, sort_keys=True),
                 payload_sha, previous_hash, event_hash, created_at),
            )
            return cursor.rowcount == 1

    def record_request(self, identity: DecisionIdentity, payload: Mapping[str, Any]) -> bool:
        return self.append("engine.request", identity.decision_ref,
                           identity.symbol, payload)

    def record_proposal(self, identity: DecisionIdentity,
                        payload: Mapping[str, Any]) -> bool:
        proposal = dict(payload)
        unknown = set(proposal) - PROPOSAL_FIELDS
        if unknown:
            raise ValueError(f"champs de proposition interdits: {sorted(unknown)}")
        return self.append("brain.proposal", identity.decision_ref,
                           identity.symbol, proposal)

    def proposal_for(self, identity: DecisionIdentity) -> tuple[dict | None, str]:
        """Rend uniquement la proposition exacte, sinon un motif d'alerte."""
        try:
            with self._connect() as db:
                row = db.execute(
                    """SELECT payload_json, payload_sha256 FROM events
                       WHERE decision_ref=? AND kind='brain.proposal'
                       ORDER BY seq DESC LIMIT 1""",
                    (identity.decision_ref,),
                ).fetchone()
                if row is None:
                    stale = db.execute(
                        """SELECT decision_ref FROM events
                           WHERE symbol=? AND kind='brain.proposal'
                           ORDER BY seq DESC LIMIT 1""",
                        (identity.symbol,),
                    ).fetchone()
                    return None, ("BRAIN_PROPOSAL_STALE" if stale
                                  else "BRAIN_PROPOSAL_PENDING")
            payload = json.loads(row[0])
            if digest(payload) != row[1]:
                return None, "BRAIN_PROPOSAL_CORRUPT"
            expected = identity.to_dict()
            if any(payload.get(key) != value for key, value in expected.items()):
                return None, "BRAIN_IDENTITY_MISMATCH"
            if not str(payload.get("evidence_digest", "")):
                return None, "BRAIN_EVIDENCE_UNSEALED"
            if set(payload) - PROPOSAL_FIELDS:
                return None, "BRAIN_SCHEMA_INVALID"
            return payload, "BRAIN_PROPOSAL_EXACT"
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return None, "CENTRAL_MEMORY_UNAVAILABLE"

    def alert(self, code: str, identity: DecisionIdentity, detail: str = "") -> None:
        """Alerte idempotente dans le noyau et dans un NDJSON lisible."""
        payload = {"code": code, **identity.to_dict(), "detail": detail[:240]}
        try:
            inserted = self.append("system.alert", identity.decision_ref,
                                   identity.symbol, payload)
            if not inserted:
                return
            self.alerts_path.parent.mkdir(parents=True, exist_ok=True)
            row = {"at": datetime.now(timezone.utc).isoformat(), **payload}
            with self.alerts_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        except (OSError, sqlite3.Error):
            return

    def health(self) -> dict:
        """Etat factuel du noyau pour la supervision, sans mutation metier."""
        if not self.path.exists():
            return {"state": "EMPTY", "events": 0, "alerts": 0,
                    "proposals": 0, "last_alert": ""}
        try:
            with self._connect() as db:
                events = int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
                proposals = int(db.execute(
                    "SELECT COUNT(*) FROM events WHERE kind='brain.proposal'"
                ).fetchone()[0])
                alerts = int(db.execute(
                    "SELECT COUNT(*) FROM events WHERE kind='system.alert'"
                ).fetchone()[0])
                row = db.execute(
                    """SELECT payload_json FROM events
                       WHERE kind='system.alert' ORDER BY seq DESC LIMIT 1"""
                ).fetchone()
            last = json.loads(row[0]).get("code", "") if row else ""
            return {"state": "HEALTHY", "events": events, "alerts": alerts,
                    "proposals": proposals, "last_alert": last}
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return {"state": "FAILED", "events": 0, "alerts": 0,
                    "proposals": 0, "last_alert": "CENTRAL_MEMORY_UNAVAILABLE"}
