"""Path validation and safety utilities for DSA-110 continuum imaging pipeline.

This module provides functions for:
- Path traversal attack prevention (validate_path, sanitize_filename)
- Safe path construction (get_safe_path, is_safe_path)
- Filesystem policy enforcement (enforce_path_policy)
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

# Direct import to avoid circular dependency with common.utils.__init__
from dsa110_continuum.utils.env_utils import get_env_bool

logger = logging.getLogger(__name__)

# ==============================================================================
# Path Validation Functions
# ==============================================================================


def validate_path(
    user_path: str | Path,
    base_directory: str | Path,
    allow_absolute: bool = False,
) -> Path:
    """Validate and sanitize a user-provided path to prevent path traversal attacks.

    Parameters
    ----------
    user_path : str or Path
        The path provided by the user (may be relative or absolute)
    base_directory : str or Path
        The base directory that paths must be within
    allow_absolute : bool
        If True, allow absolute paths (still validated against base)

    Returns
    -------
    Path
        A validated Path object that is guaranteed to be within base_directory

    Raises
    ------
    ValueError
        If the path attempts to escape base_directory
        If the path is invalid or contains dangerous components
    """
    # codeql[py/path-injection]: This function intentionally accepts user input for validation.
    # The path is validated and sanitized before being returned.
    base_dir = Path(base_directory).resolve()
    user_path_obj = Path(user_path)

    # Resolve the user path
    if user_path_obj.is_absolute():
        if not allow_absolute:
            raise ValueError(f"Absolute paths not allowed: {user_path}")
        # codeql[py/path-injection]: User input is validated below
        resolved_path = user_path_obj.resolve()
    else:
        # Resolve relative to base directory
        # codeql[py/path-injection]: User input is validated below
        resolved_path = (base_dir / user_path_obj).resolve()

    # Check for path traversal attempts
    try:
        resolved_path.relative_to(base_dir)
    except ValueError:
        raise ValueError(f"Path traversal detected: {user_path} would escape {base_directory}")

    # Check for dangerous path components
    parts = resolved_path.parts
    if ".." in parts or "." in parts and parts.count(".") > 1:
        raise ValueError(f"Invalid path components detected: {user_path}")

    return resolved_path


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize a filename to prevent directory traversal and other attacks.

    Parameters
    ----------
    filename : str
        The filename to sanitize
    max_length : int
        Maximum length of the filename

    Returns
    -------
    str
        A sanitized filename safe for use in file operations

    Raises
    ------
    ValueError
        If the filename is invalid or contains dangerous characters
    """
    if not filename or not filename.strip():
        raise ValueError("Filename cannot be empty")

    # Remove path separators and dangerous characters
    dangerous_chars = ["/", "\\", "..", "\x00"]
    for char in dangerous_chars:
        if char in filename:
            raise ValueError(f"Filename contains dangerous character: {char}")

    # Remove leading/trailing whitespace and dots
    sanitized = filename.strip().strip(".")

    if not sanitized:
        raise ValueError("Filename is invalid after sanitization")

    # Limit length
    if len(sanitized) > max_length:
        raise ValueError(f"Filename too long (max {max_length} characters)")

    return sanitized


def get_safe_path(
    user_input: str | Path,
    base_dir: str | Path,
    subdirectory: str | None = None,
) -> Path:
    """Get a safe path by combining user input with a base directory.

    This is a convenience function that validates and constructs a safe path.

    Parameters
    ----------
    user_input : str or Path
        User-provided path component
    base_dir : str or Path
        Base directory (must exist)
    subdirectory : str or Path, optional
        Optional subdirectory within base_dir

    Returns
    -------
    Path
        A validated Path object

    Raises
    ------
    ValueError
        If validation fails
    FileNotFoundError
        If base_dir doesn't exist
    """
    base = Path(base_dir)
    if not base.exists():
        raise FileNotFoundError(f"Base directory does not exist: {base_dir}")

    if subdirectory:
        base = base / subdirectory
        base.mkdir(parents=True, exist_ok=True)

    return validate_path(user_input, base)


def is_safe_path(path: str | Path, allowed_dirs: list[str | Path]) -> bool:
    """Check if a path is within any of the allowed directories.

    Parameters
    ----------
    path : str or Path
        The path to check
    allowed_dirs : list of str or Path
        List of allowed base directories

    Returns
    -------
    bool
        True if the path is within one of the allowed directories
    """
    try:
        resolved_path = Path(path).resolve()
        for allowed_dir in allowed_dirs:
            allowed = Path(allowed_dir).resolve()
            try:
                resolved_path.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False
    except (ValueError, OSError):
        return False


# ==============================================================================
# Path Safety / Policy Enforcement Functions
# ==============================================================================

_DEFAULT_FORBIDDEN_ROOTS = ("/home", "/usr", "/var", "/tmp", "/opt")


def _normalize_path(p: Path) -> Path:
    """Normalize a path for comparison."""
    try:
        return p.expanduser().resolve(strict=False)
    except Exception:
        return p.expanduser().absolute()


def _is_under(child: Path, parent: Path) -> bool:
    """Check if child is a subdirectory of parent."""
    try:
        child_norm = _normalize_path(child)
        parent_norm = _normalize_path(parent)
        child_norm.relative_to(parent_norm)
        return True
    except Exception:
        return False


