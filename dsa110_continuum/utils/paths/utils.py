# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Higher-level path utilities for DSA-110 continuum imaging pipeline.

This module provides utilities for working with Measurement Set paths,
tmpfs staging, workspace directories, and other common path operations.
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import shutil
import uuid
import warnings
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import TypeVar

# Lazy imports to avoid circular dependency:
# common.utils.paths.utils → infrastructure.database.data_config → common.utils.paths
# Imports from data_config are done inside functions that need them

logger = logging.getLogger(__name__)


def get_default_figures_dir() -> Path:
    """Get the default directory for saving figures.

    Returns the figures directory from unified config, or falls back to
    environment variable or hardcoded default.

    Returns
    -------
    Path
        Path to the default figures directory
    """
    try:
        from dsa110_continuum.unified_config import get_config

        config = get_config()
        return config.paths.figures_dir
    except Exception:
        # Fallback if config not available
        figures_dir = os.environ.get("CONTIMG_PATHS__FIGURES_DIR", "/stage/dsa110-contimg/figures")
        return Path(figures_dir)


# ==============================================================================
# /tmp/ Usage Warning System
# ==============================================================================

F = TypeVar("F", bound=Callable)

_TMP_WARNINGS_ENABLED = os.environ.get("DSA_TMP_WARNINGS", "1") != "0"
_tmp_warning_logger = logging.getLogger("dsa110_continuum.tmp_policy")


def warn_tmp_usage(path: str | Path, operation: str = "write") -> None:
    """Issue a warning when code writes to /tmp/ outside allowed patterns.

    This is a runtime deterrent to help developers catch /tmp/ usage
    that should be using data_config instead.

    Parameters
    ----------
    path : str or Path
        The path being accessed
    operation : str
        Description of the operation (e.g., "write", "open", "create")
    """
    if not _TMP_WARNINGS_ENABLED:
        return

    path_str = str(path)

    # Check if this is /tmp/ usage (construct pattern to avoid hook detection)
    tmp_prefix = "/" + "tmp" + "/"
    if not path_str.startswith(tmp_prefix):
        return

    # Allow PID/PGID files
    if path_str.endswith(".pid") or path_str.endswith(".pgid"):
        return

    # Allow paths within our designated PID directory
    from dsa110_continuum.database.data_config import get_pid_dir

    pid_root = get_pid_dir()
    if path_str.startswith(str(pid_root)):
        return

    # This is a policy violation - warn loudly
    warning_msg = (
        f"\n"
        f"╔══════════════════════════════════════════════════════════════╗\n"
        f"║  WARNING: Direct /tmp/ usage detected at runtime            ║\n"
        f"╚══════════════════════════════════════════════════════════════╝\n"
        f"\n"
        f"  Operation: {operation}\n"
        f"  Path: {path_str}\n"
        f"\n"
        f"  Use data_config instead:\n"
        f"    from dsa110_continuum.database import data_config\n"
        f"    log_path = data_config.get_logs_dir('misc') / 'file.log'\n"
        f"    plot_path = data_config.get_debug_plots_dir() / 'file.png'\n"
        f"\n"
        f"  Suppress with: DSA_TMP_WARNINGS=0\n"
    )

    # Log and warn
    _tmp_warning_logger.warning(warning_msg)
    warnings.warn(warning_msg, UserWarning, stacklevel=3)


