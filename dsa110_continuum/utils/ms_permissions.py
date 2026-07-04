# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Helpers for ensuring Measurement Set paths are writable."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

LOG = logging.getLogger(__name__)


def _chmod_user_writable(path: Path, add_exec: bool = False) -> None:
    if os.access(path, os.W_OK):
        return
    try:
        st = path.stat()
    except OSError as exc:
        raise RuntimeError(f"Cannot stat path for permissions: {path}: {exc}") from exc

    if st.st_uid != os.getuid():
        raise RuntimeError(
            f"Path is not writable and not owned by current user: {path}. "
            "Ensure the MS was created by the same user or clean stale root-owned "
            "tmpfs staging directories."
        )

    new_mode = st.st_mode | stat.S_IWUSR
    if add_exec and path.is_dir():
        new_mode |= stat.S_IXUSR
    try:
        os.chmod(path, new_mode)
        LOG.info("Adjusted path permissions to be user-writable: %s", path)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to make path writable: {path}: {exc}. "
            "Check filesystem permissions or disable tmpfs staging."
        ) from exc


def ensure_dir_writable(path: Path) -> None:
    """Ensure a directory exists and is writable by the current user."""
    dir_path = Path(path)
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as exc:
        raise RuntimeError(f"Cannot create directory {dir_path}: {exc}") from exc

    _chmod_user_writable(dir_path, add_exec=True)


def ensure_ms_writable(ms_path: str) -> None:
    """Ensure MS and main tables are writable by the current user."""
    ms_root = Path(ms_path)
    if not ms_root.exists():
        raise RuntimeError(f"MS path does not exist: {ms_root}")

    LOG.info("MS preflight: ensure writable: %s", ms_root)

    _chmod_user_writable(ms_root, add_exec=True)

    table_paths = {ms_root / "table.dat"}
    try:
        for table_path in ms_root.rglob("table.dat"):
            table_paths.add(table_path)
    except OSError:
        pass

    for table_path in sorted(table_paths):
        if table_path.exists():
            _chmod_user_writable(table_path, add_exec=False)
