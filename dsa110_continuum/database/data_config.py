"""Configuration for data registry paths and auto-publish settings.

This module implements the redesigned directory structure that aligns with
the scientific workflow. The new structure provides:
- Clear data provenance (raw → calibrated → images → mosaics)
- Better organization by processing stage
- Separation of active processing from final products
- Support for backward compatibility during migration
"""

import getpass
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

# Direct import to avoid circular dependency with common.utils.__init__
from dsa110_continuum.utils.env_utils import get_env_bool
from dsa110_continuum.utils.paths import resolve_paths

logger = logging.getLogger(__name__)

_DEFAULT_FORBIDDEN_ROOTS = ("/usr", "/var", "/tmp", "/opt")


def _normalize_path(p: Path) -> Path:
    try:
        return p.expanduser().resolve(strict=False)
    except Exception:
        return p.expanduser().absolute()


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child_norm = _normalize_path(child)
        parent_norm = _normalize_path(parent)
        child_norm.relative_to(parent_norm)
        return True
    except Exception:
        return False


def _is_forbidden_target(path: Path) -> bool:
    from dsa110_continuum.utils import get_env_list

    forbidden_roots_str = get_env_list("CONTIMG_FORBIDDEN_ROOTS")
    if not forbidden_roots_str:
        forbidden_roots = [Path(p) for p in _DEFAULT_FORBIDDEN_ROOTS]
    else:
        forbidden_roots = [Path(p) for p in forbidden_roots_str]

    for root in forbidden_roots:
        if _is_under(path, root):
            return True
    return False


def _is_allowed_target(path: Path) -> bool:
    from dsa110_continuum.utils import get_env_list

    allowed_roots_str = get_env_list("CONTIMG_ALLOWED_ROOTS")
    if not allowed_roots_str:
        return True
    allowed_roots = [Path(p) for p in allowed_roots_str]
    return any(_is_under(path, root) for root in allowed_roots)


