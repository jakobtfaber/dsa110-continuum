"""
HDF5 file indexing and querying for DSA-110 Continuum Imaging Pipeline.

This module provides utilities for indexing, querying, and grouping HDF5
subband files with proper error handling and logging.

Grouping is performed by ``hdf5_group_by_metadata.group_subbands_by_metadata()``,
which uses exact ``time_array[0]`` matching from HDF5 headers.  The
``query_subband_groups()`` function is the main entry point that delegates
to the metadata grouper and adds RA bounds for calibrator transit matching.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py

from dsa110_continuum.utils.exceptions import (
    DatabaseError,
    InvalidPathError,
    UVH5ReadError,
    ValidationError,
)
from dsa110_continuum.utils.fast_meta import peek_uvh5_phase_and_midtime
from dsa110_continuum.utils.logging import log_context
from dsa110_continuum.database.unified import get_pipeline_db_path
# (legacy QueryBuilder dependency inlined in deduplicate_indexed_files)

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes for Subband Grouping
# =============================================================================


@dataclass
class SubbandGroup:
    """
    Represents a single acquisition group of subband files.

    Attributes
    ----------
    files : List[str]
        List of file paths in this group (sorted by time).
    representative_time : str
        ISO8601 timestamp representing this group (from first file).
    num_subbands : int
        Number of unique subbands present in this group.
    missing_subbands : List[int]
        List of subband indices that are missing (expected but not present).
    duplicate_count : int
        Number of duplicate subband files in this group.
    time_span_s : float
        Time span of the group in seconds (last - first timestamp).
    """

    files: list[str]
    representative_time: str
    num_subbands: int
    missing_subbands: list[int] = field(default_factory=list)
    duplicate_count: int = 0
    time_span_s: float = 0.0

    @property
    def is_complete(self) -> bool:
        """Return True if group has all 16 subbands with no duplicates."""
        return self.num_subbands == 16 and self.duplicate_count == 0

    def __len__(self) -> int:
        """Return number of files in this group."""
        return len(self.files)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "files": self.files,
            "representative_time": self.representative_time,
            "num_subbands": self.num_subbands,
            "missing_subbands": self.missing_subbands,
            "duplicate_count": self.duplicate_count,
            "time_span_s": self.time_span_s,
            "is_complete": self.is_complete,
            "file_count": len(self.files),
        }


@dataclass
class GroupingMetrics:
    """
    Aggregate metrics for grouping results.

    Attributes
    ----------
    sigma : float
        Estimated within-acquisition jitter scale (seconds).
    W_max : float
        Maximum allowed within-group time span (seconds).
    status : str
        Status of the grouping algorithm ("ok" or "dp_failed_greedy_fallback").
    total_files : int
        Total number of files processed.
    total_groups : int
        Total number of groups created.
    complete_groups : int
        Number of groups with all 16 subbands and no duplicates.
    fraction_complete : float
        Fraction of groups that are complete (0.0 to 1.0).
    total_missing : int
        Total count of missing subbands across all groups.
    total_duplicates : int
        Total count of duplicate files across all groups.
    """

    sigma: float
    W_max: float
    status: str
    total_files: int = 0
    total_groups: int = 0
    complete_groups: int = 0
    fraction_complete: float = 0.0
    total_missing: int = 0
    total_duplicates: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "sigma": self.sigma,
            "W_max": self.W_max,
            "status": self.status,
            "total_files": self.total_files,
            "total_groups": self.total_groups,
            "complete_groups": self.complete_groups,
            "fraction_complete": self.fraction_complete,
            "total_missing": self.total_missing,
            "total_duplicates": self.total_duplicates,
        }


@dataclass
class GroupingResult:
    """
    Container for grouping results with metrics.

    Supports iteration, length, and boolean checks for convenience:
    - `len(result)` returns number of groups
    - `bool(result)` returns True if any groups exist
    - `for group in result` iterates over SubbandGroup objects

    Attributes
    ----------
    groups : List[SubbandGroup]
        List of SubbandGroup objects.
    metrics : GroupingMetrics
        Aggregate metrics for the grouping.
    """

    groups: list[SubbandGroup]
    metrics: GroupingMetrics

    def __len__(self) -> int:
        """Return number of groups."""
        return len(self.groups)

    def __bool__(self) -> bool:
        """Return True if any groups exist."""
        return len(self.groups) > 0

    def __iter__(self) -> Iterator[SubbandGroup]:
        """Iterate over SubbandGroup objects."""
        return iter(self.groups)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "groups": [g.to_dict() for g in self.groups],
            "metrics": self.metrics.to_dict(),
        }


# =============================================================================
# Filename Parsing
# =============================================================================


# Pattern for parsing subband filenames (handles _spl suffix)
# Example: 2025-01-15T12:00:00_sb05.hdf5 or 2025-01-15T12:00:00_sb05_spl.hdf5
SUBBAND_PATTERN = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})_sb(?P<index>\d{2})(?:_spl)?\.hdf5$"
)


def parse_subband_filename(filename: str) -> tuple[str, int] | None:
    """Extract (timestamp_iso, subband_idx) from a filename, or None if not matched.

    Parameters
    ----------
    filename : str
        Filename of the HDF5 subband file.

    Returns
    -------
    tuple or None
        Tuple of (timestamp_iso, subband_index) or None if filename doesn't match pattern.
    """
    match = SUBBAND_PATTERN.search(filename)
    if not match:
        return None
    return match.group("timestamp"), int(match.group("index"))


def _parse_timestamp_components(timestamp_iso: str) -> tuple[str, str]:
    """Parse ISO timestamp into date and time components.

    Parameters
    ----------
    timestamp_iso : str
        ISO format timestamp (e.g., "2025-01-15T12:00:00")

    Returns
    -------
    tuple[str, str]
        Tuple of (obs_date, obs_time) where obs_date is YYYY-MM-DD and obs_time is HH:MM:SS
    """
    if "T" in timestamp_iso:
        date_part, time_part = timestamp_iso.split("T", 1)
        return date_part, time_part
    return timestamp_iso, ""


def _iso_to_mjd(timestamp_iso: str) -> float | None:
    """Convert ISO timestamp to Modified Julian Date.

    Parameters
    ----------
    timestamp_iso : str
        ISO format timestamp

    Returns
    -------
    float or None
        MJD value or None if conversion fails
    """
    try:
        from astropy.time import Time
        dt = datetime.fromisoformat(timestamp_iso)
        return Time(dt).mjd
    except Exception:
        return None


def _extract_file_metadata(
    file_path: Path,
    timestamp_iso: str,
    pointing_cache: dict[str, tuple[float | None, float | None, float | None]],
    *,
    pointing_cache_key: str | None = None,
    jd_start_override: float | None = None,
) -> dict[str, Any]:
    """Extract all metadata needed for database insertion from a file.

    Parameters
    ----------
    file_path : Path
        Path to the HDF5 file
    timestamp_iso : str
        ISO timestamp string for caching
    pointing_cache : dict
        Cache for pointing metadata (keyed by timestamp_iso)

    Returns
    -------
    dict
        Dictionary with keys: file_size, modified_time, timestamp_mjd,
        ra_deg, dec_deg, jd_start
    """
    null_result = {
        "file_size": None, "modified_time": None, "timestamp_mjd": None,
        "ra_deg": None, "dec_deg": None, "jd_start": None,
    }
    # Get filesystem metadata
    try:
        fstat = file_path.stat()
        file_size = fstat.st_size
        modified_time = fstat.st_mtime
    except OSError:
        return null_result

    # Convert timestamp to MJD
    timestamp_mjd = _iso_to_mjd(timestamp_iso)

    # Get RA/Dec from file metadata (with caching)
    ra_deg = None
    dec_deg = None
    try:
        cache_key = pointing_cache_key or timestamp_iso
        cached = pointing_cache.get(cache_key)
        if cached is None:
            ra_rad, dec_rad, meta_mjd = peek_uvh5_phase_and_midtime(file_path)
            cached = (
                math.degrees(float(ra_rad)),
                math.degrees(float(dec_rad)),
                float(meta_mjd) if meta_mjd is not None else None,
            )
            pointing_cache[cache_key] = cached

        ra_deg, dec_deg, meta_mjd = cached

        # Use metadata MJD if timestamp conversion failed
        if timestamp_mjd is None and meta_mjd is not None:
            timestamp_mjd = meta_mjd
    except Exception as e:
        logger.warning(f"Failed to get metadata for {file_path.name}: {e}")

    # Read jd_start (time_array[0]) — the exact grouping key.
    # This may already be provided via jd_start_override from the metadata
    # grouper; fall back to reading from file.
    jd_start = jd_start_override
    if jd_start is None:
        try:
            with h5py.File(str(file_path), "r") as fh:
                jd_start = float(fh["Header/time_array"][0])
        except Exception as e:
            logger.warning(f"Failed to read time_array[0] from {file_path.name}: {e}")

    return {
        "file_size": file_size,
        "modified_time": modified_time,
        "timestamp_mjd": timestamp_mjd,
        "ra_deg": ra_deg,
        "dec_deg": dec_deg,
        "jd_start": jd_start,
    }


def _handle_redis_staging(
    redis_client: Any,
    file_path: Path,
    timestamp_iso: str,
) -> tuple[str, float | None, list[Path]] | None:
    """Handle Redis staging for atomic group insertion.

    Uses ``time_array[0]`` (JD) as the exact-match grouping key.  All 16
    subbands in an observation share a bit-identical ``time_array``, so the
    JD float serves as a perfect, unambiguous key — no tolerance window needed.

    Buffers files in Redis until a complete group (16 subbands) is formed,
    then returns the complete group for insertion.  Returns ``None`` if the
    file was staged but the group is not yet complete.

    Parameters
    ----------
    redis_client : redis.Redis
        Redis client instance.
    file_path : Path
        Path to the file being indexed.
    timestamp_iso : str
        ISO timestamp string from the filename (used as fallback group_id).

    Returns
    -------
    tuple[str, float | None, list[Path]] or None
        ``(group_id, jd_start, files_to_insert)`` when the group is complete,
        or ``None`` if still staging.
    """
    from dsa110_continuum.database.hdf5_group_by_metadata import (
        _read_time_bounds,
    )

    try:
        # Read the exact grouping key from the HDF5 header.
        bounds = _read_time_bounds(file_path)
        if bounds is not None:
            jd_start = bounds[0]
            # Use JD as the Redis key — exact float64 match, no tolerance.
            redis_key = f"staging:group:jd:{jd_start}"
        else:
            # Fallback: filename-based key (no jd_start available).
            jd_start = None
            redis_key = f"staging:group:ts:{timestamp_iso}"

        # Add file to staging buffer.
        redis_client.sadd(redis_key, str(file_path))
        redis_client.expire(redis_key, 3600)  # 1 hour TTL

        # Check if group is complete (16 subbands).
        if redis_client.scard(redis_key) < 16:
            return None  # Still waiting for more files.

        # Group is complete — retrieve all files and clean up.
        complete_group_files = [
            Path(f.decode("utf-8")) for f in redis_client.smembers(redis_key)
        ]
        redis_client.delete(redis_key)

        # Use timestamp from first file sorted by subband (matches metadata grouper).
        parsed_files: list[tuple[str, int, Path]] = []
        for fp in complete_group_files:
            p = parse_subband_filename(fp.name)
            if p:
                parsed_files.append((p[0], p[1], fp))
        parsed_files.sort(key=lambda x: x[1])  # sort by subband index
        group_id = parsed_files[0][0] if parsed_files else timestamp_iso

        return (group_id, jd_start, complete_group_files)

    except Exception as e:
        logger.error(f"Redis staging failed for {file_path.name}: {e}")
        redis_key = f"staging:group:ts:{timestamp_iso}"
        redis_client.sadd(redis_key, str(file_path))
        redis_client.expire(redis_key, 3600)
        return None


def _insert_file_record(
    conn: sqlite3.Connection,
    file_path: Path,
    timestamp_iso: str,
    group_id: str,
    subband_idx: int,
    metadata: dict[str, Any],
    indexed_at: float,
) -> None:
    """Insert a single file record into the database with deduplication.

    Uses 'INSERT OR IGNORE' to implement Tier 1 deduplication at the database level.
    If a record with the same (group_id, subband_num) already exists, the insert
    is skipped, protecting the pipeline from upstream "jitter" duplicates.

    Parameters
    ----------
    conn : sqlite3.Connection
        Active database connection.
    file_path : Path
        Full path to the subband file.
    timestamp_iso : str
        Original ISO timestamp from filename.
    group_id : str
        Canonical group identifier (timestamp of first arrival).
    subband_idx : int
        Zero-indexed subband number (0-15).
    metadata : dict
        Extracted metadata (size, MJD, RA/Dec, etc.).
    indexed_at : float
        Unix timestamp when this indexing session started.
    """
    obs_date, obs_time = _parse_timestamp_components(timestamp_iso)

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO hdf5_files (
            path, filename, group_id, subband_code, subband_num,
            timestamp_iso, timestamp_mjd, file_size_bytes, modified_time,
            indexed_at, stored, processed, ra_deg, dec_deg, jd_start,
            obs_date, obs_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?)
        """,
        (
            str(file_path),
            file_path.name,
            group_id,
            f"sb{subband_idx:02d}",
            subband_idx,
            timestamp_iso,
            metadata["timestamp_mjd"],
            metadata["file_size"],
            metadata["modified_time"],
            indexed_at,
            metadata["ra_deg"],
            metadata["dec_deg"],
            metadata.get("jd_start"),
            obs_date,
            obs_time,
        ),
    )

    if cursor.rowcount == 0:
        logger.debug(f"Skipping redundant subband file: {file_path.name} (group {group_id} already has sb{subband_idx:02d})")


