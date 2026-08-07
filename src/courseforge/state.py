from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
                CREATE TABLE IF NOT EXISTS processed_webhooks (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS enrollments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    last_error TEXT,
                    UNIQUE(email, release_id)
                );
                CREATE TABLE IF NOT EXISTS subscribers (
                    email TEXT PRIMARY KEY,
                    consent_at TEXT NOT NULL,
                    consent_source TEXT NOT NULL,
                    unsubscribe_token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    unsubscribed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS campaign_deliveries (
                    campaign_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    PRIMARY KEY(campaign_id, email)
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

    def record_paid_checkout(self, *, event_id: str, event_type: str, email: str, release_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO processed_webhooks (event_id, event_type, received_at) VALUES (?, ?, ?)",
                (event_id, event_type, now),
            )
            if inserted.rowcount == 0:
                return False
            connection.execute(
                """INSERT INTO enrollments (email, release_id, status, created_at)
                VALUES (?, ?, 'pending', ?)
                ON CONFLICT(email, release_id) DO NOTHING""",
                (email.casefold().strip(), release_id, now),
            )
        return True

    def list_pending_enrollments(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, email, release_id, status, created_at
                FROM enrollments WHERE status IN ('pending', 'failed')
                ORDER BY created_at ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_enrollment_sent(self, enrollment_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE enrollments SET status = 'sent', sent_at = ?, last_error = NULL
                WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(), enrollment_id),
            )

    def mark_enrollment_failed(self, enrollment_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE enrollments SET status = 'failed', last_error = ? WHERE id = ?",
                (error[:1000], enrollment_id),
            )

    def add_subscriber(self, *, email: str, consent_at: str, consent_source: str, unsubscribe_token: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            inserted = connection.execute(
                """INSERT OR IGNORE INTO subscribers
                (email, consent_at, consent_source, unsubscribe_token, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (email.casefold().strip(), consent_at, consent_source.strip(), unsubscribe_token, now),
            )
        return inserted.rowcount > 0

    def list_campaign_recipients(self, campaign_id: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT s.email, s.unsubscribe_token
                FROM subscribers AS s
                LEFT JOIN campaign_deliveries AS d
                  ON d.campaign_id = ? AND d.email = s.email
                WHERE s.unsubscribed_at IS NULL
                  AND (d.status IS NULL OR d.status != 'sent')
                ORDER BY s.created_at ASC
                LIMIT ?""",
                (campaign_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_campaign_result(self, *, campaign_id: str, email: str, release_id: str, status: str, message_id: str | None = None, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO campaign_deliveries
                (campaign_id, email, release_id, status, message_id, last_error, created_at, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, email) DO UPDATE SET
                  status = excluded.status,
                  message_id = excluded.message_id,
                  last_error = excluded.last_error,
                  sent_at = excluded.sent_at""",
                (
                    campaign_id,
                    email.casefold().strip(),
                    release_id,
                    status,
                    message_id,
                    (error or "")[:1000] or None,
                    now,
                    now if status == "sent" else None,
                ),
            )

    def unsubscribe(self, token: str) -> bool:
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE subscribers SET unsubscribed_at = ?
                WHERE unsubscribe_token = ? AND unsubscribed_at IS NULL""",
                (datetime.now(timezone.utc).isoformat(), token),
            )
        return updated.rowcount > 0
