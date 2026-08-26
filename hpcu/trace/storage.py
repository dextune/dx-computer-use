"""Trace and verified-experience SQLite storage.

Trace rows and experience rows share one SQLite connection but use logically
separate schemas. Experience payloads are bounded, versioned, and corrupt rows
are skipped so one bad cache entry cannot block runtime boot.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from hpcu.schemas.trace import TraceEventType, TraceRecord


class TraceStorage:
    """SQLite-backed trace and verified-experience storage."""

    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                scene_version INTEGER NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                element_id TEXT,
                failure_code TEXT,
                model_call_count INTEGER NOT NULL DEFAULT 0,
                latency_us INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_traces_seq ON traces(seq);
            CREATE INDEX IF NOT EXISTS idx_traces_scene_version
                ON traces(scene_version);

            CREATE TABLE IF NOT EXISTS experiences (
                cache_key TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                updated_ns INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_experiences_updated
                ON experiences(updated_ns DESC);
        """)
        self._conn.commit()

    def insert(self, record: TraceRecord) -> None:
        self._conn.execute(
            """INSERT INTO traces
               (seq, event_type, timestamp_ns, scene_version, payload,
                element_id, failure_code, model_call_count, latency_us)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.seq,
                record.event_type.value,
                record.timestamp_ns,
                record.scene_version,
                json.dumps(record.payload),
                record.element_id,
                record.failure_code,
                record.model_call_count,
                record.latency_us,
            ),
        )
        self._conn.commit()

    def load_all(self) -> list[TraceRecord]:
        """Load all records ordered by seq."""
        rows = self._conn.execute("SELECT * FROM traces ORDER BY seq").fetchall()
        return [_row_to_record(r) for r in rows]

    def load_since(self, min_seq: int) -> list[TraceRecord]:
        """Load records with seq > min_seq."""
        rows = self._conn.execute(
            "SELECT * FROM traces WHERE seq > ? ORDER BY seq", (min_seq,)
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM traces").fetchone()
        return row["cnt"] if row else 0

    def upsert_experience(
        self,
        cache_key: str,
        payload: dict[str, Any],
        *,
        schema_version: int = 1,
        updated_ns: int | None = None,
        max_entries: int = 256,
    ) -> None:
        """Persist one versioned experience and enforce bounded retention."""
        if not cache_key.strip():
            raise ValueError("experience cache_key must be non-empty")
        if schema_version <= 0:
            raise ValueError("experience schema_version must be positive")
        if max_entries <= 0:
            raise ValueError("experience max_entries must be positive")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        timestamp = time.time_ns() if updated_ns is None else int(updated_ns)
        with self._conn:
            self._conn.execute(
                """INSERT INTO experiences(
                       cache_key, schema_version, payload, updated_ns
                   )
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       schema_version=excluded.schema_version,
                       payload=excluded.payload,
                       updated_ns=excluded.updated_ns""",
                (cache_key, schema_version, encoded, timestamp),
            )
            self._conn.execute(
                """DELETE FROM experiences
                   WHERE cache_key IN (
                       SELECT cache_key FROM experiences
                       ORDER BY updated_ns DESC, cache_key DESC
                       LIMIT -1 OFFSET ?
                   )""",
                (max_entries,),
            )

    def load_experiences(self) -> list[dict[str, Any]]:
        """Load valid experience payloads; silently isolate corrupt rows."""
        rows = self._conn.execute(
            """SELECT cache_key, schema_version, payload, updated_ns
               FROM experiences ORDER BY updated_ns DESC, cache_key DESC"""
        ).fetchall()
        loaded: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            loaded.append(
                {
                    "cache_key": row["cache_key"],
                    "schema_version": row["schema_version"],
                    "payload": payload,
                    "updated_ns": row["updated_ns"],
                }
            )
        return loaded

    def delete_experience(self, cache_key: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM experiences WHERE cache_key = ?", (cache_key,)
            )

    def experience_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM experiences"
        ).fetchone()
        return row["cnt"] if row else 0

    def close(self) -> None:
        self._conn.close()


def _row_to_record(row: sqlite3.Row) -> TraceRecord:
    return TraceRecord(
        seq=row["seq"],
        event_type=TraceEventType(row["event_type"]),
        timestamp_ns=row["timestamp_ns"],
        scene_version=row["scene_version"],
        payload=json.loads(row["payload"]),
        element_id=row["element_id"],
        failure_code=row["failure_code"],
        model_call_count=row["model_call_count"],
        latency_us=row["latency_us"],
    )