def index_subband_files(
    conn: sqlite3.Connection | None,
    files: list[Path],
    chunk_size: int | None = None,
    deduplicate: bool = True,
    redis_client: Any | None = None,
) -> int:
    """Index a list of subband files into the database with deduplication.

    This is the authoritative indexing function used by both sensors and CLI.
    It implements several layers of reliability to handle high-volume data ingest:

    1. Tier 1 Deduplication (Disk-to-DB):
       If `deduplicate=True`, it first queries the DB to skip files that already
       have a record for their specific disk path.

    2. Database Constraint Deduplication (Integrity):
       Uses 'INSERT OR IGNORE' based on the (group_id, subband_num) UNIQUE index.
       This prevents redundant subbands (from correlator jitter) from creating
       groups with >16 files.

    3. Redis Staging Pattern (Atomic Ingest):
       If `redis_client` is provided, files are buffered in Redis until a
       complete group (16 subbands) is detected. Only then are they flushed
       to SQLite. This prevents "partial" groups from ever hitting the database.

    Parameters
    ----------
    conn : sqlite3.Connection or None
        Database connection. If None, one will be created and closed.
    files : List[Path]
        List of file paths to index.
    chunk_size : int, optional
        If provided, only process the first N files.
    deduplicate : bool, default=True
        If True, skip files already in database (recommended for sensors).
    redis_client : redis.Redis, optional
        Redis client for staging buffer. If provided, enables atomic group insertion.

    Returns
    -------
    int
        Number of files successfully indexed.
    """
    if not files:
        return 0

    close_conn = False
    if conn is None:
        db_path = get_pipeline_db_path()
        conn = sqlite3.connect(db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        close_conn = True

    # Tier 1: Deduplicate files before indexing
    if deduplicate:
        files = deduplicate_indexed_files(files, conn)
        if not files:
            logger.debug("All files already indexed, skipping")
            if close_conn:
                conn.close()
            return 0

    # Tier 3: Get lifecycle manager for event tracking (not vendored from
    # dsa110-contimg; the ImportError fallback is the live path)
    try:
        from dsa110_continuum.database.hdf5_lifecycle import (
            HDF5LifecycleState,
            get_lifecycle_manager,
        )

        lifecycle_mgr = get_lifecycle_manager()
    except ImportError:
        lifecycle_mgr = None

    indexed = 0
    indexed_at = time.time()
    pointing_cache: dict[str, tuple[float | None, float | None, float | None]] = {}
    files_to_process = files[:chunk_size] if chunk_size else files
    # Sort files by filename to ensure subbands are indexed in order (sb00, sb01, ..., sb15)
    files_to_process = sorted(files_to_process, key=lambda p: p.name)

    # --- Build file→group lookup via metadata grouper ---
    # group_subbands_by_metadata reads time_array[0] from each file, giving us:
    #   • Exact group_id (representative_time from the canonical first file)
    #   • jd_start (the bit-identical JD that all 16 subbands share)
    from dsa110_continuum.database.hdf5_group_by_metadata import (
        group_subbands_by_metadata as _group_meta,
    )
    grouping_result = _group_meta(files_to_process)

    # Map: file_path_str → (group_id, jd_start)
    file_group_map: dict[str, tuple[str, float]] = {}
    for grp in grouping_result.groups:
        gid = grp.representative_time
        jd = grp.jd_start
        for fpath_str in grp.files:
            file_group_map[fpath_str] = (gid, jd)

    _deferred_calibrator_tracking: list[dict] = []

    for file_path in files_to_process:
        # Parse filename
        parsed = parse_subband_filename(file_path.name)
        if parsed is None:
            continue

        timestamp_iso, subband_idx = parsed

        # Handle Redis staging if enabled
        if redis_client:
            staged = _handle_redis_staging(redis_client, file_path, timestamp_iso)
            if staged is None:
                continue  # File staged, waiting for group completion
            group_id, jd_start_val, files_to_insert = staged
        else:
            lookup = file_group_map.get(str(file_path))
            if lookup:
                group_id, jd_start_val = lookup
            else:
                group_id = timestamp_iso
                jd_start_val = None
            files_to_insert = [file_path]

        # Process each file (may be multiple if Redis group completed)
        for f_path in files_to_insert:
            # Re-parse filename for safety
            parsed = parse_subband_filename(f_path.name)
            if parsed is None:
                continue
            timestamp_iso, subband_idx = parsed

            # Extract all metadata upfront
            metadata = _extract_file_metadata(
                f_path, timestamp_iso, pointing_cache,
                pointing_cache_key=group_id,
                jd_start_override=jd_start_val,
            )

            # Skip if file stats failed
            if metadata["file_size"] is None:
                continue

            # Insert into database
            _insert_file_record(
                conn, f_path, timestamp_iso, group_id, subband_idx, metadata, indexed_at
            )
            indexed += 1

            # Collect calibrator tracking data (deferred until after commit to avoid lock contention)
            _deferred_calibrator_tracking.append({
                "group_id": group_id,
                "timestamp_mjd": metadata.get("timestamp_mjd"),
                "timestamp_iso": timestamp_iso,
                "dec_deg": metadata.get("dec_deg"),
                "filename": f_path.name,
            })

            # Publish lifecycle event if manager available
            if lifecycle_mgr is not None:
                lifecycle_mgr.publish_event(
                    file_path=f_path,
                    state=HDF5LifecycleState.INDEXED,
                    group_id=group_id,
                    metadata={
                        "file_size_mb": metadata["file_size"] / 1024**2,
                        "subband_num": subband_idx,
                        "subband_code": f"sb{subband_idx:02d}",
                    },
                    previous_state=HDF5LifecycleState.DISCOVERED,
                )

    conn.commit()

    # Deferred calibrator tracking — runs after commit so the write lock is
    # released (not vendored from dsa110-contimg; ImportError path is live)
    if _deferred_calibrator_tracking:
        try:
            from dsa110_continuum.database.calibrator_tracking import (
                on_file_indexed as track_calibrator,
            )
            for item in _deferred_calibrator_tracking:
                try:
                    track_calibrator(
                        group_id=item["group_id"],
                        timestamp_mjd=item["timestamp_mjd"],
                        timestamp_iso=item["timestamp_iso"],
                        dec_deg=item["dec_deg"],
                    )
                except Exception as e:
                    logger.debug(
                        f"Calibrator tracking failed for {item['filename']}: {e}"
                    )
        except ImportError:
            pass  # Calibrator tracking not available

    if close_conn:
        conn.close()

    return indexed


def backfill_hdf5_radec(
    conn: sqlite3.Connection,
    *,
    start_time: str,
    end_time: str,
    max_groups: int | None = None,
) -> int:
    rows = conn.execute(
        """
        SELECT group_id, MIN(path) AS sample_path
        FROM hdf5_files
        WHERE timestamp_iso >= ? AND timestamp_iso <= ?
            AND group_id IS NOT NULL
            AND (ra_deg IS NULL OR dec_deg IS NULL)
        GROUP BY group_id
        ORDER BY group_id
        """,
        (start_time, end_time),
    ).fetchall()

    if max_groups is not None:
        rows = rows[:max_groups]

    updated = 0
    for group_id, sample_path in rows:
        ra_deg = None
        dec_deg = None
        try:
            ra_rad, dec_rad, _mjd = peek_uvh5_phase_and_midtime(Path(sample_path))
            ra_deg = math.degrees(float(ra_rad))
            dec_deg = math.degrees(float(dec_rad))
        except Exception:
            continue

        conn.execute(
            """
            UPDATE hdf5_files
            SET ra_deg = ?, dec_deg = ?
            WHERE group_id = ? AND (ra_deg IS NULL OR dec_deg IS NULL)
            """,
            (ra_deg, dec_deg, group_id),
        )
        updated += 1

    conn.commit()
    return updated


def backfill_jd_start(
    conn: sqlite3.Connection,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    max_files: int | None = None,
    batch_size: int = 500,
) -> int:
    """Backfill ``jd_start`` (``time_array[0]``) for existing DB records.

    Reads ``time_array[0]`` from the HDF5 file on disk for every row where
    ``jd_start IS NULL`` and the file still exists (``stored = 1``).

    Parameters
    ----------
    conn : sqlite3.Connection
        Database connection.
    start_time, end_time : str, optional
        ISO timestamp range to limit the backfill.
    max_files : int, optional
        Maximum number of files to backfill.
    batch_size : int
        Commit every *batch_size* updates.

    Returns
    -------
    int
        Number of rows updated.
    """
    from dsa110_continuum.database.hdf5_group_by_metadata import (
        _read_time_bounds,
    )

    wheres = ["jd_start IS NULL", "stored = 1"]
    params: list[str] = []
    if start_time:
        wheres.append("timestamp_iso >= ?")
        params.append(start_time)
    if end_time:
        wheres.append("timestamp_iso <= ?")
        params.append(end_time)

    sql = f"SELECT path FROM hdf5_files WHERE {' AND '.join(wheres)} ORDER BY timestamp_iso"
    if max_files:
        sql += f" LIMIT {max_files}"

    rows = conn.execute(sql, params).fetchall()

    updated = 0
    for i, (file_path_str,) in enumerate(rows):
        bounds = _read_time_bounds(file_path_str)
        if bounds is None:
            continue
        jd_start = bounds[0]

        conn.execute(
            "UPDATE hdf5_files SET jd_start = ? WHERE path = ?",
            (jd_start, file_path_str),
        )
        updated += 1

        if updated % batch_size == 0:
            conn.commit()
            logger.info("backfill_jd_start: %d / %d updated so far", updated, len(rows))

    conn.commit()
    logger.info("backfill_jd_start: %d rows updated out of %d candidates", updated, len(rows))
    return updated


def index_hdf5_files(directory: str) -> list[tuple[str, list[str]]]:
    """Index HDF5 files in the specified directory (Metadata discovery version).

    DEPRECATED: Use index_subband_files for pipeline database indexing.
    This function remains for legacy metadata exploration where we list datasets
    within the files without adding them to the pipeline DB.

    Parameters
    ----------
    directory :
        The path to the directory containing HDF5 files.

    Returns
    -------
        A list of tuples where each tuple contains the filename and
        a list of datasets within that file.
    """
    if not os.path.isdir(directory):
        raise InvalidPathError(
            path=directory,
            path_type="directory",
            reason="Directory does not exist",
        )

    indexed_files = []
    errors = []

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".hdf5"):
            continue

        file_path = os.path.join(directory, filename)

        try:
            with h5py.File(file_path, "r") as hdf_file:
                datasets = list(hdf_file.keys())
                indexed_files.append((filename, datasets))
                logger.debug(
                    f"Indexed HDF5 file: {filename}",
                    extra={
                        "file_path": file_path,
                        "dataset_count": len(datasets),
                    },
                )
        except OSError as e:
            error_msg = f"Failed to read HDF5 file: {filename}: {e}"
            logger.warning(error_msg, extra={"file_path": file_path})
            errors.append({"file": filename, "error": str(e)})
        except Exception as e:
            error_msg = f"Unexpected error reading HDF5 file: {filename}: {e}"
            logger.error(error_msg, exc_info=True, extra={"file_path": file_path})
            errors.append({"file": filename, "error": str(e)})

    if errors:
        logger.warning(
            f"Indexed {len(indexed_files)} files with {len(errors)} errors",
            extra={
                "directory": directory,
                "indexed_count": len(indexed_files),
                "error_count": len(errors),
                "errors": errors,
            },
        )
    else:
        logger.info(
            f"Indexed {len(indexed_files)} HDF5 files",
            extra={
                "directory": directory,
                "indexed_count": len(indexed_files),
            },
        )

    return indexed_files


