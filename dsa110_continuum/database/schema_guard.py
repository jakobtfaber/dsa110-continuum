# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# infrastructure/database/schema_guard.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 5).
"""
Schema Guard - Runtime validation of SQLite database schemas.

This module provides decorators and utilities to validate that database
tables exist with the required columns before executing functions that
depend on them. This catches schema mismatches early with clear error
messages instead of cryptic SQLite errors.

Usage:
    @requires_table("hdf5_files")
    def index_orphaned_files(db_path: str, storage_dir: str, ...):
        ...

    # Or validate explicitly:
    validate_schema(conn, "hdf5_files")
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# Type variable for decorated functions
F = TypeVar("F", bound=Callable[..., Any])


class SchemaError(Exception):
    """ """

    def __init__(
        self,
        message: str,
        table: str | None = None,
        missing_columns: set[str] | None = None,
        db_path: str | None = None,
    ):
        super().__init__(message)
        self.table = table
        self.missing_columns = missing_columns or set()
        self.db_path = db_path

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.db_path:
            parts.append(f"Database: {self.db_path}")
        if self.table:
            parts.append(f"Table: {self.table}")
        if self.missing_columns:
            parts.append(f"Missing columns: {sorted(self.missing_columns)}")
        return " | ".join(parts)


# =============================================================================
# Required Schema Registry
# =============================================================================
# Maps table name -> set of required column names
# Functions decorated with @requires_table will validate these exist
#
# NOTE: Only include columns that are REQUIRED for the function to work.
# Additional columns are allowed - we only check for presence of these.

REQUIRED_SCHEMA: dict[str, set[str]] = {
    # ---------------------------------------------------------------------------
    # Core Pipeline Tables
    # ---------------------------------------------------------------------------
    # HDF5 file tracking (storage_validator.py)
    "hdf5_files": {
        "path",
        "filename",
        "group_id",
        "subband_code",
        "subband_num",
        "timestamp_iso",
        "timestamp_mjd",
        "file_size_bytes",
        "modified_time",
        "indexed_at",
        "stored",
        "processed",
        "ra_deg",
        "dec_deg",
        "jd_start",
        "obs_date",
        "obs_time",
    },
    # Measurement Set index (unified.py: ms_index_upsert)
    "ms_index": {
        "path",
        "mid_mjd",
        "status",
        "stage",
        "group_id",
        "created_at",
    },
    # Image products (unified.py: images_insert)
    "images": {
        "id",
        "path",
        "ms_path",
        "type",
        "center_ra_deg",
        "center_dec_deg",
        "created_at",
    },
    # Image QA results
    "image_qa": {
        "ms_path",
        "overall_quality",
    },
    # Photometry results (unified.py: photometry_insert)
    "photometry": {
        "id",
        "image_path",
        "source_id",
        "ra_deg",
        "dec_deg",
        "flux_jy",
    },
    # Jobs (unified.py: create_job, update_job_status)
    "jobs": {
        "id",
        "type",
        "status",
        "ms_path",
        "created_at",
    },
    # ---------------------------------------------------------------------------
    # Calibration Tables
    # ---------------------------------------------------------------------------
    # Calibration table registry (unified.py: register_caltable_set)
    "caltables": {
        "id",
        "set_name",
        "path",
        "source_ms_path",
        "table_type",
        "order_index",
        "created_at",
        "status",
    },
    # Bandpass calibrators (unified.py: get_bandpass_calibrators)
    "bandpass_calibrators": {
        "name",
        "ra_deg",
        "dec_deg",
        "status",
    },
    # Calibrator transits
    "calibrator_transits": {
        "calibrator_name",
        "transit_mjd",
    },
    # ---------------------------------------------------------------------------
    # Queue/Processing Tables
    # ---------------------------------------------------------------------------
    # Processing queue
    "processing_queue": {
        "id",
        "group_id",
        "status",
        "created_at",
    },
    # Batch jobs
    "batch_jobs": {
        "id",
        "job_type",
        "status",
        "created_at",
    },
    "batch_job_items": {
        "id",
        "batch_job_id",
        "item_path",
        "status",
    },
    # ---------------------------------------------------------------------------
    # Catalog/Science Tables
    # ---------------------------------------------------------------------------
    # Transient candidates
    "variable_source_candidates": {
        "id",
        "source_id",
        "ra_deg",
        "dec_deg",
    },
    # Monitoring sources
    "monitoring_sources": {
        "source_id",
        "ra_deg",
        "dec_deg",
    },
    # Sources catalog
    "sources": {
        "id",
        "ra_deg",
        "dec_deg",
    },
    # Data registry
    "data_registry": {
        "id",
        "path",
        "data_type",
    },
    # Pipeline jobs queue
    "pipeline_jobs": {
        "id",
        "status",
        "job_type",
    },
    # Flagging history
    "flagging_history": {
        "id",
        "ms_path",
        "flag_command",
    },
    # Selfcal iterations
    "selfcal_iterations": {
        "id",
        "ms_path",
        "iteration",
    },
}


# Cache for validated schemas to avoid repeated checks
_validated_schemas: dict[str, set[str]] = {}


def get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Get the set of column names for a table.

    Parameters
    ----------
    conn: sqlite3.Connection :

    """
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def validate_schema(
    conn: sqlite3.Connection,
    table: str,
    db_path: str | None = None,
    use_cache: bool = True,
) -> None:
    """Validate that a table exists with required columns.

    Parameters
    ----------
    conn :
        SQLite connection
    table :
        Table name to validate
    db_path :
        Optional path for error messages
    use_cache :
        If True, skip validation if already validated for this db
    conn: sqlite3.Connection :

    Raises
    ------
    SchemaError
        If table doesn't exist or is missing required columns

    """
    # Check cache
    cache_key = f"{db_path or id(conn)}:{table}"
    if use_cache and cache_key in _validated_schemas:
        return

    # Get actual columns
    actual_columns = get_table_columns(conn, table)

    # Check table exists
    if not actual_columns:
        raise SchemaError(
            f"Table '{table}' does not exist in database",
            table=table,
            db_path=db_path,
        )

    # Check required columns
    required = REQUIRED_SCHEMA.get(table)
    if required:
        missing = required - actual_columns
        if missing:
            raise SchemaError(
                f"Table '{table}' is missing {len(missing)} required column(s)",
                table=table,
                missing_columns=missing,
                db_path=db_path,
            )

    # Cache successful validation
    if use_cache:
        _validated_schemas[cache_key] = actual_columns
        logger.debug(f"Schema validated for table '{table}' in {db_path or 'connection'}")


