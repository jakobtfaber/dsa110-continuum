# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
MS (Measurement Set) access serialization using file locking.

This module provides a context manager to serialize access to Measurement Sets,
preventing CASA table lock conflicts when multiple processes try to access the
same MS concurrently.
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOG = logging.getLogger(__name__)


@contextmanager
def ms_lock(ms_path: str, timeout: float | None = None, poll_interval: float = 0.1):
    """Context manager to serialize access to a Measurement Set.

        Uses file locking (fcntl.flock) to ensure only one process accesses the MS
        at a time. This prevents CASA table lock conflicts when multiple processes
        try to access the same MS concurrently.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    timeout : Optional[float], optional
        Maximum time to wait for lock (seconds).
        If None, waits indefinitely. (Default is None)
    poll_interval : float, optional
        Time between lock attempts (seconds). (Default is 0.1)

    Yields
    ------
        None
        Lock is held during context

        Example
    -------
        >>> with ms_lock("/path/to/data.ms"):
        ...     # Only one process can execute this block at a time
        ...     image_ms(...)
    """
    # Create lock file path (in same directory as MS)
    ms_path_obj = Path(ms_path)
    lock_file_path = ms_path_obj.parent / f"{ms_path_obj.name}.lock"

    # Ensure lock file exists
    lock_file_path.touch(exist_ok=True)

    lock_acquired = False
    lock_fd = None

    try:
        # Open lock file for reading/writing
        lock_fd = os.open(str(lock_file_path), os.O_RDWR)

        start_time = time.time()
        while True:
            try:
                # Try to acquire exclusive lock (non-blocking)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_acquired = True
                LOG.debug(f"Acquired MS lock: {lock_file_path}")
                break
            except BlockingIOError:
                # Lock is held by another process
                if timeout is not None:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        msg = f"Timeout waiting for MS lock after {timeout}s: {lock_file_path}"
                        raise TimeoutError(msg)
                    LOG.debug(
                        f"Waiting for MS lock "
                        f"(elapsed: {elapsed:.1f}s/{timeout}s): "
                        f"{lock_file_path}"
                    )
                else:
                    LOG.debug(f"Waiting for MS lock: {lock_file_path}")

                time.sleep(poll_interval)

        # Lock acquired, yield control
        yield

    finally:
        # Release lock
        if lock_acquired and lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                LOG.debug(f"Released MS lock: {lock_file_path}")
            except Exception as e:
                LOG.warning(f"Error releasing MS lock: {e}")

        # Close file descriptor
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except Exception as e:
                LOG.warning(f"Error closing lock file descriptor: {e}")


def cleanup_stale_locks(ms_path: str, max_age_seconds: float = 3600.0) -> bool:
    """Clean up stale lock files that may have been left by crashed processes.

        Checks if lock file exists and is older than max_age_seconds. If so, removes it.
        This is a safety mechanism for lock files left behind by crashed processes.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    max_age_seconds : float, optional
        Maximum age of lock file before considering it stale. (Default is 3600.0)

    Returns
    -------
        None
    """
    ms_path_obj = Path(ms_path)
    lock_file_path = ms_path_obj.parent / f"{ms_path_obj.name}.lock"

    if not lock_file_path.exists():
        return False

    # Check lock file age
    lock_age = time.time() - lock_file_path.stat().st_mtime

    if lock_age > max_age_seconds:
        LOG.warning(
            f"Removing stale MS lock file "
            f"(age: {lock_age:.0f}s > {max_age_seconds}s): "
            f"{lock_file_path}"
        )
        try:
            lock_file_path.unlink()
            return True
        except Exception as e:
            LOG.error(f"Failed to remove stale lock file: {e}")
            return False

    return False