def _resolve_writable_path(
    path_str: str,
    fallback_root: Path | None = None,
    description: str = "path",
    warn_on_fallback: bool = True,
) -> Path:
    """Resolve a path, falling back to a writable location if necessary.

    This ensures that we always have a writable directory, preventing
    'permission denied' errors in environments where the default system
    paths are not writable by the current user.

    Parameters
    ----------
    path_str : str
        The desired path.
    fallback_root : Path, optional
        Root directory for fallback path. If None, uses ~/.dsa110-contimg or /tmp.
    description : str
        Description of the path for logging.
    warn_on_fallback : bool
        Whether to log a warning when falling back. If False, logs at INFO level.

    Returns
    -------
    Path
        A writable path.
    """
    path = Path(path_str)

    strict_paths = get_env_bool("CONTIMG_STRICT_PATHS", default=False)
    primary_permitted = (not _is_forbidden_target(path)) and _is_allowed_target(path)
    if not primary_permitted:
        msg = (
            f"Configured {description} '{path}' is not permitted by path policy. "
            f"Set CONTIMG_ALLOWED_ROOTS and CONTIMG_FORBIDDEN_ROOTS appropriately."
        )
        if strict_paths and not description.startswith("PID"):
            # Don't raise for system directories if fallback is available
            pass
        log_level = logging.WARNING if warn_on_fallback else logging.INFO
        logger.log(log_level, msg)

    # Try to create the directory
    if primary_permitted:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except (PermissionError, OSError):
            pass

    if strict_paths and not description.startswith("PID"):
        if fallback_root is None:
            # If no fallback root is provided, we can try to use a default one
            # instead of raising an error immediately
            pass

            raise OSError(
                f"Could not write to configured {description} '{path}'. "
                "Strict path mode is enabled (CONTIMG_STRICT_PATHS=1). "
                "Set the appropriate CONTIMG_* directory environment variables to a writable location."
            )

        new_path = fallback_root / path.name
        if _is_forbidden_target(new_path) or not _is_allowed_target(new_path):
            raise OSError(
                f"Configured {description} '{path}' is not writable, and fallback '{new_path}' is not permitted "
                "by path policy."
            )

        try:
            new_path.mkdir(parents=True, exist_ok=True)
            if os.access(new_path, os.W_OK):
                log_level = logging.WARNING if warn_on_fallback else logging.INFO
                logger.log(
                    log_level,
                    "Could not write to configured %s '%s'. Falling back to writable location: '%s'",
                    description,
                    path,
                    new_path,
                )
                return new_path
        except (PermissionError, OSError):
            pass

        raise OSError(
            f"Could not write to configured {description} '{path}' or fallback '{new_path}'. "
            "Strict path mode is enabled (CONTIMG_STRICT_PATHS=1)."
        )

    # If we are here, the path is not usable. Determine fallback.
    if fallback_root is None:
        # In strict mode, check if home directory fallback is allowed
        if strict_paths:
            # Try user home first, but verify it's allowed
            try:
                fallback_root = Path.home() / ".dsa110-contimg"
                # Check if home directory is forbidden
                if _is_forbidden_target(fallback_root) or not _is_allowed_target(fallback_root):
                    raise OSError(
                        f"Could not write to configured {description} '{path}'. "
                        f"Home directory fallback '{fallback_root}' is not permitted by path policy. "
                        "Strict path mode is enabled (CONTIMG_STRICT_PATHS=1). "
                        "Set the appropriate CONTIMG_* directory environment variables to a writable location "
                        "within allowed roots (CONTIMG_ALLOWED_ROOTS)."
                    )
            except RuntimeError:
                # No home directory available
                raise OSError(
                    f"Could not write to configured {description} '{path}'. "
                    "No home directory available and strict path mode is enabled (CONTIMG_STRICT_PATHS=1). "
                    "Set the appropriate CONTIMG_* directory environment variables to a writable location "
                    "within allowed roots (CONTIMG_ALLOWED_ROOTS)."
                )
        else:
            # Non-strict mode: try user home first
            try:
                fallback_root = Path.home() / ".dsa110-contimg"
            except RuntimeError:
                # Fallback to system temp directory if no home (e.g. some container envs)
                # Use tempfile.gettempdir() which respects TMPDIR environment variable
                try:
                    user = getpass.getuser()
                except Exception:
                    user = "unknown"
                fallback_root = Path(tempfile.gettempdir()) / f"dsa110-contimg-{user}"

    # Construct new path preserving the original structure relative to root if possible
    # but here we just want a safe place.
    # For simplicity, we just append the last component or create a specific structure.
    # But usually we are resolving a BASE directory.

    new_path = fallback_root / path.name

    if _is_forbidden_target(new_path) or not _is_allowed_target(new_path):
        raise OSError(
            f"Could not write to configured {description} '{path}'. "
            f"Computed fallback '{new_path}' is not permitted by path policy."
        )

    # Attempt to create fallback
    try:
        new_path.mkdir(parents=True, exist_ok=True)
        if os.access(new_path, os.W_OK):
            # Only warn if explicitly requested (warn_on_fallback=True)
            # In container environments, falling back to home/.dsa110-contimg is often expected behavior
            log_level = logging.WARNING if warn_on_fallback else logging.INFO
            logger.log(
                log_level,
                "Could not write to configured %s '%s'. Falling back to writable location: '%s'",
                description,
                path,
                new_path,
            )
            return new_path
    except (PermissionError, OSError):
        pass

    # Last resort: random temp dir (only if not in strict mode)
    # In strict mode, we should have already raised an error above
    if strict_paths:
        raise OSError(
            f"Could not write to configured {description} '{path}' or fallback '{new_path}'. "
            "Strict path mode is enabled (CONTIMG_STRICT_PATHS=1). "
            "Set the appropriate CONTIMG_* directory environment variables to a writable location "
            "within allowed roots (CONTIMG_ALLOWED_ROOTS)."
        )

    try:
        new_path = Path(tempfile.mkdtemp(prefix=f"dsa110-{path.name}-"))
        # Only warn if explicitly requested (warn_on_fallback=True)
        # In container environments, falling back to temp/home is often expected behavior
        if warn_on_fallback:
            logger.warning(
                "Could not write to configured %s '%s' or standard fallback. "
                "Using temporary directory: '%s'. "
                "To prevent this fallback, set CONTIMG_STRICT_PATHS=1.",
                description,
                path,
                new_path,
            )
        else:
            logger.info(
                "Using temporary directory for %s: '%s'. "
                "To prevent this fallback, set CONTIMG_STRICT_PATHS=1.",
                description,
                new_path,
            )
        return new_path
    except Exception as e:
        # If we can't even make a temp dir, we re-raise the original issue or a new one
        raise OSError(
            f"Could not find any writable location for {description}. Tried '{path}' and '{new_path}'"
        ) from e


# Base paths
_paths = resolve_paths()

STAGE_BASE = _resolve_writable_path(
    str(_paths.staging_dir),
    description="staging directory",
    warn_on_fallback=False,
)