def query_hdf5_file(file_path: str, dataset_name: str) -> Any:
    """Query a specific dataset in an HDF5 file."""
    if not os.path.isfile(file_path):
        raise InvalidPathError(
            path=file_path,
            path_type="file",
            reason="File does not exist",
        )

    try:
        with h5py.File(file_path, "r") as hdf_file:
            if dataset_name not in hdf_file:
                available = list(hdf_file.keys())
                raise ValidationError(
                    f"Dataset '{dataset_name}' not found in '{file_path}'",
                    field="dataset_name",
                    value=dataset_name,
                    constraint=f"must be one of: {available}",
                    available_datasets=available,
                    file_path=file_path,
                )
            return hdf_file[dataset_name][:]

    except ValidationError:
        raise
    except OSError as e:
        raise UVH5ReadError(
            file_path=file_path,
            reason=str(e),
            original_exception=e,
        ) from e
    except Exception as e:
        raise UVH5ReadError(
            file_path=file_path,
            reason=f"Unexpected error: {e}",
            original_exception=e,
        ) from e


def get_hdf5_metadata(file_path: str) -> dict[str, Any]:
    """Retrieve metadata from an HDF5 file."""
    if not os.path.isfile(file_path):
        raise InvalidPathError(
            path=file_path,
            path_type="file",
            reason="File does not exist",
        )

    try:
        with h5py.File(file_path, "r") as hdf_file:
            metadata = {
                "filename": os.path.basename(file_path),
                "datasets": list(hdf_file.keys()),
                "attributes": {key: hdf_file.attrs[key] for key in hdf_file.attrs},
            }

            logger.debug(
                f"Retrieved metadata for {metadata['filename']}",
                extra={
                    "file_path": file_path,
                    "dataset_count": len(metadata["datasets"]),
                    "attribute_count": len(metadata["attributes"]),
                },
            )

            return metadata

    except OSError as e:
        raise UVH5ReadError(
            file_path=file_path,
            reason=str(e),
            original_exception=e,
        ) from e
    except Exception as e:
        raise UVH5ReadError(
            file_path=file_path,
            reason=f"Unexpected error: {e}",
            original_exception=e,
        ) from e


