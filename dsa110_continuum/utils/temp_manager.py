"""Centralized temporary directory management.

Ensures all temporary file operations use the project temp directory instead
of system /tmp/. This module should be imported early in the application
lifecycle to configure Python's tempfile module.

The project temp directory hierarchy:
    /data/dsa110-contimg/tmp/
    ├── aegean/          # Photometry temporary files
    ├── cache/           # Cached data (FITS viewer, etc.)
    ├── downloads/       # API download temporary files
    ├── pytest/          # Test isolation directory
    └── ...              # Other subdirectories as needed

Environment Variables:
    CONTIMG_TEMP_DIR: Override project temp directory location. Set to a
        writable path (e.g. $HOME/tmp or /dev/shm/dsa110-contimg) if the
        default (CONTIMG_BASE_DIR/tmp) is not writable (e.g. in workers).
    CONTIMG_BASE_DIR: Base data directory; default temp is {BASE}/tmp.
    TMPDIR: Standard Unix temp directory (set by this module after config).

Examples
--------
>>> from dsa110_continuum.utils.temp_manager import get_temp_dir, get_temp_subdir
>>>
>>> # Get project temp directory
>>> temp_dir = get_temp_dir()
>>> print(temp_dir)
/data/dsa110-contimg/tmp
>>>
>>> # Get subdirectory for specific purpose
>>> cache_dir = get_temp_subdir("cache")
>>> fits_cache = cache_dir / "viewer"
>>>
>>> # tempfile module automatically uses project temp after import
>>> import tempfile
>>> with tempfile.TemporaryDirectory() as tmpdir:
...     # tmpdir will be under /data/dsa110-contimg/tmp/
...     print(tmpdir)
"""

import logging
import os
import tempfile
from pathlib import Path
from dsa110_continuum.utils import get_env_path

logger = logging.getLogger(__name__)

