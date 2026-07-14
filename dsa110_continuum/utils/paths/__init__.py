"""Consolidated path utilities for DSA-110 continuum imaging pipeline.

This package provides all path resolution, validation, and utility functions
for the pipeline's multi-tier storage architecture.

Modules
-------
resolver : Path resolution and ResolvedPaths dataclass
config : Path configuration constants (CONTIMG_SCRATCH_DIR, etc.)
validation : Path validation, sanitization, and safety enforcement
utils : Higher-level MS path operations and tmpfs utilities
temporary : TempPaths class for temporary file management
"""

# Core path resolution (resolver.py)
from dsa110_continuum.utils.paths.resolver import (
    ResolvedPaths,
    get_repo_root,
    print_inventory,
    resolve_paths,
    resolve_paths_with_sources,
)

# Path configuration constants (config.py)
from dsa110_continuum.utils.paths.config import (
    CONTIMG_ARCHIVE_DIR,
    CONTIMG_BASE_DIR,
    CONTIMG_SCRATCH_DIR,  # Deprecated alias for CONTIMG_TMPFS_DIR
    CONTIMG_STAGING_DIR,
    CONTIMG_TMPFS_DIR,
)

# Path validation and safety (validation.py)
from dsa110_continuum.utils.paths.validation import (
    enforce_path_policy,
    get_safe_path,
    is_safe_path,
    sanitize_filename,
    validate_path,
)

# Temporary file management (re-export from temporary.py)
from dsa110_continuum.utils.paths.temporary import TempPaths

# Higher-level path utilities (utils.py)
from dsa110_continuum.utils.paths.utils import (
    archive_ms_to_hdd,
    calculate_path_hash,
    copy_ms_from_tmpfs,
    determine_ms_type,
    ensure_date_directory,
    ensure_ms_in_tmpfs,
    extract_date_from_filename,
    extract_date_from_path,
    get_default_figures_dir,
    get_group_definition_path,
    get_ms_output_path,
    get_workspace_path,
    guard_tmp_write,
    move_ms_to_calibrated,
    save_group_definition,
    warn_tmp_usage,
)

__all__ = [
    # resolver.py
    "ResolvedPaths",
    "resolve_paths",
    "resolve_paths_with_sources",
    "get_repo_root",
    "print_inventory",
    # config.py
    "CONTIMG_ARCHIVE_DIR",
    "CONTIMG_BASE_DIR",
    "CONTIMG_TMPFS_DIR",
    "CONTIMG_SCRATCH_DIR",  # Deprecated alias
    "CONTIMG_STAGING_DIR",
    # validation.py
    "validate_path",
    "sanitize_filename",
    "get_safe_path",
    "is_safe_path",
    "enforce_path_policy",
    # temp_paths.py (re-export)
    "TempPaths",
    # utils.py
    "get_default_figures_dir",
    "warn_tmp_usage",
    "guard_tmp_write",
    "get_ms_output_path",
    "move_ms_to_calibrated",
    "get_workspace_path",
    "ensure_date_directory",
    "extract_date_from_filename",
    "extract_date_from_path",
    "determine_ms_type",
    "get_group_definition_path",
    "save_group_definition",
    "copy_ms_from_tmpfs",
    "ensure_ms_in_tmpfs",
    "calculate_path_hash",
    "archive_ms_to_hdd",
]