# =============================================================================
# Database Query Functions
# =============================================================================


def query_subband_groups(
    db_path: str,
    start_time: str,
    end_time: str,
    m_min: int = 14,
    m_max: int = 18,
    *,
    read_dec: bool = False,
) -> GroupingResult:
    """
    Query subband file groups from the HDF5 index database.

    Uses metadata-based grouping: reads ``time_array[0]`` from each HDF5 file
    for exact-match grouping, then computes RA bounds via fast GMST.  Returns
    ``SubbandGroupWithRA`` objects with ``ra_min_deg``, ``ra_max_deg``, and
    ``ra_center_deg`` for calibrator transit matching.

    Parameters
    ----------
    db_path : str
        Path to the HDF5 index SQLite database.
    start_time : str
        Start time in ISO8601 format (e.g., "2025-01-15T00:00:00").
    end_time : str
        End time in ISO8601 format (e.g., "2025-01-15T23:59:59").
    m_min : int
        Deprecated — kept for API compatibility, ignored by metadata grouper.
    m_max : int
        Deprecated — kept for API compatibility, ignored by metadata grouper.
    read_dec : bool
        If True, read declination from the first file in each group
        (adds ~1 ms/group).  Default: False.

    Returns
    -------
    GroupingResult
        Contains list of SubbandGroupWithRA objects and GroupingMetrics.
        Iterate directly: ``for group in result: ...``
        Access files: ``group.files``
        Check completeness: ``group.is_complete``
        Access RA bounds: ``group.ra_min_deg``, ``group.ra_max_deg``
    """
    from dsa110_continuum.database.hdf5_group_by_metadata import (
        group_subbands_by_metadata,
        group_subbands_from_db_rows,
    )

    if not os.path.isfile(db_path):
        raise InvalidPathError(
            path=db_path,
            path_type="file",
            reason="HDF5 index database does not exist",
        )

    with log_context(
        pipeline_stage="subband_grouping",
        db_path=db_path,
        start_time=start_time,
        end_time=end_time,
    ):
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            cursor = conn.cursor()

            # Fetch rows including jd_start (may be NULL for pre-backfill data).
            cursor.execute(
                """
                SELECT path, timestamp_iso, subband_num, jd_start
                FROM hdf5_files
                WHERE timestamp_iso BETWEEN ? AND ?
                ORDER BY timestamp_iso, path
            """,
                (start_time, end_time),
            )

            rows = cursor.fetchall()
            conn.close()

            if not rows:
                logger.info(
                    "No files found in time range",
                    extra={"start_time": start_time, "end_time": end_time},
                )
                return GroupingResult(
                    groups=[],
                    metrics=GroupingMetrics(
                        sigma=0.0,
                        W_max=0.0,
                        status="no_files",
                        total_files=0,
                        total_groups=0,
                    ),
                )

            # Fast path: if all rows have jd_start, group from DB data
            # (reads only 1 file per group for jd_end instead of all files).
            rows_with_jd = [(p, ts, sb, jd) for p, ts, sb, jd in rows if jd is not None]
            rows_without_jd = [(p, ts, sb, jd) for p, ts, sb, jd in rows if jd is None]

            if not rows_without_jd:
                # All rows have jd_start — use DB-accelerated grouping.
                result = group_subbands_from_db_rows(
                    rows_with_jd,
                    read_dec=read_dec,
                )
            else:
                # Fallback: some rows missing jd_start, read all from files.
                logger.info(
                    "Falling back to file-based grouping (%d/%d rows missing jd_start)",
                    len(rows_without_jd), len(rows),
                )
                file_paths = [path for path, _, _, _ in rows]
                result = group_subbands_by_metadata(
                    file_paths,
                    read_dec=read_dec,
                )

            logger.info(
                f"Found {len(result)} subband groups",
                extra={
                    "group_count": len(result),
                    "file_count": result.metrics.total_files,
                    "complete_groups": result.metrics.complete_groups,
                    "fraction_complete": result.metrics.fraction_complete,
                    "start_time": start_time,
                    "end_time": end_time,
                },
            )

            return result

        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to query HDF5 index database: {e}",
                db_name="hdf5",
                db_path=db_path,
                operation="query",
                table_name="hdf5_files",
                original_exception=e,
            ) from e
        except Exception as e:
            raise DatabaseError(
                f"Unexpected error querying HDF5 index: {e}",
                db_name="hdf5",
                db_path=db_path,
                operation="query",
                original_exception=e,
            ) from e