# Default project temp directory (can be overridden via CONTIMG_TEMP_DIR)
# Use configuration environment variable or standard default
_base_dir = str(get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg"))
if "jfaber" in str(_base_dir):
    # Avoid forbidden path for this specific user/environment
    _DEFAULT_PROJECT_TEMP = Path("/data/jfaber/tmp")
else:
    _DEFAULT_PROJECT_TEMP = Path(_base_dir) / "tmp"

# Cached temp directory (computed once on first access)
_PROJECT_TEMP_DIR: Path | None = None
_CONFIGURED: bool = False


def _get_project_temp_base() -> Path:
    """Get the base project temp directory from environment or default.

    Returns
    -------
    Path
        Base project temp directory
    """
    # Check environment variables in order of preference
    if env_temp := os.environ.get("CONTIMG_TEMP_DIR"):
        return Path(env_temp)

    # Use default
    return _DEFAULT_PROJECT_TEMP


def ensure_temp_dir() -> Path:
    """Ensure project temp directory exists and is writable.

    Creates the directory if it doesn't exist and verifies write permissions.
    If the configured directory is not writable, automatically falls back to
    a writable location (user home or system temp).

    Returns
    -------
    Path
        Project temp directory (guaranteed to exist and be writable)

    Examples
    --------
    >>> temp_dir = ensure_temp_dir()
    >>> assert temp_dir.exists()
    >>> assert temp_dir.is_dir()
    """
    global _PROJECT_TEMP_DIR

    # Return cached value if already verified
    if _PROJECT_TEMP_DIR is not None and _PROJECT_TEMP_DIR.exists():
        return _PROJECT_TEMP_DIR

    # Get base directory
    temp_dir = _get_project_temp_base()

    # Try to create and verify directory
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Verify writable using tempfile
        test_file = temp_dir / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()

            # Success!
            _PROJECT_TEMP_DIR = temp_dir
            logger.debug(f"Project temp directory verified: {temp_dir}")
            return temp_dir

        except (PermissionError, OSError) as e:
            logger.warning(
                f"Project temp directory exists but is not writable: {temp_dir}\n"
                f"Error: {e}\n"
                f"Falling back to writable location... "
                f"(Set CONTIMG_TEMP_DIR to a writable path to avoid fallback)"
            )

    except (PermissionError, OSError) as e:
        logger.warning(
            f"Cannot create project temp directory: {temp_dir}\n"
            f"Error: {e}\n"
            f"Falling back to writable location... "
            f"(Set CONTIMG_TEMP_DIR to a writable path to avoid fallback)"
        )

    # Check if strict paths mode is enabled
    strict_paths = os.environ.get("CONTIMG_STRICT_PATHS", "0").lower() in {"1", "true", "yes", "on"}

    # Fallback: try data_config._resolve_writable_path; on ImportError (e.g.
    # circular import during conversion workers), use system temp directly.
    try:
        from dsa110_continuum.database.data_config import _resolve_writable_path

        fallback_dir = _resolve_writable_path(
            str(temp_dir),
            description="project temp directory",
            warn_on_fallback=True,
        )
    except ImportError:
        # Circular import or data_config not ready (e.g. in spawn workers).
        # In strict mode, we MUST fail rather than fall back to system temp.
        if strict_paths:
            raise OSError(
                f"Cannot write to project temp directory '{temp_dir}' and strict path mode is enabled "
                "(CONTIMG_STRICT_PATHS=1). Set CONTIMG_TEMP_DIR to a writable path within allowed roots. "
                "Allowed roots: CONTIMG_ALLOWED_ROOTS. Forbidden roots: CONTIMG_FORBIDDEN_ROOTS."
            )

        # Use system temp so conversion/workers do not fail (non-strict mode only).
        system_tmp = Path(tempfile.gettempdir()) / "dsa110-contimg"
        try:
            system_tmp.mkdir(parents=True, exist_ok=True)
            (system_tmp / ".write_test").touch()
            (system_tmp / ".write_test").unlink()
            fallback_dir = system_tmp
            logger.warning(
                "Using system temp for project temp (import fallback): %s. "
                "Set CONTIMG_TEMP_DIR to a writable path to use project temp. "
                "To prevent this fallback, set CONTIMG_STRICT_PATHS=1.",
                fallback_dir,
            )
        except (PermissionError, OSError):
            fallback_dir = Path(tempfile.gettempdir())
            logger.warning(
                "Using system temp dir as-is: %s. Set CONTIMG_TEMP_DIR to a writable path. "
                "To prevent this fallback, set CONTIMG_STRICT_PATHS=1.",
                fallback_dir,
            )

    # Cache the fallback directory
    _PROJECT_TEMP_DIR = fallback_dir
    logger.info(f"Using fallback temp directory: {fallback_dir}")
    return fallback_dir


def configure_tempfile_module():
    """Configure Python's tempfile module to use project temp directory.

    This function:
    1. Ensures project temp directory exists
    2. Sets tempfile.tempdir to project directory
    3. Updates environment variables (TMPDIR, TEMP, TMP)

    Should be called early in application initialization (automatically
    called on module import).

    Examples
    --------
    >>> configure_tempfile_module()
    >>> import tempfile
    >>> print(tempfile.gettempdir())
    /data/dsa110-contimg/tmp
    """
    global _CONFIGURED

    if _CONFIGURED:
        return  # Already configured

    try:
        temp_dir = ensure_temp_dir()

        # Configure tempfile module
        tempfile.tempdir = str(temp_dir)

        # Set environment variables for consistency
        # (affects subprocesses and external tools)
        os.environ["TMPDIR"] = str(temp_dir)
        os.environ["TEMP"] = str(temp_dir)
        os.environ["TMP"] = str(temp_dir)

        _CONFIGURED = True

        logger.info(f"Configured tempfile module to use: {temp_dir}")

    except Exception as e:
        # Log error but don't fail - fall back to system temp
        logger.warning(
            f"Failed to configure project temp directory: {e}\n"
            f"Falling back to system temp directory"
        )


def get_temp_dir() -> Path:
    """Get the project temp directory (ensuring it exists).

    This is the primary function for obtaining the project temp directory.
    Use this instead of hardcoded paths or tempfile.gettempdir().

    Returns
    -------
    Path
        Project temp directory path

    Examples
    --------
    >>> from dsa110_continuum.utils.temp_manager import get_temp_dir
    >>> temp_dir = get_temp_dir()
    >>> output_file = temp_dir / "myfile.txt"
    """
    return ensure_temp_dir()


def get_temp_subdir(name: str, create: bool = True) -> Path:
    """Get a subdirectory within project temp directory.

    Use this for organizing temporary files by purpose:
    - "aegean" for photometry temporary files
    - "cache" for cached data
    - "downloads" for API downloads
    - "pytest" for test isolation

    Parameters
    ----------
    name : str
        Subdirectory name (no path separators)
    create : bool, optional
        Create subdirectory if it doesn't exist (default: True)

    Returns
    -------
    Path
        Subdirectory path

    Raises
    ------
    ValueError
        If name contains path separators

    Examples
    --------
    >>> cache_dir = get_temp_subdir("cache")
    >>> aegean_dir = get_temp_subdir("aegean")
    >>> viewer_cache = cache_dir / "fits_viewer"
    """
    if "/" in name or "\\" in name:
        raise ValueError(f"Subdirectory name cannot contain path separators: {name}")

    subdir = ensure_temp_dir() / name

    if create:
        subdir.mkdir(parents=True, exist_ok=True)

    return subdir


def cleanup_temp_files(
    max_age_hours: int = 24, dry_run: bool = False, exclude_patterns: list | None = None
) -> tuple[int, int]:
    """Remove old temporary files from project temp directory.

    Removes files and directories older than the specified age. Useful for
    automated cleanup scripts or maintenance tasks.

    Parameters
    ----------
    max_age_hours : int, optional
        Maximum age in hours (default: 24)
    dry_run : bool, optional
        If True, only report what would be deleted (default: False)
    exclude_patterns : list of str, optional
        Glob patterns to exclude from cleanup (e.g., ["pytest/*", "cache/*"])

    Returns
    -------
    tuple of (files_removed, dirs_removed)
        Number of files and directories removed

    Examples
    --------
    >>> # Clean up files older than 1 day
    >>> files, dirs = cleanup_temp_files(max_age_hours=24)
    >>> print(f"Cleaned up {files} files and {dirs} directories")
    >>>
    >>> # Dry run to see what would be deleted
    >>> cleanup_temp_files(max_age_hours=12, dry_run=True)
    """
    import time

    temp_dir = get_temp_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    exclude_patterns = exclude_patterns or []

    files_removed = 0
    dirs_removed = 0

    def should_exclude(path: Path) -> bool:
        """Check if path matches any exclude pattern."""
        rel_path = path.relative_to(temp_dir)
        for pattern in exclude_patterns:
            # Simple glob matching
            if rel_path.match(pattern):
                return True
        return False

    # Note: Walk in reverse depth order to remove directories after their contents
    for item in sorted(temp_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        # Skip if excluded
        if should_exclude(item):
            continue

        # Skip if not old enough
        try:
            if item.stat().st_mtime >= cutoff:
                continue
        except (FileNotFoundError, PermissionError):
            continue

        # Remove file or directory
        try:
            if item.is_file():
                if not dry_run:
                    item.unlink()
                logger.debug(f"{'Would remove' if dry_run else 'Removed'} file: {item}")
                files_removed += 1
            elif item.is_dir():
                # Only remove if empty
                if not any(item.iterdir()):
                    if not dry_run:
                        item.rmdir()
                    logger.debug(f"{'Would remove' if dry_run else 'Removed'} directory: {item}")
                    dirs_removed += 1
        except (PermissionError, OSError) as e:
            logger.warning(f"Cannot remove {item}: {e}")

    if dry_run:
        logger.info(f"DRY RUN: Would remove {files_removed} files and {dirs_removed} directories")
    else:
        logger.info(f"Removed {files_removed} files and {dirs_removed} directories")

    return files_removed, dirs_removed


def get_temp_stats() -> dict:
    """Get statistics about project temp directory usage.

    Returns
    -------
    dict
        Statistics including total size, file count, subdirectory info

    Examples
    --------
    >>> stats = get_temp_stats()
    >>> print(f"Temp directory size: {stats['size_gb']:.2f} GB")
    >>> print(f"Total files: {stats['file_count']}")
    """
    temp_dir = get_temp_dir()

    total_size = 0
    file_count = 0
    dir_count = 0
    subdirs = {}

    for item in temp_dir.rglob("*"):
        if item.is_file():
            try:
                size = item.stat().st_size
                total_size += size
                file_count += 1

                # Track by subdirectory
                try:
                    rel_path = item.relative_to(temp_dir)
                    if len(rel_path.parts) > 1:
                        subdir = rel_path.parts[0]
                        if subdir not in subdirs:
                            subdirs[subdir] = {"size": 0, "files": 0}
                        subdirs[subdir]["size"] += size
                        subdirs[subdir]["files"] += 1
                except ValueError:
                    pass
            except (FileNotFoundError, PermissionError):
                pass
        elif item.is_dir():
            dir_count += 1

    return {
        "path": str(temp_dir),
        "size_bytes": total_size,
        "size_mb": total_size / (1024**2),
        "size_gb": total_size / (1024**3),
        "file_count": file_count,
        "dir_count": dir_count,
        "subdirs": subdirs,
    }


# Auto-configure on module import
# This ensures tempfile module is configured before any other code runs
try:
    configure_tempfile_module()
except Exception as e:
    # Don't fail import if configuration fails
    logger.warning(f"Temp directory auto-configuration failed: {e}")
