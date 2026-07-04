# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# infrastructure/database/data_registry.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 5).
"""Data registry database module.

    Provides functions for tracking all data instances through their lifecycle
    from staging to published. The schema is managed by Alembic migration
    006_data_registry_tables.py and stored in the unified pipeline.sqlite3.

    Tables
------
    data_registry
    Central registry tracking status (staging/publishing/published)
    data_relationships
    Parent-child relationships between data products
    data_tags
    Flexible tagging for organization and search

    Usage
-----
    from dsa110_contimg.infrastructure.database.data_registry import get_data_registry_connection
    conn = get_data_registry_connection()  # Uses pipeline.sqlite3
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DataRecord:
    """Data registry record."""

    id: int
    data_type: str
    data_id: str
    base_path: str
    status: str  # 'staging', 'publishing', or 'published'
    stage_path: str
    published_path: str | None
    created_at: float
    staged_at: float
    published_at: float | None
    publish_mode: str | None  # 'auto' or 'manual'
    metadata_json: str | None
    qa_status: str | None
    validation_status: str | None
    finalization_status: str  # 'pending', 'finalized', 'failed'
    auto_publish_enabled: bool
    publish_attempts: int = 0
    publish_error: str | None = None
    photometry_status: str | None = None
    photometry_job_id: str | None = None


def ensure_data_registry_db(path: Path) -> sqlite3.Connection:
    """Open connection to the data registry tables in pipeline.sqlite3.

    The schema is managed by Alembic migrations. This function verifies
    the tables exist and returns a connection.

    Tables (see migration 006_data_registry_tables.py):
      - data_registry: Central registry of all data instances
      - data_relationships: Relationships between data instances
      - data_tags: Tags for organization/search

    Parameters
    ----------
    path : Path
        Path to the SQLite database file (should be pipeline.sqlite3).

    Returns
    -------
    sqlite3.Connection
        Connection to the database with data_registry tables.


    """
    path = Path(path)
    if not path.exists():
        raise RuntimeError(
            f"Database file does not exist: {path}. "
            "Run Alembic migrations first: alembic upgrade head"
        )

    conn = sqlite3.connect(os.fspath(path))

    # Verify schema exists (tables should be created by Alembic migration)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='data_registry'"
    )
    if cursor.fetchone() is None:
        conn.close()
        raise RuntimeError(
            f"data_registry table not found in {path}. "
            "Run Alembic migrations: cd backend/src/dsa110_contimg/database && "
            "alembic upgrade head"
        )

    return conn


def get_data_registry_connection() -> sqlite3.Connection:
    """Get connection to the default data registry database.

    Returns
    -------
        Connection to the data registry table in pipeline.sqlite3

    """
    from dsa110_continuum.database.unified import get_pipeline_db_path

    db_path = get_pipeline_db_path()
    return ensure_data_registry_db(db_path)


def register_data(
    conn: sqlite3.Connection,
    data_type: str,
    data_id: str,
    stage_path: str,
    metadata: dict[str, Any] | None = None,
    auto_publish: bool = True,
) -> str:
    """Register a new data instance in the registry.

    Parameters
    ----------
    conn :
        Database connection
    data_type :
        Type of data ('ms', 'calib_ms', 'image', 'mosaic', etc.)
    data_id :
        Unique identifier for this data instance
    stage_path :
        Path in staging area
    metadata :
        Optional metadata dictionary (will be JSON-encoded)
    auto_publish :
        Whether auto-publish is enabled for this instance
    conn: sqlite3.Connection :

    Returns
    -------
        data_id (same as input)

    """
    now = time.time()
    metadata_json = json.dumps(metadata) if metadata else None

    conn.execute(
        """
        INSERT OR REPLACE INTO data_registry
        (data_type, data_id, base_path, status, stage_path, created_at, staged_at,
         metadata_json, auto_publish_enabled, finalization_status)
        VALUES (?, ?, ?, 'staging', ?, ?, ?, ?, ?, 'pending')
        """,
        (
            data_type,
            data_id,
            str(Path(stage_path).parent),  # base_path is parent directory
            stage_path,
            now,
            now,
            metadata_json,
            1 if auto_publish else 0,
        ),
    )
    conn.commit()
    return data_id


def finalize_data(
    conn: sqlite3.Connection,
    data_id: str,
    qa_status: str | None = None,
    validation_status: str | None = None,
) -> bool:
    """Mark data as finalized and trigger auto-publish if enabled and criteria met.

    Parameters
    ----------
    conn :
        Database connection
    data_id :
        Data instance ID
    qa_status :
        QA status ('pending', 'passed', 'failed', 'warning')
    validation_status :
        Validation status ('pending', 'validated', 'invalid')
    conn: sqlite3.Connection :

    Returns
    -------
        True if finalized (and possibly auto-published), False otherwise

    """
    cur = conn.cursor()

    # CRITICAL: Whitelist allowed column names to prevent SQL injection
    # Even though values are parameterized, column names must be whitelisted
    ALLOWED_UPDATE_COLUMNS = {
        "finalization_status",
        "qa_status",
        "validation_status",
    }

    # Update finalization status and QA/validation if provided
    updates = []
    params = []

    # Always set finalization_status
    updates.append("finalization_status = ?")
    params.append("finalized")

    # Add optional updates only if column is whitelisted
    if qa_status and "qa_status" in ALLOWED_UPDATE_COLUMNS:
        updates.append("qa_status = ?")
        params.append(qa_status)

    if validation_status and "validation_status" in ALLOWED_UPDATE_COLUMNS:
        updates.append("validation_status = ?")
        params.append(validation_status)

    # Add data_id for WHERE clause
    params.append(data_id)

    if updates:
        cur.execute(
            f"UPDATE data_registry SET {', '.join(updates)} WHERE data_id = ?",
            tuple(params),
        )

    # Check if auto-publish should be triggered
    cur.execute(
        """
        SELECT auto_publish_enabled, qa_status, validation_status, data_type, stage_path
        FROM data_registry
        WHERE data_id = ?
        """,
        (data_id,),
    )
    row = cur.fetchone()

    if not row:
        conn.commit()
        return False

    auto_enabled, qa, validation, dtype, stage_path = row

    if auto_enabled:
        # Check criteria (simplified - will be enhanced with config)
        should_publish = True
        if validation != "validated":
            should_publish = False

        # For science data types, require QA passed
        if dtype in ("image", "mosaic", "calib_ms", "caltable"):
            if qa != "passed":
                should_publish = False

        if should_publish:
            # Trigger auto-publish
            trigger_auto_publish(conn, data_id)
            conn.commit()
            return True

    conn.commit()
    return True


def trigger_auto_publish(
    conn: sqlite3.Connection,
    data_id: str,
    products_base: Path | None = None,
    max_attempts: int = 3,
) -> bool:
    """Trigger auto-publish for a data instance.

    Moves data from staging (SSD) to persistent storage (HDD).

    Uses database-level locking (SELECT FOR UPDATE) to prevent concurrent access race conditions.
    Implements retry tracking with exponential backoff for transient failures.

    Parameters
    ----------
    conn :
        Database connection
    data_id :
        Data instance ID
    products_base :
        Base path for published products (defaults to /data/dsa110-contimg/products)
    max_attempts :
        Maximum number of publish attempts (default: 3)
    conn: sqlite3.Connection :

    Returns
    -------
        True if successful, False otherwise

    """
    if products_base is None:
        products_base = Path(os.getenv("CONTIMG_PRODUCTS_BASE", "/data/dsa110-contimg/products"))

    cur = conn.cursor()

    # CRITICAL: Use BEGIN IMMEDIATE to prevent concurrent publish attempts
    # BEGIN IMMEDIATE acquires an exclusive lock, preventing race conditions
    try:
        # Start transaction with immediate lock
        conn.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT data_type, stage_path, base_path, publish_attempts, status
            FROM data_registry
            WHERE data_id = ? AND status IN ('staging', 'publishing')
            """,
            (data_id,),
        )
        row = cur.fetchone()

        if not row:
            conn.rollback()
            logger.warning(f"Data {data_id} not found or already published")
            return False

        data_type, stage_path, base_path, publish_attempts, status = row

        # Check if already publishing (another process has the lock)
        if status == "publishing":
            conn.rollback()
            logger.debug(f"Data {data_id} is already being published by another process")
            return False

        # Check if max attempts exceeded
        if publish_attempts and publish_attempts >= max_attempts:
            conn.rollback()
            logger.warning(
                f"Data {data_id} has exceeded max publish attempts ({publish_attempts}/{max_attempts}). "
                f"Manual intervention required."
            )
            return False

        # Set status to 'publishing' to prevent concurrent attempts
        cur.execute(
            """
            UPDATE data_registry
            SET status = 'publishing'
            WHERE data_id = ?
            """,
            (data_id,),
        )
        conn.commit()  # Commit the lock

    except sqlite3.OperationalError as e:
        conn.rollback()
        logger.error(f"Failed to acquire lock for {data_id}: {e}")
        return False
    except Exception as e:
        conn.rollback()
        logger.error(f"Unexpected error acquiring lock for {data_id}: {e}")
        return False

    # Determine published path based on data type
    type_to_dir = {
        "ms": "ms",
        "calib_ms": "calib_ms",
        "caltable": "caltables",
        "image": "images",
        "mosaic": "mosaics",
        "catalog": "catalogs",
        "qa": "qa",
        "metadata": "metadata",
    }

    # CRITICAL: Prevent publishing data types that should stay on SSD
    # raw_ms files should never be moved to HDD - they stay on /stage/ for processing
    if data_type == "raw_ms":
        conn.rollback()
        logger.warning(
            f"Cannot publish {data_id}: raw_ms type should not be published. "
            f"Raw MS files stay on /stage/ for downstream processing."
        )
        return False

    type_dir = type_to_dir.get(data_type, "misc")
    published_dir = products_base / type_dir
    published_dir.mkdir(parents=True, exist_ok=True)

    # Move data (preserve directory structure)
    stage_path_obj = Path(stage_path).resolve()
    if not stage_path_obj.exists():
        error_msg = f"Stage path does not exist: {stage_path}"
        logger.error(error_msg)
        _record_publish_failure(conn, cur, data_id, publish_attempts, error_msg)
        return False

    # CRITICAL: Enhanced path validation using validate_path_safe helper
    from dsa110_continuum.database import data_config
    from dsa110_continuum.utils.naming import validate_path_safe

    expected_staging_base = data_config.STAGE_BASE
    is_safe, error_msg = validate_path_safe(stage_path_obj, expected_staging_base)
    if not is_safe:
        logger.error(f"Stage path validation failed for {data_id}: {error_msg}")
        _record_publish_failure(conn, cur, data_id, publish_attempts, error_msg)
        return False

    # Published path maintains same structure
    published_path = published_dir / stage_path_obj.name

    # CRITICAL: Check if published path already exists (could indicate duplicate or failed previous publish)
    if published_path.exists():
        logger.warning(
            f"Published path already exists: {published_path}. "
            f"This may indicate a duplicate publish or failed cleanup."
        )
        # For safety, append timestamp to avoid overwriting
        timestamp = int(time.time())
        published_path = published_dir / f"{stage_path_obj.stem}_{timestamp}{stage_path_obj.suffix}"

    # CRITICAL: Enhanced path validation for published path
    expected_products_base = Path(
        os.getenv("CONTIMG_PRODUCTS_BASE", "/data/dsa110-contimg/products")
    )
    is_safe, error_msg = validate_path_safe(published_path, expected_products_base)
    if not is_safe:
        logger.error(f"Published path validation failed for {data_id}: {error_msg}")
        _record_publish_failure(conn, cur, data_id, publish_attempts, error_msg)
        return False

    try:
        # Move directory/file
        if stage_path_obj.is_dir():
            shutil.move(str(stage_path_obj), str(published_path))
        else:
            published_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage_path_obj), str(published_path))

        # CRITICAL: Verify move succeeded before updating database
        if not published_path.exists():
            raise RuntimeError(
                f"Move appeared to succeed but destination does not exist: {published_path}"
            )
        if stage_path_obj.exists():
            raise RuntimeError(f"Move appeared to succeed but source still exists: {stage_path}")

        # Update database - clear publish error on success
        now = time.time()
        cur.execute(
            """
            UPDATE data_registry
            SET status = 'published',
                published_path = ?,
                published_at = ?,
                publish_mode = 'auto',
                publish_error = NULL,
                publish_attempts = 0
            WHERE data_id = ?
            """,
            (str(published_path.resolve()), now, data_id),
        )
        conn.commit()

        logger.info(f"Auto-published {data_id} from {stage_path} to {published_path}")
        return True

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to auto-publish {data_id}: {error_msg}", exc_info=True)
        _record_publish_failure(conn, cur, data_id, publish_attempts, error_msg)
        return False


