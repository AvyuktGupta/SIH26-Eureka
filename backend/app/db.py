"""SQLite persistence: L2 RoadSegmentRisk rows, L1 cache, L4 time-series.

Only L4 and L6 (via get_risk_trend) read the time-series store, as specified.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from app.config import DATABASE_PATH


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingestion_cache (
                cache_key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                payload TEXT NOT NULL,
                fetched_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS road_segment_risk (
                session_id TEXT NOT NULL,
                h3_index TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (session_id, h3_index)
            );

            CREATE TABLE IF NOT EXISTS risk_timeseries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                h3_index TEXT NOT NULL,
                ts INTEGER NOT NULL,
                fused_score REAL NOT NULL,
                probability REAL NOT NULL,
                severity REAL NOT NULL,
                exposure REAL NOT NULL,
                confidence REAL NOT NULL,
                hysteresis_state TEXT NOT NULL,
                pending_ticks INTEGER NOT NULL,
                dominant_hazard TEXT NOT NULL,
                used_fallback INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_ts_lookup
                ON risk_timeseries (session_id, h3_index, ts);

            CREATE TABLE IF NOT EXISTS hysteresis_memory (
                session_id TEXT NOT NULL,
                h3_index TEXT NOT NULL,
                state TEXT NOT NULL,
                pending_ticks INTEGER NOT NULL,
                last_score REAL NOT NULL,
                PRIMARY KEY (session_id, h3_index)
            );
            """
        )
        conn.commit()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def cache_get(key: str, ttl_s: int) -> Optional[Any]:
    with db() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM ingestion_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    if time.time() - row["fetched_at"] > ttl_s:
        return None
    return json.loads(row["payload"])


def cache_get_last(key: str) -> tuple[Optional[Any], Optional[int]]:
    """Return last-known payload even if TTL expired (fallback path)."""
    with db() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM ingestion_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None, None
    return json.loads(row["payload"]), int(row["fetched_at"])


def cache_put(key: str, source: str, payload: Any) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ingestion_cache(cache_key, source, payload, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                source=excluded.source,
                payload=excluded.payload,
                fetched_at=excluded.fetched_at
            """,
            (key, source, json.dumps(payload), int(time.time())),
        )


def upsert_segments(session_id: str, rows: list[dict[str, Any]]) -> None:
    now = int(time.time())
    with db() as conn:
        conn.executemany(
            """
            INSERT INTO road_segment_risk(session_id, h3_index, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, h3_index) DO UPDATE SET
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            [(session_id, r["h3_index"], json.dumps(r), now) for r in rows],
        )


def load_segments(session_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT payload FROM road_segment_risk WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def append_risk(session_id: str, record: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO risk_timeseries(
                session_id, h3_index, ts, fused_score, probability, severity,
                exposure, confidence, hysteresis_state, pending_ticks,
                dominant_hazard, used_fallback
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                record["h3_index"],
                record.get("ts", int(time.time())),
                record["fused_score"],
                record["probability"],
                record["severity"],
                record["exposure"],
                record["confidence"],
                record["hysteresis_state"],
                record["pending_ticks"],
                record["dominant_hazard"],
                1 if record.get("used_fallback") else 0,
            ),
        )


def get_trend(session_id: str, h3_index: str, limit: int = 12) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT ts, fused_score, hysteresis_state, dominant_hazard, used_fallback
            FROM risk_timeseries
            WHERE session_id = ? AND h3_index = ?
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (session_id, h3_index, limit),
        ).fetchall()
    return [dict(r) for r in reversed(list(rows))]


def load_hysteresis(session_id: str) -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT h3_index, state, pending_ticks, last_score FROM hysteresis_memory WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    return {
        r["h3_index"]: {
            "state": r["state"],
            "pending_ticks": r["pending_ticks"],
            "last_score": r["last_score"],
        }
        for r in rows
    }


def save_hysteresis(session_id: str, memory: dict[str, dict[str, Any]]) -> None:
    with db() as conn:
        conn.executemany(
            """
            INSERT INTO hysteresis_memory(session_id, h3_index, state, pending_ticks, last_score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, h3_index) DO UPDATE SET
                state=excluded.state,
                pending_ticks=excluded.pending_ticks,
                last_score=excluded.last_score
            """,
            [
                (session_id, h3, m["state"], m["pending_ticks"], m["last_score"])
                for h3, m in memory.items()
            ],
        )