def clear_schema_cache() -> None:
    """Clear the schema validation cache. Useful for testing."""
    _validated_schemas.clear()


def _extract_db_path(args: tuple, kwargs: dict) -> str | None:
    """Extract db_path from function arguments.

    Parameters
    ----------
    """
    # Check kwargs first
    if "db_path" in kwargs:
        return kwargs["db_path"]

    # Check first positional arg (common pattern)
    if args and isinstance(args[0], (str, Path)):
        path = str(args[0])
        if path.endswith(".sqlite3") or path.endswith(".db"):
            return path

    return None


def _extract_connection(args: tuple, kwargs: dict) -> sqlite3.Connection | None:
    """Extract sqlite3.Connection from function arguments.

    Parameters
    ----------
    """
    # Check kwargs
    for key in ("conn", "connection", "db"):
        if key in kwargs and isinstance(kwargs[key], sqlite3.Connection):
            return kwargs[key]

    # Check positional args
    for arg in args:
        if isinstance(arg, sqlite3.Connection):
            return arg

    return None


def requires_table(*tables: str) -> Callable[[F], F]:
    """Decorator that validates required tables exist before function execution.

    The decorated function must have either:
    - A `db_path` argument (str/Path to SQLite database)
    - A `conn` argument (sqlite3.Connection)

    Parameters
    ----------
    *tables : str
        Table names that must exist with required columns.

    Examples
    --------
    @requires_table("hdf5_files")
    def index_orphaned_files(db_path: str, storage_dir: str, ...):
        ...

    @requires_table("ms_index", "images")
    def link_images_to_ms(conn: sqlite3.Connection, ...):
        ...
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Try to get connection from args
            conn = _extract_connection(args, kwargs)
            db_path = _extract_db_path(args, kwargs)

            # If we have a path but no connection, open temporarily for validation
            close_conn = False
            if conn is None and db_path:
                try:
                    conn = sqlite3.connect(db_path, timeout=10)
                    close_conn = True
                except sqlite3.Error as e:
                    raise SchemaError(
                        f"Cannot connect to database: {e}",
                        db_path=db_path,
                    )

            if conn is None:
                logger.warning(
                    f"Cannot validate schema for {func.__name__}: "
                    "no connection or db_path found in arguments"
                )
                return func(*args, **kwargs)

            try:
                # Validate all required tables
                for table in tables:
                    validate_schema(conn, table, db_path=db_path)
            finally:
                if close_conn:
                    conn.close()

            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def validate_insert_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: set[str],
    db_path: str | None = None,
) -> None:
    """Validate that all columns being inserted exist in the table.

    This is stricter than requires_table - it checks specific columns
    rather than just the required minimum.

    Parameters
    ----------
    conn :
        SQLite connection
    table :
        Table name
    columns :
        Column names that will be inserted
    db_path :
        Optional path for error messages
    conn: sqlite3.Connection :

    Raises
    ------
    SchemaError
        If any columns don't exist in the table

    """
    actual_columns = get_table_columns(conn, table)

    if not actual_columns:
        raise SchemaError(
            f"Table '{table}' does not exist",
            table=table,
            db_path=db_path,
        )

    missing = columns - actual_columns
    if missing:
        raise SchemaError(
            f"Cannot insert into '{table}': columns do not exist",
            table=table,
            missing_columns=missing,
            db_path=db_path,
        )