_env_data = os.getenv("CONTIMG_DATA_BASE")
if _env_data:
    logging.getLogger(__name__).warning("CONTIMG_DATA_BASE is deprecated; use CONTIMG_BASE_DIR instead")
DATA_BASE = _resolve_writable_path(
    _env_data or str(_paths.base_dir),
    description="data directory",
    warn_on_fallback=bool(_env_data),
)

PRODUCTS_BASE = DATA_BASE / "products"  # Published products
STATE_BASE = DATA_BASE / "state"  # Pipeline state (databases)

# Auto-publish configuration
AUTO_PUBLISH_ENABLED_BY_DEFAULT = True
AUTO_PUBLISH_DELAY_SECONDS = 0  # Delay before auto-publish (0 = immediate)

# Stage-based organization (new structure – now default)
DATA_TYPES: dict[str, dict[str, Any]] = {
    "raw_ms": {
        "staging_dir": STAGE_BASE / "raw" / "ms",
        "published_dir": None,  # Raw MS not published
        "auto_publish_criteria": {
            "qa_required": False,
            "validation_required": False,
        },
    },
    "calibrated_ms": {
        "staging_dir": STAGE_BASE / "calibrated" / "ms",
        "published_dir": PRODUCTS_BASE / "ms",  # Optional: publish calibrated MS
        "auto_publish_criteria": {
            "qa_required": True,
            "qa_status": "passed",
            "validation_required": True,
        },
    },
    "calibration_table": {
        "staging_dir": STAGE_BASE / "calibrated" / "tables",
        "published_dir": PRODUCTS_BASE / "caltables",
        "auto_publish_criteria": {
            "qa_required": True,
            "qa_status": "passed",
            "validation_required": True,
        },
    },
    "image": {
        "staging_dir": STAGE_BASE / "images",
        "published_dir": PRODUCTS_BASE / "images",  # Optional
        "auto_publish_criteria": {
            "qa_required": True,
            "qa_status": "passed",
            "validation_required": True,
        },
    },
    "mosaic": {
        "staging_dir": STAGE_BASE / "mosaics",
        "published_dir": PRODUCTS_BASE / "mosaics",
        "auto_publish_criteria": {
            "qa_required": True,
            "qa_status": "passed",
            "validation_required": True,
        },
    },
    "catalog": {
        "staging_dir": STAGE_BASE / "products" / "catalogs",
        "published_dir": PRODUCTS_BASE / "catalogs",
        "auto_publish_criteria": {
            "qa_required": False,
            "validation_required": True,
        },
    },
    "qa": {
        "staging_dir": STAGE_BASE / "products" / "qa",
        "published_dir": PRODUCTS_BASE / "qa",
        "subdirs": ["cal_qa", "ms_qa", "image_qa"],
        "auto_publish_criteria": {
            "qa_required": False,
            "validation_required": False,
        },
    },
    "metadata": {
        "staging_dir": STAGE_BASE / "products" / "metadata",
        "published_dir": PRODUCTS_BASE / "metadata",
        "subdirs": [
            "pipe_meta",
            "cal_meta",
            "ms_meta",
            "catalog_meta",
            "image_meta",
            "mosaic_meta",
        ],
        "auto_publish_criteria": {
            "qa_required": False,
            "validation_required": False,
        },
    },
    # Legacy types for backward compatibility
    "ms": {
        "staging_dir": STAGE_BASE / "raw" / "ms",
        "published_dir": PRODUCTS_BASE / "ms",
        "auto_publish_criteria": {
            "qa_required": False,
            "validation_required": True,
        },
    },
    "calib_ms": {
        "staging_dir": STAGE_BASE / "calibrated" / "ms",
        "published_dir": PRODUCTS_BASE / "ms",
        "auto_publish_criteria": {
            "qa_required": True,
            "qa_status": "passed",
            "validation_required": True,
        },
    },
    "caltable": {
        "staging_dir": STAGE_BASE / "calibrated" / "tables",
        "published_dir": PRODUCTS_BASE / "caltables",
        "auto_publish_criteria": {
            "qa_required": True,
            "qa_status": "passed",
            "validation_required": True,
        },
    },
}


def get_staging_dir(data_type: str) -> Path:
    """Get staging directory for a data type.

    Parameters
    ----------
    """
    if data_type not in DATA_TYPES:
        raise ValueError(f"Unknown data type: {data_type}")
    return DATA_TYPES[data_type]["staging_dir"]


