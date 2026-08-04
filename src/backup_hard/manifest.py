"""Transactional SQLite state for resumable backup runs."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backup_hard.models import CopyResult, SourceIdentity


SCHEMA_VERSION = 1
SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
  backup_id TEXT PRIMARY KEY, tool_version TEXT NOT NULL, schema_version INTEGER NOT NULL,
  source_identity_json TEXT NOT NULL, status TEXT NOT NULL, policy_sha256 TEXT, plan_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS volumes (
  stable_id TEXT PRIMARY KEY, label TEXT NOT NULL, root TEXT NOT NULL, removable INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory_files (
  source_path TEXT PRIMARY KEY, volume_id TEXT NOT NULL, relative_path TEXT NOT NULL,
  filename TEXT NOT NULL, extension TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS classifications (
  source_path TEXT PRIMARY KEY, included INTEGER NOT NULL, category TEXT NOT NULL, reason TEXT NOT NULL,
  origin TEXT, media_type TEXT, package_name TEXT, origin_rule TEXT, archive_timestamp INTEGER,
  archive_month TEXT, date_source TEXT
);
CREATE TABLE IF NOT EXISTS destination_groups (
  group_key TEXT PRIMARY KEY, file_count INTEGER NOT NULL, split_required INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS destination_mappings (
  source_path TEXT PRIMARY KEY, destination_path TEXT NOT NULL UNIQUE, group_key TEXT NOT NULL,
  position INTEGER NOT NULL, part_number INTEGER, collision_index INTEGER NOT NULL,
  encoded_filename TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS copy_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_path TEXT NOT NULL, attempt INTEGER NOT NULL,
  status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS copy_results (
  source_path TEXT PRIMARY KEY, destination_path TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, detail TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS verification_results (
  source_path TEXT NOT NULL, destination_path TEXT NOT NULL, level TEXT NOT NULL,
  passed INTEGER NOT NULL, issue TEXT NOT NULL, detail TEXT NOT NULL,
  PRIMARY KEY (source_path, destination_path, level)
);
CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL, path TEXT NOT NULL,
  operation TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class ManifestError(RuntimeError):
    pass


class Manifest:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def create_run(self, backup_id: str, tool_version: str, identity: SourceIdentity) -> None:
        payload = json.dumps(identity.__dict__, sort_keys=True, ensure_ascii=False)
        try:
            with self.transaction() as connection:
                connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, 'planned', NULL, NULL)",
                    (backup_id, tool_version, SCHEMA_VERSION, payload),
                )
        except sqlite3.IntegrityError as exc:
            raise ManifestError(f"Run already exists: {backup_id}") from exc

    def record_copy_result(self, result: CopyResult) -> None:
        """Commit one completed result immediately for crash-safe resume."""
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO copy_results VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(source_path) DO UPDATE SET destination_path=excluded.destination_path,
                   status=excluded.status, attempts=excluded.attempts, size_bytes=excluded.size_bytes,
                   sha256=excluded.sha256, detail=excluded.detail, updated_at=CURRENT_TIMESTAMP""",
                (result.source_path, result.destination_path, result.status.value, result.attempts,
                 result.size_bytes, result.sha256, result.detail),
            )

    def copy_result(self, source_path: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM copy_results WHERE source_path = ?", (source_path,)
        ).fetchone()

    @staticmethod
    def canonical_digest(value: object) -> str:
        payload = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def lock_policy_and_plan(self, backup_id: str, policy: object, plan: object) -> tuple[str, str]:
        policy_digest = self.canonical_digest(policy)
        plan_digest = self.canonical_digest(plan)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT policy_sha256, plan_sha256 FROM runs WHERE backup_id = ?", (backup_id,)
            ).fetchone()
            if row is None:
                raise ManifestError(f"Unknown run: {backup_id}")
            existing = (row["policy_sha256"], row["plan_sha256"])
            if existing != (None, None) and existing != (policy_digest, plan_digest):
                raise ManifestError("Locked policy or plan digest changed")
            connection.execute(
                "UPDATE runs SET policy_sha256 = ?, plan_sha256 = ? WHERE backup_id = ?",
                (policy_digest, plan_digest, backup_id),
            )
        return policy_digest, plan_digest

    def assert_locked_digests(self, backup_id: str, policy: object, plan: object) -> None:
        row = self.connection.execute(
            "SELECT policy_sha256, plan_sha256 FROM runs WHERE backup_id = ?", (backup_id,)
        ).fetchone()
        expected = (self.canonical_digest(policy), self.canonical_digest(plan))
        if row is None or (row["policy_sha256"], row["plan_sha256"]) != expected:
            raise ManifestError("Policy or plan does not match the locked run")
