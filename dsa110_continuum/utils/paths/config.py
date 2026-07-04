# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Path configuration constants for DSA-110 continuum imaging pipeline.

This module provides commonly-used path constants that are resolved at import time
from environment variables. For more complex path resolution, use the resolver module.
"""

from __future__ import annotations

import os
from pathlib import Path

from dsa110_continuum.utils.paths.resolver import resolve_paths

# Resolve paths once at import time for constant access
_paths = resolve_paths()

# Public constants
CONTIMG_BASE_DIR: Path = _paths.base_dir
CONTIMG_TMPFS_DIR: Path = _paths.tmpfs_dir
CONTIMG_STAGING_DIR: Path = _paths.staging_dir

# Archive directory on HDD for long-term MS storage
# Default: /data/stage/dsa110-contimg/ms/ (13 TB HDD)
CONTIMG_ARCHIVE_DIR: Path = Path(
    os.environ.get("CONTIMG_ARCHIVE_DIR", "/data/stage/dsa110-contimg/ms")
)

# Deprecated alias - use CONTIMG_TMPFS_DIR instead
CONTIMG_SCRATCH_DIR: Path = CONTIMG_TMPFS_DIR