def get_published_dir(data_type: str) -> Path | None:
    """Get published directory for a data type.

    Parameters
    ----------
    """
    if data_type not in DATA_TYPES:
        raise ValueError(f"Unknown data type: {data_type}")
    published_dir = DATA_TYPES[data_type].get("published_dir")
    return Path(published_dir) if published_dir else None


def get_auto_publish_criteria(data_type: str) -> dict[str, Any]:
    """Get auto-publish criteria for a data type.

    Parameters
    ----------
    """
    if data_type not in DATA_TYPES:
        raise ValueError(f"Unknown data type: {data_type}")
    return DATA_TYPES[data_type].get("auto_publish_criteria", {})


# New structure helper functions
def get_raw_ms_dir() -> Path:
    """Get directory for raw (uncalibrated) MS files."""
    return STAGE_BASE / "raw" / "ms"


def get_calibrated_ms_dir() -> Path:
    """Get directory for calibrated MS files."""
    return STAGE_BASE / "calibrated" / "ms"


def get_calibration_tables_dir() -> Path:
    """Get directory for calibration tables."""
    return STAGE_BASE / "calibrated" / "tables"


def get_groups_dir() -> Path:
    """Get directory for group definitions."""
    return STAGE_BASE / "raw" / "groups"


def get_workspace_dir() -> Path:
    """Get workspace directory for active processing."""
    return STAGE_BASE / "workspace"


def get_workspace_active_dir(stage: str | None = None) -> Path:
    """Get directory for active processing workspace.

    Parameters
    ----------
    stage :
        Optional stage name (e.g., 'conversion', 'calibration', 'imaging', 'mosaicking')
    stage: Optional[str] :
         (Default value = None)

    Returns
    -------
        Path to active workspace directory (or stage-specific subdirectory)

    """
    base = STAGE_BASE / "workspace" / "active"
    if stage:
        return base / stage
    return base


def get_workspace_failed_dir() -> Path:
    """Get directory for failed processing attempts."""
    return STAGE_BASE / "workspace" / "failed"


def get_products_dir() -> Path:
    """Get directory for validated products ready to publish."""
    return STAGE_BASE / "products"


def get_logs_dir(category: str = "misc") -> Path:
    """Get directory for logs.

    Parameters
    ----------
    category : str, optional
        Log category (e.g., 'calibration', 'imaging'), by default "misc"
    """
    return STAGE_BASE / "logs" / category


def get_debug_plots_dir() -> Path:
    """Get directory for debug plots."""
    return STAGE_BASE / "debug"


def get_reports_dir() -> Path:
    """Get directory for reports."""
    return STAGE_BASE / "reports"


def get_test_dir() -> Path:
    """Get directory for test outputs."""
    return STAGE_BASE / "test"


def get_pid_dir() -> Path:
    """Get directory for PID files."""
    # Use _resolve_writable_path to ensure PID directory is writable
    pid_root = os.environ.get("CONTIMG_PID_DIR") or os.environ.get("DSA_PID_ROOT")
    if pid_root:
        default_pid_root = Path(pid_root)
    else:
        # Use CONTIMG_TEMP_DIR if available, otherwise fallback to system temp
        temp_dir = os.environ.get("CONTIMG_TEMP_DIR")
        if temp_dir:
            default_pid_root = Path(temp_dir) / "pids"
        else:
            default_pid_root = Path(tempfile.gettempdir()) / "pids"
            
    return _resolve_writable_path(str(default_pid_root), description="PID directory")