def update_photometry_status(
    conn: sqlite3.Connection,
    data_id: str,
    status: str,
    job_id: str | None = None,
) -> bool:
    """Update photometry status for a data product.

    Parameters
    ----------
    conn :
        Database connection
    data_id :
        Data product ID
    status :
        Status ("pending", "running", "completed", "failed")
    job_id :
        Optional batch job ID
    conn: sqlite3.Connection :

    Returns
    -------
        True if updated successfully, False otherwise

    """
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE data_registry
            SET photometry_status = ?, photometry_job_id = ?
            WHERE data_id = ?
            """,
            (status, job_id, data_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            logger.warning(f"No data record found for data_id: {data_id}")
            return False
        return True
    except Exception as e:
        logger.error(f"Failed to update photometry status for {data_id}: {e}")
        conn.rollback()
        return False


def get_photometry_status(
    conn: sqlite3.Connection,
    data_id: str,
) -> dict[str, Any] | None:
    """Get photometry status for a data product.

    Parameters
    ----------
    conn :
        Database connection
    data_id :
        Data product ID
    conn: sqlite3.Connection :

    Returns
    -------
        Dict with "status" and "job_id" keys, or None if not found

    """
    try:
        cur = conn.cursor()
        # Try to select photometry columns (may not exist in older schemas)
        try:
            cur.execute(
                """
                SELECT photometry_status, photometry_job_id
                FROM data_registry
                WHERE data_id = ?
                """,
                (data_id,),
            )
        except sqlite3.OperationalError:
            # Columns don't exist yet
            return None

        row = cur.fetchone()
        if not row:
            return None

        status, job_id = row
        return {"status": status, "job_id": job_id}
    except Exception as e:
        logger.error(f"Failed to get photometry status for {data_id}: {e}")
        return None


def link_photometry_to_data(
    conn: sqlite3.Connection,
    data_id: str,
    photometry_job_id: str,
) -> bool:
    """Link a photometry job to a data product.

    Convenience function that calls update_photometry_status() with "pending" status.

    Parameters
    ----------
    conn :
        Database connection
    data_id :
        Data product ID
    photometry_job_id :
        Batch photometry job ID
    conn: sqlite3.Connection :

    Returns
    -------
        True if linked successfully, False otherwise

    """
    return update_photometry_status(
        conn=conn,
        data_id=data_id,
        status="pending",
        job_id=photometry_job_id,
    )


def _record_publish_failure(
    conn: sqlite3.Connection,
    cur: sqlite3.Cursor,
    data_id: str,
    current_attempts: int,
    error_msg: str,
) -> None:
    """Record a publish failure and update attempt counter.

    Parameters
    ----------
    conn :
        Database connection
    cur :
        Database cursor
    data_id :
        Data instance ID
    current_attempts :
        Current number of attempts
    error_msg :
        Error message to record
    conn: sqlite3.Connection :

    cur: sqlite3.Cursor :

    """
    try:
        new_attempts = (current_attempts or 0) + 1
        cur.execute(
            """
            UPDATE data_registry
            SET status = 'staging',
                publish_attempts = ?,
                publish_error = ?
            WHERE data_id = ?
            """,
            (new_attempts, error_msg[:500], data_id),  # Limit error message length
        )
        conn.commit()
        logger.debug(
            f"Recorded publish failure for {data_id}: attempt {new_attempts}, error: {error_msg[:100]}"
        )
    except Exception as e:
        logger.error(f"Failed to record publish failure for {data_id}: {e}")
        conn.rollback()


def publish_data_manual(
    conn: sqlite3.Connection,
    data_id: str,
    products_base: Path | None = None,
) -> bool:
    """Manually publish data (user-initiated).

    Parameters
    ----------
    conn :
        Database connection
    data_id :
        Data instance ID
    products_base :
        Base path for published products
    conn: sqlite3.Connection :

    Returns
    -------
        True if successful, False otherwise

    """
    if products_base is None:
        products_base = Path(os.getenv("CONTIMG_PRODUCTS_BASE", "/data/dsa110-contimg/products"))

    cur = conn.cursor()
    cur.execute(
        """
        SELECT data_type, stage_path
        FROM data_registry
        WHERE data_id = ? AND status = 'staging'
        """,
        (data_id,),
    )
    row = cur.fetchone()

    if not row:
        logger.warning(f"Data {data_id} not found or already published")
        return False

    data_type, stage_path = row

    # Use same logic as auto-publish for path determination
    type_to_dir = {
        "ms": "ms",
        "calib_ms": "calib_ms",
        "caltable": "caltables",
        "image": "images",
        "mosaic": "mosaics",
        "catalog": "catalogs",
        "qa": "qa",
        "metadata": "metadata",
    }

    type_dir = type_to_dir.get(data_type, "misc")
    published_dir = products_base / type_dir
    published_dir.mkdir(parents=True, exist_ok=True)

    stage_path_obj = Path(stage_path)
    if not stage_path_obj.exists():
        logger.error(f"Stage path does not exist: {stage_path}")
        return False

    published_path = published_dir / stage_path_obj.name

    try:
        if stage_path_obj.is_dir():
            shutil.move(str(stage_path_obj), str(published_path))
        else:
            published_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage_path_obj), str(published_path))

        now = time.time()
        cur.execute(
            """
            UPDATE data_registry
            SET status = 'published',
                published_path = ?,
                published_at = ?,
                publish_mode = 'manual'
            WHERE data_id = ?
            """,
            (str(published_path), now, data_id),
        )
        conn.commit()

        logger.info(f"Manually published {data_id} from {stage_path} to {published_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to manually publish {data_id}: {e}")
        conn.rollback()
        return False


def get_data(conn: sqlite3.Connection, data_id: str) -> DataRecord | None:
    """Get a data record by ID.

    Parameters
    ----------
    conn: sqlite3.Connection :

    """
    cur = conn.cursor()
    # Try to select with new columns, fall back to old columns if they don't exist
    try:
        cur.execute(
            """
            SELECT id, data_type, data_id, base_path, status, stage_path, published_path,
                   created_at, staged_at, published_at, publish_mode, metadata_json,
                   qa_status, validation_status, finalization_status, auto_publish_enabled,
                   publish_attempts, publish_error, photometry_status, photometry_job_id
            FROM data_registry
            WHERE data_id = ?
            """,
            (data_id,),
        )
        row = cur.fetchone()

        if not row:
            return None

        # Handle both old and new schema
        if len(row) >= 20:
            return DataRecord(
                id=row[0],
                data_type=row[1],
                data_id=row[2],
                base_path=row[3],
                status=row[4],
                stage_path=row[5],
                published_path=row[6],
                created_at=row[7],
                staged_at=row[8],
                published_at=row[9],
                publish_mode=row[10],
                metadata_json=row[11],
                qa_status=row[12],
                validation_status=row[13],
                finalization_status=row[14],
                auto_publish_enabled=bool(row[15]),
                publish_attempts=row[16] or 0,
                publish_error=row[17],
                photometry_status=row[18],
                photometry_job_id=row[19],
            )
        else:
            # Old schema without new columns
            return DataRecord(
                id=row[0],
                data_type=row[1],
                data_id=row[2],
                base_path=row[3],
                status=row[4],
                stage_path=row[5],
                published_path=row[6],
                created_at=row[7],
                staged_at=row[8],
                published_at=row[9],
                publish_mode=row[10],
                metadata_json=row[11],
                qa_status=row[12],
                validation_status=row[13],
                finalization_status=row[14],
                auto_publish_enabled=bool(row[15]),
                publish_attempts=0,
                publish_error=None,
            )
    except sqlite3.OperationalError:
        # Fall back to old schema if columns don't exist
        cur.execute(
            """
            SELECT id, data_type, data_id, base_path, status, stage_path, published_path,
                   created_at, staged_at, published_at, publish_mode, metadata_json,
                   qa_status, validation_status, finalization_status, auto_publish_enabled
            FROM data_registry
            WHERE data_id = ?
            """,
            (data_id,),
        )
        row = cur.fetchone()

        if not row:
            return None

        return DataRecord(
            id=row[0],
            data_type=row[1],
            data_id=row[2],
            base_path=row[3],
            status=row[4],
            stage_path=row[5],
            published_path=row[6],
            created_at=row[7],
            staged_at=row[8],
            published_at=row[9],
            publish_mode=row[10],
            metadata_json=row[11],
            qa_status=row[12],
            validation_status=row[13],
            finalization_status=row[14],
            auto_publish_enabled=bool(row[15]),
            publish_attempts=0,
            publish_error=None,
        )


def list_data(
    conn: sqlite3.Connection,
    data_type: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> tuple[list[DataRecord], int]:
    """List data records with optional filters and pagination.

    Parameters
    ----------
    conn: sqlite3.Connection :

    data_type: Optional[str] :
         (Default value = None)
    status: Optional[str] :
         (Default value = None)
    limit: Optional[int] :
         (Default value = None)
    offset: Optional[int] :
         (Default value = None)

    Returns
    -------
        Tuple of (records, total_count)

    """
    cur = conn.cursor()

    # Try to select with new columns, fall back to old columns if they don't exist
    try:
        # Build base query for counting
        count_query = "SELECT COUNT(*) FROM data_registry WHERE 1=1"
        count_params = []

        if data_type:
            count_query += " AND data_type = ?"
            count_params.append(data_type)

        if status:
            count_query += " AND status = ?"
            count_params.append(status)

        # Get total count
        cur.execute(count_query, count_params)
        total_count = cur.fetchone()[0]

        # Build query for data
        query = """
            SELECT id, data_type, data_id, base_path, status, stage_path, published_path,
                   created_at, staged_at, published_at, publish_mode, metadata_json,
                   qa_status, validation_status, finalization_status, auto_publish_enabled,
                   publish_attempts, publish_error, photometry_status, photometry_job_id
            FROM data_registry
            WHERE 1=1
        """
        params = []

        if data_type:
            query += " AND data_type = ?"
            params.append(data_type)

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC"

        # Add pagination
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None:
                query += " OFFSET ?"
                params.append(offset)

        cur.execute(query, params)
        rows = cur.fetchall()

        records = [
            DataRecord(
                id=row[0],
                data_type=row[1],
                data_id=row[2],
                base_path=row[3],
                status=row[4],
                stage_path=row[5],
                published_path=row[6],
                created_at=row[7],
                staged_at=row[8],
                published_at=row[9],
                publish_mode=row[10],
                metadata_json=row[11],
                qa_status=row[12],
                validation_status=row[13],
                finalization_status=row[14],
                auto_publish_enabled=bool(row[15]),
                publish_attempts=(row[16] if len(row) > 16 and row[16] is not None else 0),
                publish_error=row[17] if len(row) > 17 else None,
                photometry_status=row[18] if len(row) > 18 else None,
                photometry_job_id=row[19] if len(row) > 19 else None,
            )
            for row in rows
        ]
        return records, total_count
    except sqlite3.OperationalError:
        # Fall back to old schema if columns don't exist
        # Build count query
        count_query = "SELECT COUNT(*) FROM data_registry WHERE 1=1"
        count_params = []

        if data_type:
            count_query += " AND data_type = ?"
            count_params.append(data_type)

        if status:
            count_query += " AND status = ?"
            count_params.append(status)

        cur.execute(count_query, count_params)
        total_count = cur.fetchone()[0]

        # Build data query
        query = """
            SELECT id, data_type, data_id, base_path, status, stage_path, published_path,
                   created_at, staged_at, published_at, publish_mode, metadata_json,
                   qa_status, validation_status, finalization_status, auto_publish_enabled
            FROM data_registry
            WHERE 1=1
        """
        params = []

        if data_type:
            query += " AND data_type = ?"
            params.append(data_type)

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC"

        # Add pagination
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None:
                query += " OFFSET ?"
                params.append(offset)

        cur.execute(query, params)
        rows = cur.fetchall()

        records = [
            DataRecord(
                id=row[0],
                data_type=row[1],
                data_id=row[2],
                base_path=row[3],
                status=row[4],
                stage_path=row[5],
                published_path=row[6],
                created_at=row[7],
                staged_at=row[8],
                published_at=row[9],
                publish_mode=row[10],
                metadata_json=row[11],
                qa_status=row[12],
                validation_status=row[13],
                finalization_status=row[14],
                auto_publish_enabled=bool(row[15]),
                publish_attempts=0,
                publish_error=None,
            )
            for row in rows
        ]
        return records, total_count


def link_data(
    conn: sqlite3.Connection,
    parent_id: str,
    child_id: str,
    relationship_type: str,
) -> bool:
    """Link two data instances with a relationship.

    Parameters
    ----------
    conn: sqlite3.Connection :

    """
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO data_relationships
            (parent_data_id, child_data_id, relationship_type)
            VALUES (?, ?, ?)
            """,
            (parent_id, child_id, relationship_type),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to link data {parent_id} -> {child_id}: {e}")
        return False