# =============================================================================
# HDF5 Lifecycle Management & Cleanup (Tier 1, 2, 3)
# =============================================================================


@dataclass
class CleanupResult:
    """Result of cleanup operations."""

    stale_groups_removed: list[str] = field(default_factory=list)
    abandoned_groups_marked: list[str] = field(default_factory=list)
    duplicates_removed: list[str] = field(default_factory=list)
    orphaned_files_removed: int = 0
    total_groups_affected: int = 0
    cleanup_timestamp: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/reporting."""
        return {
            "stale_groups_removed": self.stale_groups_removed,
            "abandoned_groups_marked": self.abandoned_groups_marked,
            "duplicates_removed": self.duplicates_removed,
            "orphaned_files_removed": self.orphaned_files_removed,
            "total_groups_affected": self.total_groups_affected,
            "cleanup_timestamp": self.cleanup_timestamp,
        }


def cleanup_stale_groups(
    cutoff_hours: float = 2.0,
    conn: sqlite3.Connection | None = None,
    dry_run: bool = False,
) -> CleanupResult:
    """
    Remove incomplete subband groups older than cutoff time.
    
    DEPRECATED: This function is being phased out in favor of the Redis Staging pattern
    implemented in index_subband_files. The staging pattern ensures incomplete groups
    never reach the database, making this cleanup redundant.
    
    This function remains as a fallback for legacy ingestion or manual cleanup.
    
    Parameters
    ----------
    cutoff_hours : float, default=2.0
        Delete groups indexed more than this many hours ago
    conn : sqlite3.Connection, optional
        Database connection. If None, creates a new connection.
    dry_run : bool, default=False
        If True, only report what would be deleted without actual deletion
    
    Returns
    -------
    CleanupResult
        Details of cleanup operation
    """
    # ... (Implementation kept as legacy fallback, but marked deprecated)
    # The actual implementation is already updated to be robust, so we keep it
    # as a safety net but document that Redis handles the primary "cleanup" via prevention.

    # Original implementation follows...
    close_conn = False
    if conn is None:
        db_path = get_pipeline_db_path()
        conn = sqlite3.connect(db_path, timeout=60)
        close_conn = True

    result = CleanupResult()
    cutoff_time = time.time() - (cutoff_hours * 3600)

    try:
        # Step 1: Find candidate stale files (older than cutoff, not processed)
        # Instead of grouping by ID immediately, we get ALL potentially stale files
        cursor = conn.execute(
            """
            SELECT path, filename, group_id, indexed_at
            FROM hdf5_files
            WHERE stored=1 AND processed=0 AND indexed_at < ?
            ORDER BY timestamp_iso
            """,
            (cutoff_time,),
        )

        candidates = cursor.fetchall()

        if not candidates:
            logger.info("No stale candidates found.")
            return result

        # Step 2: Run metadata-based grouping on these candidates to see if
        # they form valid groups.  This uses time_array[0] exact-match grouping
        # which is the authoritative production method.
        from dsa110_continuum.database.hdf5_group_by_metadata import (
            group_subbands_by_metadata,
        )

        file_paths = [row[0] for row in candidates]  # full paths for HDF5 reads

        grouping_result = group_subbands_by_metadata(file_paths)

        # Identify which files are part of COMPLETE groups (to be SAVED)
        saved_files = set()
        for group in grouping_result.groups:
            if group.is_complete:
                for f in group.files:
                    saved_files.add(f)

        # Identify which files are truly incomplete (to be DELETED)
        # These are files that failed to form a complete group even with metadata grouping
        files_to_delete = []
        groups_to_delete = set()

        for path, filename, group_id, indexed_at in candidates:
            if path not in saved_files:
                files_to_delete.append((path, group_id))
                groups_to_delete.add(group_id)

        # Step 3: Perform Deletion
        for group_id in groups_to_delete:
            result.stale_groups_removed.append(group_id)

            # Count how many files for this group we are deleting
            count = sum(1 for p, g in files_to_delete if g == group_id)

            logger.warning(
                f"{'[DRY RUN] Would delete' if dry_run else 'Deleting'} stale group fragment: "
                f"{group_id} ({count} files)"
            )

        if not dry_run:
            for path, group_id in files_to_delete:
                conn.execute("DELETE FROM hdf5_files WHERE path=?", (path,))

        result.total_groups_affected = len(groups_to_delete)

        if not dry_run:
            conn.commit()

        logger.info(
            f"Cleanup: {'Would remove' if dry_run else 'Removed'} "
            f"{len(result.stale_groups_removed)} stale group fragments "
            f"(Saved {len(saved_files)} files belonging to valid groups)"
        )

    finally:
        if close_conn:
            conn.close()

    return result


def mark_groups_as_abandoned(
    completion_timeout_minutes: int = 30,
    conn: sqlite3.Connection | None = None,
) -> CleanupResult:
    """
    Mark incomplete groups as 'abandoned' after completion timeout.

    This function implements Tier 2 timeout logic by marking groups that:
    - Have been waiting for completion for longer than timeout
    - Are still incomplete (<16 subbands)
    - Have not been processed yet

    Abandoned groups are NOT deleted but marked for later cleanup.

    Parameters
    ----------
    completion_timeout_minutes : int, default=30
        Mark groups as abandoned if incomplete after this many minutes
    conn : sqlite3.Connection, optional
        Database connection. If None, creates a new connection.

    Returns
    -------
    CleanupResult
        Details of groups marked as abandoned
    """
    close_conn = False
    if conn is None:
        db_path = get_pipeline_db_path()
        conn = sqlite3.connect(db_path, timeout=60)
        close_conn = True

    result = CleanupResult()
    cutoff_time = time.time() - (completion_timeout_minutes * 60)

    try:
        # Find groups that have timed out waiting for completion
        cursor = conn.execute(
            """
            SELECT group_id, COUNT(*) as cnt, MIN(indexed_at) as first_indexed
            FROM hdf5_files
            WHERE stored=1 AND processed=0 AND indexed_at < ?
            GROUP BY group_id
            HAVING cnt < 16 AND cnt >= 13
            ORDER BY first_indexed
            """,
            (cutoff_time,),
        )

        incomplete_groups = cursor.fetchall()

        for group_id, cnt, first_indexed in incomplete_groups:
            age_minutes = (time.time() - first_indexed) / 60
            result.abandoned_groups_marked.append(group_id)

            logger.warning(
                f"Marking group as abandoned: {group_id} "
                f"({cnt}/16 subbands, waited {age_minutes:.0f}min)"
            )

            # Note: Currently we just mark in logs. In the future, we could
            # add an 'abandoned' column to the database to track this state.
            # For now, these groups will be caught by cleanup_stale_groups.

        result.total_groups_affected = len(incomplete_groups)

        logger.info(
            f"Found {len(result.abandoned_groups_marked)} group(s) abandoned "
            f"after {completion_timeout_minutes}min timeout"
        )

    finally:
        if close_conn:
            conn.close()

    return result


def remove_duplicate_files(
    group_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    dry_run: bool = False,
) -> CleanupResult:
    """
    Remove duplicate subband files (same group_id + subband_num).

    When the correlator writes duplicate files, this function removes
    duplicates keeping only the first-indexed copy.

    Parameters
    ----------
    group_id : str, optional
        If provided, only remove duplicates within this group.
        If None, scan all groups.
    conn : sqlite3.Connection, optional
        Database connection. If None, creates a new connection.
    dry_run : bool, default=False
        If True, only report what would be deleted

    Returns
    -------
    CleanupResult
        Details of duplicates removed
    """
    close_conn = False
    if conn is None:
        db_path = get_pipeline_db_path()
        conn = sqlite3.connect(db_path, timeout=60)
        close_conn = True

    result = CleanupResult()

    try:
        # Find duplicate files (same group_id + subband_num)
        query = """
            SELECT group_id, subband_num, GROUP_CONCAT(path) as paths,
                   COUNT(*) as dup_count
            FROM hdf5_files
            WHERE stored=1
        """
        params = []

        if group_id is not None:
            query += " AND group_id=?"
            params.append(group_id)

        query += " GROUP BY group_id, subband_num HAVING dup_count > 1"

        cursor = conn.execute(query, params)
        duplicates = cursor.fetchall()

        for group, sb_num, paths_str, dup_count in duplicates:
            paths = paths_str.split(",")

            # Keep first file, delete the rest
            keep_path = paths[0]
            delete_paths = paths[1:]

            logger.warning(
                f"{'[DRY RUN] Would remove' if dry_run else 'Removing'} "
                f"{len(delete_paths)} duplicate(s) for {group}_sb{sb_num:02d} "
                f"(keeping {keep_path})"
            )

            if not dry_run:
                for del_path in delete_paths:
                    conn.execute(
                        "DELETE FROM hdf5_files WHERE path=?",
                        (del_path,),
                    )
                    result.duplicates_removed.append(del_path)

        result.total_groups_affected = len(duplicates)

        if not dry_run:
            conn.commit()

        logger.info(
            f"Duplicate cleanup: {'Would remove' if dry_run else 'Removed'} "
            f"{len(result.duplicates_removed)} duplicate file(s)"
        )

    finally:
        if close_conn:
            conn.close()

    return result


def deduplicate_indexed_files(
    files: list[Path],
    conn: sqlite3.Connection | None = None,
) -> list[Path]:
    """
    Filter out files that are already indexed in the database.

    This implements Tier 1 deduplication by checking which files
    already exist in the database before indexing.

    Parameters
    ----------
    files : list[Path]
        List of file paths to check
    conn : sqlite3.Connection, optional
        Database connection. If None, creates a new connection.

    Returns
    -------
    list[Path]
        Files that are NOT yet indexed (safe to index)

    Examples
    --------
    >>> new_files = deduplicate_indexed_files([Path("/data/file1.hdf5")])
    >>> index_subband_files(conn, new_files)
    """
    if not files:
        return []

    close_conn = False
    if conn is None:
        db_path = get_pipeline_db_path()
        conn = sqlite3.connect(db_path, timeout=60)
        close_conn = True

    try:
        # Build query to check which paths already exist (inlined from the
        # retired legacy QueryBuilder: '?' placeholders + simple SELECT)
        file_paths = [str(f) for f in files]
        placeholders = ", ".join("?" * len(file_paths))

        cursor = conn.execute(
            f"SELECT path FROM hdf5_files WHERE path IN ({placeholders})",  # noqa: S608
            file_paths,
        )

        existing_paths = set(row[0] for row in cursor.fetchall())

        # Return only files not yet indexed
        new_files = [f for f in files if str(f) not in existing_paths]

        if len(existing_paths) > 0:
            logger.debug(
                f"Deduplication: {len(existing_paths)} files already indexed, "
                f"{len(new_files)} new files to index"
            )

        return new_files

    finally:
        if close_conn:
            conn.close()


def vacuum_deleted_records(
    retention_days: int = 7,
    conn: sqlite3.Connection | None = None,
) -> int:
    """
    Remove records for files deleted from disk (stored=0) after retention period.

    This implements Tier 2 database maintenance by cleaning up records
    for files that have been deleted from disk but still tracked in DB.

    Parameters
    ----------
    retention_days : int, default=7
        Keep deletion records for this many days before purging
    conn : sqlite3.Connection, optional
        Database connection. If None, creates a new connection.

    Returns
    -------
    int
        Number of records purged
    """
    close_conn = False
    if conn is None:
        db_path = get_pipeline_db_path()
        conn = sqlite3.connect(db_path, timeout=60)
        close_conn = True

    try:
        cutoff_time = time.time() - (retention_days * 86400)

        # Delete records marked as removed older than retention period
        cursor = conn.execute(
            """
            DELETE FROM hdf5_files
            WHERE stored=0 AND indexed_at < ?
            """,
            (cutoff_time,),
        )

        deleted_count = cursor.rowcount
        conn.commit()

        logger.info(
            f"Vacuum: Purged {deleted_count} deletion record(s) older than {retention_days} days"
        )

        return deleted_count

    finally:
        if close_conn:
            conn.close()


def select_hdf5_groups_by_position(
    db_path: str,
    source_ra_deg: float,
    source_dec_deg: float,
    beam_radius_deg: float = 1.75,
    n_groups: int = 12,
    require_complete: bool = True,
) -> list[str]:
    """Select HDF5 groups where a source falls within the primary beam.

    Uses the actual RA/Dec metadata stored per HDF5 file to find groups whose
    spatial coverage includes the requested source position.  This eliminates
    reliance on transit-time computation – the physically correct approach for
    a drift-scan telescope where:

    * The sky drifts through the fixed beam.
    * Each 5-minute group covers a unique RA strip (~1.25° wide).
    * ``ra_deg`` in the database records the beam centre during that group.

    Parameters
    ----------
    db_path : str
        Path to ``pipeline.sqlite3``.
    source_ra_deg : float
        Source RA in degrees [0, 360).
    source_dec_deg : float
        Source Dec in degrees.
    beam_radius_deg : float
        Half-power beam radius in degrees.  Default **1.75°** — half of the
        DSA-110 primary beam FWHM ≈ 3.5° at 1.4 GHz for 4.65 m dishes.
    n_groups : int
        Maximum number of groups to return (closest first).  Default 12.
    require_complete : bool
        If *True* (default), only return groups with all 16 subbands present.

    Returns
    -------
    list[str]
        Group representative timestamps (ISO format), ordered by angular
        proximity to source (closest first).

    Raises
    ------
    ValueError
        If no groups fall within the beam.

    Notes
    -----
    RA wrap-around (e.g. source at 1° matching group at 359°) is handled by
    computing the shortest arc: ``min(|Δ|, 360 − |Δ|)``.  The Dec filtering
    simply uses ``|Δ Dec|``.

    Examples
    --------
    >>> groups = select_hdf5_groups_by_position(
    ...     "/data/dsa110-contimg/state/db/pipeline.sqlite3",
    ...     source_ra_deg=343.49,   # 3C 454.3
    ...     source_dec_deg=16.15,
    ... )
    """
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        # ── Query all stored groups with their mean RA/Dec ────────────
        cursor = conn.execute(
            """
            SELECT group_id,
                   AVG(ra_deg)  AS avg_ra,
                   AVG(dec_deg) AS avg_dec,
                   COUNT(*)     AS n_files,
                   MIN(timestamp_iso) AS rep_time
            FROM hdf5_files
            WHERE stored = 1
              AND ra_deg IS NOT NULL
              AND dec_deg IS NOT NULL
            GROUP BY group_id
            ORDER BY MIN(timestamp_iso)
            """
        )
        rows = cursor.fetchall()

        if not rows:
            raise ValueError(
                "No stored HDF5 groups with RA/Dec metadata found in the database."
            )

        # ── Filter by angular proximity ──────────────────────────────
        import math

        cos_dec = math.cos(math.radians(source_dec_deg))
        # Guard against cos(dec)=0 at poles
        cos_dec = max(cos_dec, 1e-6)

        candidates: list[tuple[float, str, float, float, int]] = []
        for group_id, avg_ra, avg_dec, n_files, rep_time in rows:
            if require_complete and n_files < 16:
                continue

            # Dec separation
            d_dec = abs(avg_dec - source_dec_deg)
            if d_dec > beam_radius_deg:
                continue

            # RA separation (shortest arc, corrected for cos-dec)
            d_ra_raw = abs(avg_ra - source_ra_deg)
            if d_ra_raw > 180.0:
                d_ra_raw = 360.0 - d_ra_raw
            d_ra = d_ra_raw * cos_dec  # project to great-circle

            # Total angular offset (small-angle approx OK for < few degrees)
            ang_sep = math.sqrt(d_ra * d_ra + d_dec * d_dec)

            if ang_sep <= beam_radius_deg:
                candidates.append((ang_sep, rep_time, avg_ra, avg_dec, n_files))

        if not candidates:
            raise ValueError(
                f"No HDF5 groups found with source (RA={source_ra_deg:.3f}°, "
                f"Dec={source_dec_deg:.3f}°) within beam radius "
                f"{beam_radius_deg:.2f}°.  This means either:\n"
                f"  1) No data was taken when the source was transiting, or\n"
                f"  2) The beam_radius_deg is too small (primary beam FWHM ≈ 3.5°)."
            )

        # Sort by angular separation (closest first) and limit
        candidates.sort(key=lambda c: c[0])
        selected = candidates[:n_groups]

        group_times = [c[1] for c in selected]
        ang_seps = [c[0] for c in selected]

        logger.info(
            "Spatial match: found %d groups within %.2f° of source "
            "(RA=%.3f°, Dec=%.3f°).  Closest=%.3f°, farthest=%.3f°",
            len(group_times),
            beam_radius_deg,
            source_ra_deg,
            source_dec_deg,
            ang_seps[0],
            ang_seps[-1],
        )

        return group_times

    finally:
        conn.close()