def ensure_staging_directories() -> None:
    """Ensure all staging directories exist."""
    directories = [
        get_raw_ms_dir(),
        get_calibrated_ms_dir(),
        get_calibration_tables_dir(),
        get_groups_dir(),
        get_workspace_dir(),
        get_workspace_active_dir(),
        get_workspace_failed_dir(),
        get_products_dir(),
        get_debug_plots_dir(),
        get_reports_dir(),
        get_test_dir(),
        STAGE_BASE / "logs",
        STAGE_BASE / "images",
        STAGE_BASE / "mosaics",
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    # Create subdirectories for raw MS
    (get_raw_ms_dir() / "science").mkdir(parents=True, exist_ok=True)
    (get_raw_ms_dir() / "calibrators").mkdir(parents=True, exist_ok=True)

    # Create subdirectories for calibrated MS
    (get_calibrated_ms_dir() / "science").mkdir(parents=True, exist_ok=True)
    (get_calibrated_ms_dir() / "calibrators").mkdir(parents=True, exist_ok=True)

    # Create workspace subdirectories
    for stage in ["conversion", "calibration", "imaging", "mosaicking"]:
        get_workspace_active_dir(stage).mkdir(parents=True, exist_ok=True)

    # Create thumbnails directory
    (STAGE_BASE / "thumbnails").mkdir(parents=True, exist_ok=True)

    # Create products subdirectories
    for subdir in ["catalogs"]:
        (get_products_dir() / subdir).mkdir(parents=True, exist_ok=True)

    # Create QA subdirectories
    for qa_subdir in ["cal_qa", "ms_qa", "image_qa"]:
        (get_products_dir() / "qa" / qa_subdir).mkdir(parents=True, exist_ok=True)

    # Create metadata subdirectories
    for meta_subdir in [
        "pipe_meta",
        "cal_meta",
        "ms_meta",
        "catalog_meta",
        "image_meta",
        "mosaic_meta",
    ]:
        (get_products_dir() / "metadata" / meta_subdir).mkdir(parents=True, exist_ok=True)


class PathValidationError(ValueError):
    """ """


def get_valid_staging_roots() -> list[Path]:
    """Get all valid root directories under STAGE_BASE.

    Returns
    -------
        List of valid staging root paths

    """
    return [
        STAGE_BASE / "raw",
        STAGE_BASE / "calibrated",
        STAGE_BASE / "images",
        STAGE_BASE / "mosaics",
        STAGE_BASE / "thumbnails",
        STAGE_BASE / "products",
        STAGE_BASE / "workspace",
    ]


def validate_staging_path(path: Path, data_type: str | None = None) -> bool:
    """Validate that a path is within expected staging structure.

    Parameters
    ----------
    path :
        Path to validate
    data_type :
        Optional data type to validate against specific directory

    Returns
    -------
        True if path is valid

    Raises
    ------
    PathValidationError
        If path is outside expected structure

    """
    path = Path(path).resolve()

    # Check if path is under STAGE_BASE
    try:
        path.relative_to(STAGE_BASE)
    except ValueError:
        raise PathValidationError(f"Path {path} is outside staging base {STAGE_BASE}") from None

    # If data_type specified, check against specific directory
    if data_type:
        if data_type not in DATA_TYPES:
            raise PathValidationError(f"Unknown data type: {data_type}")
        expected_dir = DATA_TYPES[data_type]["staging_dir"]
        try:
            path.relative_to(expected_dir)
        except ValueError:
            raise PathValidationError(
                f"Path {path} is not under expected directory {expected_dir} "
                f"for data type '{data_type}'"
            ) from None

    # Check if path is under a valid root
    valid_roots = get_valid_staging_roots()
    is_under_valid_root = any(_is_path_under(path, root) for root in valid_roots)

    if not is_under_valid_root:
        raise PathValidationError(
            f"Path {path} is not under any valid staging directory. "
            f"Valid roots: {[str(r) for r in valid_roots]}"
        )

    return True


def _is_path_under(path: Path, parent: Path) -> bool:
    """Check if path is under parent directory.

    Parameters
    ----------
    """
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def get_staging_status() -> dict:
    """Get status of staging directories.

    Returns
    -------
        Dictionary with directory status (exists, writable, file count)

    """
    status = {
        "stage_base": str(STAGE_BASE),
        "stage_base_exists": STAGE_BASE.exists(),
        "directories": {},
    }

    dirs_to_check = {
        "raw/ms/science": get_raw_ms_dir() / "science",
        "raw/ms/calibrators": get_raw_ms_dir() / "calibrators",
        "raw/groups": get_groups_dir(),
        "calibrated/ms/science": get_calibrated_ms_dir() / "science",
        "calibrated/ms/calibrators": get_calibrated_ms_dir() / "calibrators",
        "calibrated/tables": get_calibration_tables_dir(),
        "images": STAGE_BASE / "images",
        "mosaics": STAGE_BASE / "mosaics",
        "thumbnails": STAGE_BASE / "thumbnails",
        "products/catalogs": get_products_dir() / "catalogs",
        "products/qa": get_products_dir() / "qa",
        "products/metadata": get_products_dir() / "metadata",
        "workspace/active": get_workspace_active_dir(),
        "workspace/failed": get_workspace_failed_dir(),
    }

    for name, path in dirs_to_check.items():
        dir_status = {
            "path": str(path),
            "exists": path.exists(),
            "writable": os.access(path, os.W_OK) if path.exists() else False,
        }
        if path.exists():
            try:
                dir_status["file_count"] = sum(1 for _ in path.rglob("*") if _.is_file())
            except PermissionError:
                dir_status["file_count"] = -1
        status["directories"][name] = dir_status

    return status