def get_data_lineage(conn: sqlite3.Connection, data_id: str) -> dict[str, list[str]]:
    """Get lineage (parents and children) for a data instance.

    Parameters
    ----------
    conn: sqlite3.Connection :

    """
    cur = conn.cursor()

    # Get parents (what this data was derived from)
    cur.execute(
        """
        SELECT parent_data_id, relationship_type
        FROM data_relationships
        WHERE child_data_id = ?
        """,
        (data_id,),
    )
    parents = {}
    for parent_id, rel_type in cur.fetchall():
        if rel_type not in parents:
            parents[rel_type] = []
        parents[rel_type].append(parent_id)

    # Get children (what was produced from this data)
    cur.execute(
        """
        SELECT child_data_id, relationship_type
        FROM data_relationships
        WHERE parent_data_id = ?
        """,
        (data_id,),
    )
    children = {}
    for child_id, rel_type in cur.fetchall():
        if rel_type not in children:
            children[rel_type] = []
        children[rel_type].append(child_id)

    return {
        "parents": parents,
        "children": children,
    }


def enable_auto_publish(conn: sqlite3.Connection, data_id: str) -> bool:
    """Enable auto-publish for a data instance.

    Parameters
    ----------
    conn: sqlite3.Connection :

    """
    try:
        conn.execute(
            "UPDATE data_registry SET auto_publish_enabled = 1 WHERE data_id = ?",
            (data_id,),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to enable auto-publish for {data_id}: {e}")
        return False


def disable_auto_publish(conn: sqlite3.Connection, data_id: str) -> bool:
    """Disable auto-publish for a data instance.

    Parameters
    ----------
    conn: sqlite3.Connection :

    """
    try:
        conn.execute(
            "UPDATE data_registry SET auto_publish_enabled = 0 WHERE data_id = ?",
            (data_id,),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to disable auto-publish for {data_id}: {e}")
        return False


def check_auto_publish_criteria(
    conn: sqlite3.Connection,
    data_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check if auto-publish criteria are met for a data instance.

    Parameters
    ----------
    conn: sqlite3.Connection :

    """
    cur = conn.cursor()
    photometry_status: str | None
    try:
        cur.execute(
            """
            SELECT data_type, qa_status, validation_status, finalization_status,
                   auto_publish_enabled, photometry_status
            FROM data_registry
            WHERE data_id = ?
            """,
            (data_id,),
        )
        row = cur.fetchone()
        photometry_status = row[5] if row else None
    except sqlite3.OperationalError:
        # Schema mismatch: photometry_status column doesn't exist
        # Fall back to query without that column
        photometry_status = None
        cur.execute(
            """
            SELECT data_type, qa_status, validation_status, finalization_status, auto_publish_enabled
            FROM data_registry
            WHERE data_id = ?
            """,
            (data_id,),
        )
        row = cur.fetchone()
    else:
        # First query succeeded, no need for second query
        pass

    if not row:
        return {"enabled": False, "criteria_met": False, "reason": "not_found"}

    dtype, qa_status, validation_status, finalization_status, auto_enabled = row[:5]

    if not auto_enabled:
        return {"enabled": False, "criteria_met": False, "reason": "disabled"}

    criteria_met = True
    reasons = []

    # Check finalization
    if finalization_status != "finalized":
        criteria_met = False
        reasons.append("not_finalized")

    # Check validation
    if validation_status != "validated":
        criteria_met = False
        reasons.append("not_validated")

    # Check QA for science data
    if dtype in ("image", "mosaic", "calib_ms", "caltable"):
        if qa_status != "passed":
            criteria_met = False
            reasons.append("qa_not_passed")

    if dtype == "mosaic":
        if photometry_status != "completed":
            criteria_met = False
            reasons.append("photometry_incomplete")

    return {
        "enabled": True,
        "criteria_met": criteria_met,
        "reasons": reasons,
        "qa_status": qa_status,
        "validation_status": validation_status,
        "finalization_status": finalization_status,
        "photometry_status": photometry_status,
    }