def _is_forbidden_target(path: Path) -> bool:
    """Check if a path is within a forbidden root directory."""
    # Direct import to avoid circular dependency with common.utils.__init__
    from dsa110_continuum.utils.env_utils import get_env_list

    forbidden_roots_str = get_env_list("CONTIMG_FORBIDDEN_ROOTS")
    if not forbidden_roots_str:
        forbidden_roots = [Path(p) for p in _DEFAULT_FORBIDDEN_ROOTS]
    else:
        forbidden_roots = [Path(p) for p in forbidden_roots_str]
    return any(_is_under(path, root) for root in forbidden_roots)


def _is_allowed_target(path: Path) -> bool:
    """Check if a path is within an allowed root directory."""
    # Direct import to avoid circular dependency with common.utils.__init__
    from dsa110_continuum.utils.env_utils import get_env_list

    allowed_roots_str = get_env_list("CONTIMG_ALLOWED_ROOTS")
    if not allowed_roots_str:
        return True
    allowed_roots = [Path(p) for p in allowed_roots_str]
    return any(_is_under(path, root) for root in allowed_roots)


def _check_target(name: str, value: str | None, strict: bool) -> None:
    """Check if a target path violates policy."""
    if not value:
        return

    p = Path(value)
    if _is_forbidden_target(p) or not _is_allowed_target(p):
        msg = (
            f"Filesystem path policy violation for {name}={p}. "
            "Adjust CONTIMG_ALLOWED_ROOTS / CONTIMG_FORBIDDEN_ROOTS, or set a different CONTIMG_* path."
        )
        if strict:
            raise OSError(msg)
        logger.warning(msg)


def _warn_or_raise_on_missing_mount(path: Path, strict: bool) -> None:
    """Warn or raise if a bind mount is not detected at path."""
    try:
        root_dev = os.stat("/").st_dev
        path_dev = os.stat(path).st_dev
    except (FileNotFoundError, OSError):
        if strict:
            raise
        return
    except Exception:
        return

    if path_dev != root_dev:
        return

    msg = (
        f"Expected bind-mount not detected at '{path}'. "
        "In Docker, this usually means writes will go to the container overlay and fill host /var/lib/docker."
    )
    if strict:
        raise OSError(msg)
    logger.warning(msg)


def _maybe_check_root_free_space(strict: bool) -> None:
    """Check root filesystem free space if thresholds are configured."""
    min_free_gb_raw = os.environ.get("CONTIMG_MIN_ROOT_FREE_GB")
    min_free_pct_raw = os.environ.get("CONTIMG_MIN_ROOT_FREE_PCT")

    if not min_free_gb_raw and not min_free_pct_raw:
        return

    usage = shutil.disk_usage("/")
    free_gb = usage.free / 1024 / 1024 / 1024
    free_pct = (usage.free / usage.total) * 100 if usage.total else 0.0

    try:
        min_free_gb = float(min_free_gb_raw) if min_free_gb_raw else None
    except ValueError:
        min_free_gb = None

    try:
        min_free_pct = float(min_free_pct_raw) if min_free_pct_raw else None
    except ValueError:
        min_free_pct = None

    violated = False
    if min_free_gb is not None and free_gb < min_free_gb:
        violated = True
    if min_free_pct is not None and free_pct < min_free_pct:
        violated = True

    if not violated:
        return

    msg = (
        f"Root filesystem low free space: free={free_gb:.2f}GiB ({free_pct:.1f}%). "
        "Set CONTIMG_* paths to /data or /dev/shm and clean up before running the pipeline."
    )
    if strict:
        raise OSError(msg)
    logger.warning(msg)


def enforce_path_policy() -> None:
    """Enforce filesystem path policy at startup.

    This function checks all CONTIMG_* path environment variables against
    the configured allowed and forbidden roots. In strict mode
    (CONTIMG_STRICT_PATHS=1), violations raise OSError.

    Raises
    ------
    OSError
        If CONTIMG_STRICT_PATHS is set and a policy violation is detected.
    """
    strict = get_env_bool("CONTIMG_STRICT_PATHS", default=False)

    _maybe_check_root_free_space(strict)

    _check_target("CONTIMG_BASE_DIR", os.environ.get("CONTIMG_BASE_DIR"), strict)
    _check_target("CONTIMG_STAGING_DIR", os.environ.get("CONTIMG_STAGING_DIR"), strict)
    _check_target("CONTIMG_SCRATCH_DIR", os.environ.get("CONTIMG_SCRATCH_DIR"), strict)
    _check_target("CONTIMG_TMPFS_DIR", os.environ.get("CONTIMG_TMPFS_DIR"), strict)
    _check_target("CONTIMG_STATE_DIR", os.environ.get("CONTIMG_STATE_DIR"), strict)
    _check_target("PIPELINE_DB", os.environ.get("PIPELINE_DB"), strict)
    _check_target("CASA_HOME_DIR", os.environ.get("CASA_HOME_DIR"), strict)
    _check_target("TMPDIR", os.environ.get("TMPDIR"), strict)
    _check_target("TEMP", os.environ.get("TEMP"), strict)
    _check_target("TMP", os.environ.get("TMP"), strict)
    _check_target("NUMBA_CACHE_DIR", os.environ.get("NUMBA_CACHE_DIR"), strict)
    _check_target("MPLCONFIGDIR", os.environ.get("MPLCONFIGDIR"), strict)
    _check_target("ASTROPY_CACHE_DIR", os.environ.get("ASTROPY_CACHE_DIR"), strict)

    if Path("/.dockerenv").exists():
        base_dir = os.environ.get("CONTIMG_BASE_DIR")
        if base_dir:
            _warn_or_raise_on_missing_mount(Path(base_dir), strict)

        staging_dir = os.environ.get("CONTIMG_STAGING_DIR")
        if staging_dir:
            _warn_or_raise_on_missing_mount(Path(staging_dir), strict)
