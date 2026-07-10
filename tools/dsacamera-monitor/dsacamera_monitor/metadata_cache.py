"""Durable SQLite cache for incremental HDF5 pointing metadata reads."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MetadataCache:
    """Small transactional cache keyed by the HDF5 filename."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> MetadataCache:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pointing_metadata (
                filename TEXT PRIMARY KEY,
                t_mid_utc TEXT,
                ra_deg REAL,
                dec_deg REAL,
                dec_status TEXT NOT NULL,
                pointing_status TEXT NOT NULL,
                error TEXT,
                attempt_count INTEGER NOT NULL,
                last_attempt_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
        self.connection = connection
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("metadata cache is not open")
        return self.connection

    def load_rows(self) -> dict[str, dict[str, Any]]:
        """Return every cached row keyed by filename."""
        rows = self._connection().execute(
            """
            SELECT filename, t_mid_utc, ra_deg, dec_deg, dec_status,
                   pointing_status, error, attempt_count, last_attempt_at
            FROM pointing_metadata
            """
        )
        return {str(row["filename"]): dict(row) for row in rows}

    def write_attempts(self, records: list[dict[str, Any]], attempted_at: datetime) -> None:
        """Commit one metadata batch atomically, incrementing prior attempt counts."""
        if not records:
            return
        attempted_at_iso = _iso_utc(attempted_at)
        connection = self._connection()
        with connection:
            connection.executemany(
                """
                INSERT INTO pointing_metadata (
                    filename, t_mid_utc, ra_deg, dec_deg, dec_status,
                    pointing_status, error, attempt_count, last_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    t_mid_utc = excluded.t_mid_utc,
                    ra_deg = excluded.ra_deg,
                    dec_deg = excluded.dec_deg,
                    dec_status = excluded.dec_status,
                    pointing_status = excluded.pointing_status,
                    error = excluded.error,
                    attempt_count = pointing_metadata.attempt_count + 1,
                    last_attempt_at = excluded.last_attempt_at
                """,
                [
                    (
                        record["filename"],
                        record.get("t_mid_utc"),
                        record.get("ra_deg"),
                        record.get("dec_deg"),
                        record["dec_status"],
                        record["pointing_status"],
                        record.get("error"),
                        attempted_at_iso,
                    )
                    for record in records
                ],
            )