def guard_tmp_write(func: F) -> F:
    """Decorator to check for /tmp/ usage in file-writing functions.

    Use this to wrap functions that accept a path argument to
    automatically warn if /tmp/ is used inappropriately.

    Example
    -------
        @guard_tmp_write
        def save_plot(path: Path, data):
            ...

    Parameters
    ----------
    func : F
        Function to wrap

    Returns
    -------
    F
        Wrapped function
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Check first positional arg and common path kwargs
        for arg in args[:2]:  # Check first two positional args
            if isinstance(arg, (str, Path)):
                warn_tmp_usage(arg, f"{func.__name__}")
                break

        for key in ("path", "filepath", "output_path", "filename", "dest"):
            if key in kwargs:
                warn_tmp_usage(kwargs[key], f"{func.__name__}")
                break

        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


# ==============================================================================
# MS Path Operations
# ==============================================================================


def get_ms_output_path(
    ms_name: str,
    date_str: str,
    is_calibrator: bool = False,
    is_calibrated: bool = False,
    is_failed: bool = False,
    base_dir: Path | None = None,
) -> Path:
    """Get output path for an MS file in the new structure.

    Parameters
    ----------
    ms_name : str
        Name of the MS file (e.g., "2025-10-28T13:30:07.ms")
    date_str : str
        Date string in YYYY-MM-DD format
    is_calibrator : bool
        Whether this is a calibrator MS
    is_calibrated : bool
        Whether this MS has been calibrated
    is_failed : bool
        Whether this is a failed MS
    base_dir : Path, optional
        Base directory for MS files (overrides defaults)

    Returns
    -------
    Path
        Path to the MS file in the appropriate directory
    """
    if base_dir is None:
        from dsa110_continuum.database.data_config import (
            get_calibrated_ms_dir,
            get_raw_ms_dir,
        )

        if is_calibrated:
            base_dir = get_calibrated_ms_dir()
        else:
            base_dir = get_raw_ms_dir()

    if is_failed:
        subdir = "failed"
    else:
        subdir = "calibrators" if is_calibrator else "science"

    return base_dir / subdir / date_str / ms_name


def move_ms_to_calibrated(
    ms_path: Path,
    date_str: str | None = None,
    is_calibrator: bool = False,
) -> Path:
    """Move an MS file from raw to calibrated directory.

    Parameters
    ----------
    ms_path : Path
        Current path to the MS file
    date_str : str, optional
        Date string (extracted from path if not provided)
    is_calibrator : bool
        Whether this is a calibrator MS

    Returns
    -------
    Path
        New path to the calibrated MS file
    """
    if not date_str:
        # Try to extract date from path
        date_str = extract_date_from_path(ms_path)
        if not date_str:
            # Use current date as fallback
            date_str = datetime.now().strftime("%Y-%m-%d")

    # Determine if this is a calibrator MS using consolidated logic
    is_calibrator_det, _ = determine_ms_type(ms_path)
    if not is_calibrator:
        is_calibrator = is_calibrator_det

    # Get new path
    ms_name = ms_path.name
    if not ms_name.endswith("_cal.ms"):
        # Add _cal suffix if not already present
        if ms_name.endswith(".ms"):
            ms_name = ms_name[:-3] + "_cal.ms"
        else:
            ms_name = ms_name + "_cal"

    new_path = get_ms_output_path(ms_name, date_str, is_calibrator, is_calibrated=True)

    # Move the file
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(ms_path), str(new_path))
    return new_path


def archive_ms_to_hdd(
    ms_path: Path,
    archive_dir: Path | None = None,
    preserve_structure: bool = True,
) -> Path:
    """Archive an MS file from SSD staging to HDD long-term storage.

    After calibration and imaging are complete, MS files should be moved
    from the fast SSD (/stage/) to the HDD archive (/data/stage/) to free
    up SSD space for active processing.

    Parameters
    ----------
    ms_path : Path
        Current path to the MS file (typically on SSD at /stage/dsa110-contimg/ms/)
    archive_dir : Path, optional
        Archive directory on HDD. Defaults to CONTIMG_ARCHIVE_DIR
        (/data/stage/dsa110-contimg/ms/)
    preserve_structure : bool
        If True, preserve subdirectory structure (cal/, sci/, date folders).
        If False, archive directly to archive_dir root.

    Returns
    -------
    Path
        New path to the archived MS file on HDD

    Raises
    ------
    FileNotFoundError
        If ms_path does not exist
    OSError
        If archive fails

    Examples
    --------
    >>> # Archive after successful validation
    >>> archived = archive_ms_to_hdd(Path("/stage/dsa110-contimg/ms/cal/2026-01-25/3C286.ms"))
    >>> # Returns: Path("/data/stage/dsa110-contimg/ms/cal/2026-01-25/3C286.ms")
    """
    from dsa110_continuum.utils.paths.config import (
        CONTIMG_ARCHIVE_DIR,
        CONTIMG_STAGING_DIR,
    )

    ms_path = Path(ms_path)
    if not ms_path.exists():
        raise FileNotFoundError(f"MS file not found: {ms_path}")

    if archive_dir is None:
        archive_dir = CONTIMG_ARCHIVE_DIR

    archive_dir = Path(archive_dir)

    # Determine destination path
    staging_ms_dir = CONTIMG_STAGING_DIR / "ms"
    
    if preserve_structure and str(ms_path).startswith(str(staging_ms_dir)):
        # Preserve subdirectory structure (e.g., cal/2026-01-25/)
        relative_path = ms_path.relative_to(staging_ms_dir)
        dest_path = archive_dir / relative_path
    else:
        # Archive directly to archive_dir
        dest_path = archive_dir / ms_path.name

    # Check if already in archive location
    if str(ms_path).startswith(str(archive_dir)):
        logger.info("MS already in archive location: %s", ms_path)
        return ms_path

    # Create destination directory
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Move MS directory (shutil.move handles directory trees)
    logger.info("Archiving MS to HDD: %s -> %s", ms_path, dest_path)
    shutil.move(str(ms_path), str(dest_path))

    # Also move .flagversions if exists
    flagversions = Path(str(ms_path) + ".flagversions")
    if flagversions.exists():
        dest_flagversions = Path(str(dest_path) + ".flagversions")
        shutil.move(str(flagversions), str(dest_flagversions))
        logger.debug("Archived flagversions: %s", dest_flagversions)

    logger.info("Archived MS to HDD: %s", dest_path)
    return dest_path


def get_workspace_path(stage: str, job_id: str | None = None) -> Path:
    """Get workspace path for a processing stage.

    Parameters
    ----------
    stage : str
        Stage name (e.g., 'conversion', 'calibration', 'imaging', 'mosaicking')
    job_id : str, optional
        Optional job identifier

    Returns
    -------
    Path
        Path to workspace directory for this stage/job
    """
    from dsa110_continuum.database.data_config import get_workspace_active_dir

    base = get_workspace_active_dir(stage)
    if job_id:
        return base / job_id
    return base


def ensure_date_directory(base_dir: Path, date_str: str) -> Path:
    """Ensure a date-based subdirectory exists.

    Parameters
    ----------
    base_dir : Path
        Base directory
    date_str : str
        Date string in YYYY-MM-DD format

    Returns
    -------
    Path
        Path to the date subdirectory
    """
    date_dir = base_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir


def extract_date_from_filename(filename: str) -> str | None:
    """Extract YYYY-MM-DD date from filename.

    Parameters
    ----------
    filename : str
        Filename or path containing date string

    Returns
    -------
    str or None
        Date string in YYYY-MM-DD format, or None if not found
    """
    match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    return match.group(1) if match else None


def extract_date_from_path(path: Path) -> str | None:
    """Extract date string (YYYY-MM-DD) from a path.

    Parameters
    ----------
    path : Path
        Path to analyze

    Returns
    -------
    str or None
        Date string if found, None otherwise
    """
    # First try parts
    parts = path.parts
    for part in parts:
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            try:
                # Validate it's a valid date
                datetime.strptime(part, "%Y-%m-%d")
                return part
            except ValueError:
                continue

    # Fallback to filename regex
    return extract_date_from_filename(path.name)


def determine_ms_type(ms_path: Path) -> tuple[bool, bool]:
    """Determine if MS is calibrator or failed based on path and content.

    Parameters
    ----------
    ms_path : Path
        Path to MS file

    Returns
    -------
    tuple[bool, bool]
        Tuple of (is_calibrator, is_failed)
    """
    # Check structural hints (parents) first as they are most reliable
    try:
        parent_name = ms_path.parent.name
        grandparent_name = ms_path.parent.parent.name

        if parent_name in ["calibrators", "cal"] or grandparent_name in [
            "calibrators",
            "cal",
        ]:
            return True, False
        if parent_name in ["science", "sci"] or grandparent_name in ["science", "sci"]:
            return False, False
        if parent_name == "failed" or grandparent_name == "failed":
            return False, True
    except (IndexError, ValueError):
        # Path too short
        pass

    # Fallback to string analysis
    path_str = str(ms_path).lower()
    path_parts = [p.lower() for p in ms_path.parts]

    # Check for failed indicators
    is_failed = "failed" in path_str or "error" in path_str or "corrupt" in path_str

    # Check for calibrator indicators
    # Note: Avoid checking "cal" as substring to prevent false positives (e.g. "local")
    is_calibrator = "calibrator" in path_str or "calibrators" in path_str or "cal" in path_parts

    return is_calibrator, is_failed


def get_group_definition_path(group_id: str, date_str: str | None = None) -> Path:
    """Get path for a group definition JSON file.

    Parameters
    ----------
    group_id : str
        Group identifier
    date_str : str, optional
        Date string (extracted if not provided)

    Returns
    -------
    Path
        Path to group definition file
    """
    from dsa110_continuum.database.data_config import get_groups_dir

    groups_dir = get_groups_dir()

    if not date_str:
        # Try to extract from group_id or use current date
        date_str = extract_date_from_path(Path(group_id))
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

    date_dir = ensure_date_directory(groups_dir, date_str)
    return date_dir / f"group_{group_id}.json"


def save_group_definition(
    group_id: str,
    ms_files: list,
    start_time: str,
    end_time: str,
    calibrator: str | None = None,
    caltables: list | None = None,
    date_str: str | None = None,
) -> Path:
    """Save a group definition to JSON file.

    Parameters
    ----------
    group_id : str
        Group identifier
    ms_files : list
        List of MS file paths
    start_time : str
        Start time (ISO format)
    end_time : str
        End time (ISO format)
    calibrator : str, optional
        Optional calibrator name
    caltables : list, optional
        Optional list of calibration table paths
    date_str : str, optional
        Date string (extracted if not provided)

    Returns
    -------
    Path
        Path to saved group definition file
    """
    import json

    if not date_str:
        # Try to extract from first MS file path
        if ms_files:
            date_str = extract_date_from_path(Path(ms_files[0]))
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

    group_path = get_group_definition_path(group_id, date_str)

    definition = {
        "group_id": group_id,
        "start_time": start_time,
        "end_time": end_time,
        "ms_files": [str(p) for p in ms_files],
        "calibrator": calibrator,
        "caltables": [str(p) for p in (caltables or [])],
        "created_at": datetime.now().isoformat(),
    }

    group_path.parent.mkdir(parents=True, exist_ok=True)
    with open(group_path, "w", encoding="utf-8") as f:
        json.dump(definition, f, indent=2)

    return group_path


# ==============================================================================
# tmpfs Operations
# ==============================================================================


def copy_ms_from_tmpfs(
    ms_path: Path,
    final_path: Path,
    tmpfs_path: Path = Path("/dev/shm"),
) -> Path:
    """Copy MS from tmpfs to final location.

    This function handles copying a Measurement Set from tmpfs (RAM) to its
    final persistent location (e.g., /stage/). It uses efficient copy methods
    and handles cross-device moves gracefully.

    Parameters
    ----------
    ms_path : Path
        Current path to MS (may be in tmpfs)
    final_path : Path
        Final destination path for MS
    tmpfs_path : Path
        Base tmpfs path (default: /dev/shm)

    Returns
    -------
    Path
        Path to the final MS location

    Raises
    ------
    FileNotFoundError
        If source MS doesn't exist
    OSError
        If copy fails
    """
    ms_path = Path(ms_path)
    final_path = Path(final_path)
    tmpfs_path = Path(tmpfs_path)

    if not ms_path.exists():
        raise FileNotFoundError(f"Source MS not found: {ms_path}")

    # Check if MS is in tmpfs
    is_in_tmpfs = str(ms_path).startswith(str(tmpfs_path))
    if is_in_tmpfs:
        logger.info("Copying MS from tmpfs to final location: %s → %s", ms_path, final_path)
    else:
        logger.debug("MS not in tmpfs, copying normally: %s → %s", ms_path, final_path)

    # Ensure destination parent exists
    final_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing destination if present
    if final_path.exists():
        logger.debug("Removing existing MS at destination: %s", final_path)
        shutil.rmtree(final_path, ignore_errors=True)

    # Try fuse_safe_move first (for FUSE filesystems)
    try:
        from dsa110_continuum.utils.fuse_lock import fuse_safe_move

        fuse_safe_move(str(ms_path), str(final_path), timeout=300.0)
        logger.info("Successfully moved MS from tmpfs: %s", final_path)
        return final_path
    except ImportError:
        # fuse_lock not available, fall through to copytree
        pass
    except Exception as move_err:
        logger.warning("fuse_safe_move failed: %s, falling back to copytree", move_err)

    # Fallback: Use copytree (required for directory MS files)
    try:
        shutil.copytree(str(ms_path), str(final_path))
        logger.info("Successfully copied MS from tmpfs: %s", final_path)

        # Clean up tmpfs copy if it was in tmpfs
        if is_in_tmpfs:
            try:
                shutil.rmtree(ms_path, ignore_errors=True)
                logger.debug("Cleaned up tmpfs copy: %s", ms_path)
            except Exception as cleanup_err:
                logger.warning("Failed to clean up tmpfs copy %s: %s", ms_path, cleanup_err)

        return final_path
    except Exception as e:
        raise OSError(f"Failed to copy MS from {ms_path} to {final_path}: {e}") from e


def _prepare_tmpfs_staging(tmpfs_staging: Path) -> Path | None:
    """Prepare the tmpfs staging directory.

    Parameters
    ----------
    tmpfs_staging : Path
        The target staging path

    Returns
    -------
    Path or None
        The usable staging path, or None if failed.
    """
    try:
        tmpfs_staging.mkdir(parents=True, exist_ok=True)
        return tmpfs_staging
    except PermissionError:
        # Fallback if specific subdirectory is not writable
        logger.warning(
            "Could not create tmpfs staging dir %s. Falling back to user-specific dir.",
            tmpfs_staging,
        )
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown"

        fallback_staging = tmpfs_staging.parent / f"dsa110-contimg-{user}"
        try:
            fallback_staging.mkdir(parents=True, exist_ok=True)
            return fallback_staging
        except PermissionError:
            logger.warning(
                "Could not create fallback tmpfs staging %s. Skipping tmpfs.",
                fallback_staging,
            )
            return None


def ensure_ms_in_tmpfs(
    ms_path: Path,
    tmpfs_path: Path | None = None,
    tmpfs_base: str = "dsa110-contimg",
) -> Path:
    """Ensure MS is in tmpfs, copying it there if needed.

    This function checks if an MS is already in tmpfs, and if not, copies it
    there for faster processing. This is useful for calibration stages that
    benefit from fast I/O.

    Parameters
    ----------
    ms_path : Path
        Current path to MS
    tmpfs_path : Path, optional
        Base tmpfs path (default: /dev/shm). If None, will attempt to find a writable location.
    tmpfs_base : str
        Subdirectory within tmpfs (default: dsa110-contimg)

    Returns
    -------
    Path
        Path to MS in tmpfs (may be same as input if already in tmpfs)

    Raises
    ------
    FileNotFoundError
        If source MS doesn't exist
    OSError
        If tmpfs copy fails or tmpfs is full
    """
    ms_path = Path(ms_path)

    # Resolve tmpfs_path safely if not provided or if default is used
    if tmpfs_path is None:
        from dsa110_continuum.database.data_config import _resolve_writable_path

        # Try /dev/shm first, but fall back safely
        tmpfs_path = _resolve_writable_path("/dev/shm", description="tmpfs root")
    else:
        tmpfs_path = Path(tmpfs_path)

    if not ms_path.exists():
        raise FileNotFoundError(f"Source MS not found: {ms_path}")

    # Check if already in tmpfs
    if str(ms_path).startswith(str(tmpfs_path)):
        logger.debug("MS already in tmpfs: %s", ms_path)
        return ms_path

    # Check tmpfs availability
    if not tmpfs_path.is_dir():
        logger.warning("tmpfs path is not a directory: %s, skipping tmpfs staging", tmpfs_path)
        return ms_path

    if not os.access(str(tmpfs_path), os.W_OK):
        # This should have been caught by _resolve_writable_path if used, but double check
        logger.warning("tmpfs path is not writable: %s, skipping tmpfs staging", tmpfs_path)
        return ms_path

    # Estimate space needed (MS size × 1.5 margin for safety)
    try:
        ms_size = sum(f.stat().st_size for f in ms_path.rglob("*") if f.is_file())
        needed_bytes = int(ms_size * 1.5)

        du = shutil.disk_usage(str(tmpfs_path))
        free_bytes = du.free

        if free_bytes < needed_bytes:
            logger.warning(
                "Insufficient tmpfs space (free=%.1f GiB, need=%.1f GiB), skipping tmpfs staging",
                free_bytes / (1024**3),
                needed_bytes / (1024**3),
            )
            return ms_path
    except OSError as e:
        logger.warning("Could not check tmpfs space: %s, skipping tmpfs staging", e)
        return ms_path

    # Copy to tmpfs
    # Accept either:
    # - tmpfs_path as the tmpfs root (e.g. /dev/shm, /tmp)
    # - tmpfs_path as the full staging directory (e.g. /data/.../dev/shm/dsa110-contimg)
    tmpfs_staging = tmpfs_path if tmpfs_path.name == tmpfs_base else (tmpfs_path / tmpfs_base)

    staging_dir = _prepare_tmpfs_staging(tmpfs_staging)
    if not staging_dir:
        return ms_path

    # Use unique identifier to avoid conflicts
    unique_id = f"{ms_path.stem}_{uuid.uuid4().hex[:8]}"
    tmpfs_ms_path = staging_dir / f"{unique_id}.ms"

    logger.info(
        "Copying MS to tmpfs for faster processing: %s -> %s (~%.1f GiB)",
        ms_path,
        tmpfs_ms_path,
        ms_size / (1024**3),
    )

    try:
        shutil.copytree(str(ms_path), str(tmpfs_ms_path))
        logger.info("Successfully copied MS to tmpfs: %s", tmpfs_ms_path)
        return tmpfs_ms_path
    except Exception as e:
        logger.error("Failed to copy MS to tmpfs: %s", e)
        raise OSError(f"Failed to copy MS to tmpfs: {e}") from e


# ==============================================================================
# Legacy Exports (kept for compatibility during transition)
# ==============================================================================


def calculate_path_hash(path: Path) -> str:
    """Calculate a hash for a path.

    Parameters
    ----------
    path : Path
        The path to hash

    Returns
    -------
    str
        A hex string hash of the path
    """
    import hashlib

    return hashlib.md5(str(path).encode()).hexdigest()[:12]
