# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Run isolation utilities for parallel test/validation runs.

This module provides utilities for generating unique run identifiers and
creating isolated run directories. This ensures parallel test runs don't
interfere with each other's artifacts.

Usage:
    from dsa110_continuum.utils.run_isolation import generate_run_id, create_run_directory

    # Generate a unique run ID
    run_id = generate_run_id()  # "20250102_143045_a3f9b81c"

    # Create an isolated run directory
    run_dir, run_id = create_run_directory(
        base_dir=Path("<scratch_dir>"),
        run_id=None,  # Auto-generate if None
        isolate=True
    )
    # run_dir = Path("<scratch_dir>/runs/20250102_143045_a3f9b81c")

The run ID format is: YYYYMMDD_HHMMSS_{8-char-hash}
- Timestamp: Human-readable, helps with debugging and manual inspection
- Hash: UUID4-based, ensures uniqueness even for simultaneous runs
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_run_id() -> str:
    """Generate a unique run ID based on timestamp and entropy.

    Format: YYYYMMDD_HHMMSS_{8-char-hash}

        The hash is derived from a UUID4, providing uniqueness even when
        multiple runs start in the same second.

    Returns
    -------
        str
        Unique run identifier string (e.g., "20250102_143045_a3f9b81c").

        Example
    -------
        >>> run_id = generate_run_id()
        >>> print(run_id)
        20250102_143045_a3f9b81c
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_bytes = uuid.uuid4().bytes
    short_hash = hashlib.sha256(random_bytes).hexdigest()[:8]
    return f"{timestamp}_{short_hash}"


def create_run_directory(
    base_dir: Path,
    run_id: str | None = None,
    isolate: bool = True,
    create_subdirs: bool = True,
) -> tuple[Path, str]:
    """Create an isolated run directory for test/validation runs.

        When isolation is enabled, creates a directory structure:
        {base_dir}/runs/{run_id}/
        {base_dir}/runs/{run_id}/cal_staging/
        {base_dir}/runs/{run_id}/conversion_staging/

        When isolation is disabled, returns the base directory directly.

    Parameters
    ----------
    base_dir : Path
        Base directory for runs (e.g., ${CONTIMG_SCRATCH_DIR}).
    run_id : Optional[str], optional
        Optional run identifier. Auto-generated if None.
    isolate : bool
        If True, create isolated run directory. If False, use base_dir.
    create_subdirs : bool
        If True, create common subdirectories (cal_staging, etc.).

    Returns
    -------
        Tuple[Path, str]
        Tuple of (run_directory_path, run_id).

    Raises
    ------
        OSError
        If directory creation fails.

        Example
    -------
        >>> from dsa110_continuum.unified_config import settings
        >>> run_dir, run_id = create_run_directory(
        ...     base_dir=settings.paths.scratch_dir,
        ...     isolate=True
        ... )
        >>> # run_dir will be like /stage/.../runs/20250102_143045_a3f9b81c
    """
    if not isolate:
        # Non-isolated mode: use base directory directly
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir, "default"

    # Generate run ID if not provided
    if run_id is None:
        run_id = generate_run_id()

    # Create isolated run directory
    run_dir = base_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create common subdirectories
    if create_subdirs:
        (run_dir / "cal_staging").mkdir(exist_ok=True)
        (run_dir / "conversion_staging").mkdir(exist_ok=True)
        (run_dir / "logs").mkdir(exist_ok=True)

    logger.debug(f"Created run directory: {run_dir}")
    return run_dir, run_id


def prepare_temp_environment(
    preferred_root: str | Path | None = None,
    *,
    cwd_to: str | Path | None = None,
    setup_casa_logs: bool = True,
) -> Path:
    """Prepare temp dirs and environment variables for CASA/casacore.

    - Ensures a stable temp directory under `<root>/tmp`
    - Sets TMPDIR/TMP/TEMP and CASA_TMPDIR environment variables
    - Sets CASALOGFILE to direct CASA logs to state/logs/casa/
    - Optionally changes the current working directory to `cwd_to`

    Parameters
    ----------
    preferred_root :
        Base directory for temp files (defaults to scratch_dir)
    cwd_to :
        Change working directory to this path after setup
    setup_casa_logs :
        Configure CASA logging environment (default True)

    Returns
    -------
        Path to the temp directory used.
    """
    import os

    from dsa110_continuum.unified_config import settings

    if preferred_root:
        root = Path(preferred_root)
    else:
        try:
            root = settings.paths.scratch_dir
        except Exception:
            # Fallback to tmpfs or /tmp
            from dsa110_continuum.utils import get_env_path

            root = get_env_path("CONTIMG_TMPFS_DIR", default="/dev/shm/dsa110-contimg")

    tmp = root / "tmp"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        # Best effort: fall back to PID directory
        from dsa110_continuum.database import data_config

        tmp = data_config.get_pid_dir()
        try:
            tmp.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            pass

    # Set common temp envs used by Python and (some) casacore paths
    os.environ.setdefault("TMPDIR", str(tmp))
    os.environ.setdefault("TMP", str(tmp))
    os.environ.setdefault("TEMP", str(tmp))
    # CASA-specific (best-effort; not all versions honor this)
    os.environ.setdefault("CASA_TMPDIR", str(tmp))

    # Set up CASA logging to the persistent logs directory
    if setup_casa_logs:
        from dsa110_continuum.utils.casa_init import setup_casa_log_directory

        setup_casa_log_directory()

    if cwd_to is not None:
        outdir = Path(cwd_to)
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            os.chdir(outdir)
        except (OSError, PermissionError):
            # If chdir fails, continue; env vars will still help
            pass

    return tmp


def cleanup_run_directory(
    run_dir: Path,
    keep_artifacts: bool = False,
    keep_logs: bool = True,
) -> bool:
    """Clean up a run directory after completion.

    Parameters
    ----------
    run_dir : Path
        Path to the run directory to clean up.
    keep_artifacts : bool
        If True, keep the directory (just log). If False, delete it.
    keep_logs : bool
        If True and deleting, preserve the logs/ subdirectory.

    Returns
    -------
        bool
        True if cleanup was performed, False if skipped or failed.

        Example
    -------
        >>> cleanup_run_directory(
        ...     Path("./runs/20250102_143045_a3f9b81c"),
        ...     keep_artifacts=False
        ... )
        True
    """
    if not run_dir.exists():
        logger.debug(f"Run directory does not exist, nothing to clean: {run_dir}")
        return False

    # Check if this looks like a run directory (safety check)
    if "runs" not in str(run_dir):
        logger.warning(f"Refusing to clean directory that doesn't look like a run dir: {run_dir}")
        return False

    if keep_artifacts:
        logger.info(f"Keeping run artifacts at: {run_dir}")
        return False

    try:
        if keep_logs:
            # Preserve logs directory
            logs_dir = run_dir / "logs"
            logs_backup = None
            if logs_dir.exists():
                logs_backup = run_dir.parent / f"{run_dir.name}_logs"
                if logs_backup.exists():
                    shutil.rmtree(logs_backup, ignore_errors=True)
                shutil.move(str(logs_dir), str(logs_backup))

            # Remove run directory
            shutil.rmtree(run_dir, ignore_errors=True)

            # Restore logs to new location
            if logs_backup and logs_backup.exists():
                run_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(logs_backup), str(run_dir / "logs"))
        else:
            # Remove everything
            shutil.rmtree(run_dir, ignore_errors=True)

        logger.info(f"Cleaned up run directory: {run_dir}")
        return True

    except Exception as e:
        logger.warning(f"Failed to clean up run directory {run_dir}: {e}")
        return False


def list_run_directories(
    base_dir: Path,
    max_age_hours: float | None = None,
) -> list[tuple[Path, str, datetime]]:
    """List all run directories under the base directory.

    Parameters
    ----------
    base_dir : Path
        Base directory containing runs/ subdirectory.
    max_age_hours : float, optional
        If provided, only return runs older than this.

    Returns
    -------
        list of tuples
        List of tuples: (run_dir_path, run_id, creation_time).

        Example
    -------
        >>> from dsa110_continuum.unified_config import settings
        >>> runs = list_run_directories(settings.paths.scratch_dir)
        >>> for run_dir, run_id, created in runs:
        ...     print(f"{run_id}: {created}")
    """
    runs_dir = base_dir / "runs"
    if not runs_dir.exists():
        return []

    results = []
    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue

        run_id = entry.name
        try:
            # Try to parse timestamp from run_id
            timestamp_str = "_".join(run_id.split("_")[:2])
            created = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
        except (ValueError, IndexError):
            # Fall back to directory modification time
            created = datetime.fromtimestamp(entry.stat().st_mtime)

        # Filter by age if requested
        if max_age_hours is not None:
            age_hours = (datetime.now() - created).total_seconds() / 3600
            if age_hours < max_age_hours:
                continue

        results.append((entry, run_id, created))

    # Sort by creation time, oldest first
    results.sort(key=lambda x: x[2])
    return results


def cleanup_old_runs(
    base_dir: Path,
    max_age_hours: float = 24.0,
    keep_latest: int = 5,
) -> int:
    """Clean up old run directories to prevent disk space accumulation.

        Removes run directories older than max_age_hours, but always keeps
        at least keep_latest runs (to preserve recent work).

    Parameters
    ----------
    base_dir : Path
        Base directory containing runs/ subdirectory.
    max_age_hours : float, optional
        Remove runs older than this (default: 24 hours).
    keep_latest : int, optional
        Always keep at least this many runs (default: 5).

    Returns
    -------
        int
        Number of directories cleaned up.

        Example
    -------
        >>> from dsa110_continuum.unified_config import settings
        >>> cleaned = cleanup_old_runs(
        ...     settings.paths.scratch_dir,
        ...     max_age_hours=24.0,
        ...     keep_latest=5
        ... )
        >>> print(f"Cleaned up {cleaned} old runs")
    """
    runs = list_run_directories(base_dir, max_age_hours=0)

    if len(runs) <= keep_latest:
        logger.debug(f"Only {len(runs)} runs exist, keeping all (min: {keep_latest})")
        return 0

    # Keep the most recent `keep_latest` runs
    runs_to_consider = runs[:-keep_latest] if keep_latest > 0 else runs

    cleaned = 0
    cutoff_time = datetime.now()

    for run_dir, run_id, created in runs_to_consider:
        age_hours = (cutoff_time - created).total_seconds() / 3600
        if age_hours >= max_age_hours:
            if cleanup_run_directory(run_dir, keep_artifacts=False, keep_logs=False):
                cleaned += 1
                logger.info(f"Cleaned up old run {run_id} (age: {age_hours:.1f}h)")

    return cleaned
