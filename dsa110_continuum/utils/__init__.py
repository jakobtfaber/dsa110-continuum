# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
# This file initializes the utils module.

"""
Utilities for the DSA-110 Continuum Imaging Pipeline.

This module provides shared utilities used across pipeline stages:
- Custom exception classes for structured error handling
- Centralized logging configuration
- Constants for DSA-110 telescope parameters
- Fast UVH5 metadata reading utilities
- Antenna position utilities
"""

# Import exceptions for convenient access
# Import constants

# NOTE: CASA utilities (casa_log_environment, get_casa_task, setup_casa_log_directory)
# are imported lazily via __getattr__ to avoid triggering CASA imports in contexts
# that don't need them (e.g., dagster-adapter which only needs the GraphQL schema)

from dsa110_continuum.utils.constants import (
    DSA110_ALT,
    DSA110_LAT,
    DSA110_LATITUDE,
    DSA110_LOCATION,
    DSA110_LON,
    DSA110_LONGITUDE,
)

# Import timing decorators
from dsa110_continuum.utils.decorators import (
    timed,
    timed_context,
    timed_debug,
    timed_verbose,
)
from dsa110_continuum.utils.exceptions import (
    # Calibration errors
    CalibrationError,
    CalibrationTableNotFoundError,
    CalibratorNotFoundError,
    # Conversion errors
    ConversionError,
    DatabaseConnectionError,
    # Database errors
    DatabaseError,
    DatabaseLockError,
    DatabaseMigrationError,
    ImageNotFoundError,
    # Imaging errors
    ImagingError,
    IncompleteSubbandGroupError,
    InvalidPathError,
    MissingParameterError,
    MSWriteError,
    # Base exception
    PipelineError,
    # Queue errors
    QueueError,
    QueueStateTransitionError,
    # Subband errors
    SubbandGroupingError,
    UVH5ReadError,
    # Validation errors
    ValidationError,
    is_recoverable,
    # Helpers
    wrap_exception,
)

# Import fast metadata utilities
from dsa110_continuum.utils.fast_meta import (
    FastMeta,
    get_uvh5_freqs,
    get_uvh5_mid_mjd,
    get_uvh5_times,
    peek_uvh5_phase_and_midtime,
)

# Import logging utilities
from dsa110_continuum.utils.logging import (
    get_logger,
    log_context,
    log_exception,
    setup_logging,
)

# Import run isolation utilities
from dsa110_continuum.utils.run_isolation import (
    prepare_temp_environment,
)

# Import YAML loader utilities
from dsa110_continuum.utils.yaml_loader import (
    expand_env_recursive,
    expand_env_vars,
    load_yaml_with_env,
    safe_load_yaml_with_env,
)

# Import environment variable utilities
from dsa110_continuum.utils.env_utils import (
    EnvVarError,
    get_env_bool,
    get_env_float,
    get_env_int,
    get_env_list,
    get_env_path,
    get_required_env,
)

__all__ = [
    # Exceptions
    "PipelineError",
    "SubbandGroupingError",
    "IncompleteSubbandGroupError",
    "ConversionError",
    "UVH5ReadError",
    "MSWriteError",
    "DatabaseError",
    "DatabaseMigrationError",
    "DatabaseConnectionError",
    "DatabaseLockError",
    "QueueError",
    "QueueStateTransitionError",
    "CalibrationError",
    "CalibrationTableNotFoundError",
    "CalibratorNotFoundError",
    "ImagingError",
    "ImageNotFoundError",
    "ValidationError",
    "MissingParameterError",
    "InvalidPathError",
    "wrap_exception",
    "is_recoverable",
    # Logging
    "setup_logging",
    "log_context",
    "get_logger",
    "log_exception",
    # Constants
    "DSA110_LOCATION",
    "DSA110_LATITUDE",
    "DSA110_LONGITUDE",
    "DSA110_LAT",
    "DSA110_LON",
    "DSA110_ALT",
    # Fast metadata
    "FastMeta",
    "get_uvh5_times",
    "get_uvh5_mid_mjd",
    "get_uvh5_freqs",
    "peek_uvh5_phase_and_midtime",
    # Timing decorators
    "timed",
    "timed_context",
    "timed_debug",
    "timed_verbose",
    # Process management
    "ProcessGuard",
    "cleanup_orphaned_workers",
    # Path utilities
    "guard_tmp_write",
    "warn_tmp_usage",
    # CASA utilities
    "casa_log_environment",
    "get_casa_task",
    "setup_casa_log_directory",
    # Run isolation
    "prepare_temp_environment",
    # Temp paths
    "TempPaths",
    # YAML loader utilities
    "load_yaml_with_env",
    "safe_load_yaml_with_env",
    "expand_env_vars",
    "expand_env_recursive",
    # Environment variable utilities
    "EnvVarError",
    "get_env_bool",
    "get_env_int",
    "get_env_float",
    "get_env_path",
    "get_env_list",
    "get_required_env",
]


# =============================================================================
# Lazy imports for CASA utilities
# =============================================================================
# These are imported lazily to avoid triggering CASA imports in contexts that
# don't need them (e.g., dagster-adapter which only needs the GraphQL schema).

_CASA_UTILS = {
    "casa_log_environment",
    "get_casa_task",
    "setup_casa_log_directory",
}


def __getattr__(name: str):
    """Lazy import for CASA utilities to avoid unnecessary CASA initialization."""
    if name in _CASA_UTILS:
        from dsa110_continuum.utils import casa_init

        return getattr(casa_init, name)
    if name == "TempPaths":
        from dsa110_continuum.utils.paths.temporary import TempPaths

        return TempPaths
    # path_utils / process_guard were not vendored (no consumers in this package)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
