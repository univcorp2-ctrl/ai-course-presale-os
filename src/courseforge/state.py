from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from courseforge.models import ReleaseManifest


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    task TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    paid INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS releases (
                    release_id TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def record_usage(self, *, provider_id: str, model: str, task: str, input_tokens: int, output_tokens: int, estimated_cost_usd: float, paid: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO usage_events
                (provider_id, model, task, input_tokens, output_tokens, estimated_cost_usd, paid, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (provider_id, model, task, input_tokens, output_tokens, estimated_cost_usd, 1 if paid else 0, datetime.now(timezone.utc).isoformat()),
            )

    def paid_tokens_since(self, since: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS total
                FROM usage_events WHERE paid = 1 AND created_at >= ?""",
                (since.isoformat(),),
            ).fetchone()
        return int(row["total"] if row else 0)

    def provider_tokens_since(self, provider_id: str, since: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS total
                FROM usage_events WHERE provider_id = ? AND created_at >= ?""",
                (provider_id, since.isoformat()),
            ).fetchone()
        return int(row["total"] if row else 0)

    def save_manifest(self, manifest: ReleaseManifest) -> None:
        payload = manifest.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO releases (release_id, manifest_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(release_id) DO UPDATE SET
                manifest_json = excluded.manifest_json, updated_at = excluded.updated_at""",
                (manifest.release_id, payload, datetime.now(timezone.utc).isoformat()),
            )

    def load_manifest(self, release_id: str) -> ReleaseManifest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM releases WHERE release_id = ?", (release_id,)
            ).fetchone()
        return ReleaseManifest.model_validate(json.loads(row["manifest_json"])) if row else None
