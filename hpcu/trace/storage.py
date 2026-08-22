"""Trace storage — SQLite backend for execution traces.

The same schema is used for both the experience store and trace replay.
"""

import json
import sqlite3
from typing import Optional

from hpcu.schemas.trace import TraceEventType, TraceRecord


class TraceStorage:
    """SQLite-backed trace storage.

    Supports both in-memory (:memory:) and file-backed databases.
    """

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
            CREATE INDEX IF NOT EXISTS idx_traces_scene_version ON traces(scene_version);
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
        rows = self._conn.execute(
            "SELECT * FROM traces ORDER BY seq"
        ).fetchall()
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