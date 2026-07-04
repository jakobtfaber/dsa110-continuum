# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Optimized HDF5 I/O utilities for DSA-110 pipeline.

This module provides optimized h5py file access with proper chunk cache settings
to avoid catastrophic performance degradation when reading chunked/compressed data.

Performance Background (from HDF Group documentation):
    - Default h5py chunk cache is 1MB
    - If chunks are larger than cache, each read causes full chunk decompression
    - This can cause 1000x slowdowns for repeated access patterns
    - Solution: Set rdcc_nbytes to hold at least one full chunk

DSA-110 UVH5 File Characteristics:
    - Typical visibility chunk sizes: 2-4 MB
    - Recommended cache size: 16 MB (holds multiple chunks)
    - For metadata-only reads: cache can be disabled (0 bytes)

Two Optimization Approaches:
    1. Global h5py Configuration (RECOMMENDED):
       Call configure_h5py_cache_defaults() once at application startup.
       This monkey-patches h5py.File to use optimized cache settings for ALL
       HDF5 operations, including third-party libraries like pyuvdata.

    2. Explicit Context Managers:
       Use the provided context managers (open_uvh5, open_uvh5_metadata, etc.)
       for fine-grained control over cache settings per file.

Usage - Global Configuration (recommended):
    # At application entry point (before any h5py imports):
    from dsa110_continuum.utils.hdf5_io import configure_h5py_cache_defaults
    configure_h5py_cache_defaults()  # Patches h5py globally

    # All subsequent h5py.File() calls use 16MB cache automatically
    # This includes pyuvdata.UVData.read() and other library code

Usage - Context Managers (explicit control):
    # For repeated reads (hot path):
    with open_uvh5(path) as f:
        data = f['visdata'][:]

    # For metadata-only reads (cold path):
    with open_uvh5_metadata(path) as f:
        times = f['time_array'][:]

    # For single-pass bulk reads:
    with open_uvh5_streaming(path) as f:
        data = f['visdata'][:]  # Read all at once

Cache Status Checking:
    from dsa110_continuum.utils.hdf5_io import get_h5py_cache_info
    info = get_h5py_cache_info()
    print(f"Cache enabled: {info['patched']}, size: {info['rdcc_nbytes']/1e6:.1f}MB")

Reference:
    https://support.hdfgroup.org/documentation/hdf5/latest/improve_compressed_perf.html
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    import h5py

logger = logging.getLogger(__name__)

# Lazy-import settings to avoid circular dependency
_settings = None


def _get_settings():
    """Lazy load settings to avoid circular import."""
    global _settings
    if _settings is None:
        from dsa110_continuum.unified_config import settings

        _settings = settings
    return _settings


# Cache size constants (in bytes) - use settings when available, fallback to defaults
def _get_cache_size_default() -> int:
    try:
        return _get_settings().hdf5.cache_size_bytes
    except Exception:
        return 16 * 1024 * 1024  # 16 MB fallback


def _get_cache_size_large() -> int:
    try:
        return _get_settings().hdf5.cache_size_large_bytes
    except Exception:
        return 64 * 1024 * 1024  # 64 MB fallback


def _get_cache_size_metadata() -> int:
    try:
        return _get_settings().hdf5.cache_size_metadata_bytes
    except Exception:
        return 1 * 1024 * 1024  # 1 MB fallback


def _get_cache_slots() -> int:
    try:
        return _get_settings().hdf5.cache_slots
    except Exception:
        return 1009  # fallback


# Backward compatibility constants
HDF5_CACHE_SIZE_DEFAULT = _get_cache_size_default()
HDF5_CACHE_SIZE_LARGE = _get_cache_size_large()
HDF5_CACHE_SIZE_METADATA = _get_cache_size_metadata()
HDF5_CACHE_SIZE_STREAMING = 0  # Always 0 for streaming
HDF5_CACHE_SLOTS = _get_cache_slots()

# Track whether global defaults have been configured
_h5py_defaults_configured = False
_original_h5py_file_init = None


def configure_h5py_cache_defaults(
    cache_size: int | None = None,
    cache_slots: int | None = None,
) -> bool:
    """Configure h5py global default chunk cache settings via monkey-patching.

        This patches h5py.File.__init__ to inject default cache parameters for ALL
        h5py.File() calls in the process, including those made by third-party
        libraries like pyuvdata.

    IMPORTANT: Call this BEFORE importing pyuvdata or any other library that
        uses h5py, to ensure the patch is applied before their module-level imports.

    Parameters
    ----------
    cache_size : Optional[int]
        Cache size in bytes (default: from settings.hdf5.cache_size_bytes)
        (Default value = None)
    cache_slots : Optional[int]
        Number of hash slots (default: from settings.hdf5.cache_slots)
        (Default value = None)

    """
    global _h5py_defaults_configured, _original_h5py_file_init

    if _h5py_defaults_configured:
        logger.debug("h5py cache defaults already configured, skipping")
        return False

    # Use settings defaults if not provided
    if cache_size is None:
        cache_size = _get_cache_size_default()
    if cache_slots is None:
        cache_slots = _get_cache_slots()

    try:
        import h5py

        # Save original __init__
        _original_h5py_file_init = h5py.File.__init__

        # Create wrapper that injects cache defaults
        def _patched_file_init(
            self,
            name,
            mode="r",
            driver=None,
            libver=None,
            userblock_size=None,
            swmr=False,
            rdcc_nslots=None,
            rdcc_nbytes=None,
            rdcc_w0=None,
            track_order=None,
            fs_strategy=None,
            fs_persist=False,
            fs_threshold=1,
            fs_page_size=None,
            page_buf_size=None,
            min_meta_keep=0,
            min_raw_keep=0,
            locking=None,
            alignment_threshold=1,
            alignment_interval=1,
            meta_block_size=None,
            **kwds,
        ):
            # Inject defaults if not explicitly provided
            if rdcc_nbytes is None:
                rdcc_nbytes = cache_size
            if rdcc_nslots is None:
                rdcc_nslots = cache_slots

            return _original_h5py_file_init(
                self,
                name,
                mode=mode,
                driver=driver,
                libver=libver,
                userblock_size=userblock_size,
                swmr=swmr,
                rdcc_nslots=rdcc_nslots,
                rdcc_nbytes=rdcc_nbytes,
                rdcc_w0=rdcc_w0,
                track_order=track_order,
                fs_strategy=fs_strategy,
                fs_persist=fs_persist,
                fs_threshold=fs_threshold,
                fs_page_size=fs_page_size,
                page_buf_size=page_buf_size,
                min_meta_keep=min_meta_keep,
                min_raw_keep=min_raw_keep,
                locking=locking,
                alignment_threshold=alignment_threshold,
                alignment_interval=alignment_interval,
                meta_block_size=meta_block_size,
                **kwds,
            )

        # Apply patch
        h5py.File.__init__ = _patched_file_init
        _h5py_defaults_configured = True

        logger.info(
            f"Configured h5py cache defaults via monkey-patch: "
            f"rdcc_nbytes={cache_size / (1024 * 1024):.1f}MB, "
            f"rdcc_nslots={cache_slots}"
        )
        return True

    except Exception as e:
        logger.warning(f"Failed to configure h5py cache defaults: {e}")
        return False


def get_h5py_cache_info() -> dict:
    """Get current h5py cache configuration."""
    return {
        "default_rdcc_nbytes": HDF5_CACHE_SIZE_DEFAULT,
        "default_rdcc_nslots": HDF5_CACHE_SLOTS,
        "default_rdcc_nbytes_mb": HDF5_CACHE_SIZE_DEFAULT / (1024 * 1024),
        "configured_by_pipeline": _h5py_defaults_configured,
        "patch_applied": _original_h5py_file_init is not None,
    }


@contextmanager
def open_uvh5(
    path: str | Path,
    mode: str = "r",
    cache_size: int = HDF5_CACHE_SIZE_DEFAULT,
    cache_slots: int = HDF5_CACHE_SLOTS,
    use_fuse_lock: bool = False,
    lock_timeout: float = 5.0,
) -> Iterator[h5py.File]:
    """Open UVH5/HDF5 file with optimized chunk cache settings.

        This is the recommended method for opening HDF5 files in the pipeline.
        Uses a 16 MB chunk cache by default, which prevents repeated chunk
        decompression when accessing chunked datasets.

    Parameters
    ----------
    path : str or Path
        Path to HDF5 file
    mode : str
        File mode ('r', 'r+', 'w', 'w-', 'a')
    cache_size : int
        Chunk cache size in bytes (default: 16 MB)
    cache_slots : int
        Number of hash table slots (default: 1009)
    use_fuse_lock : bool
        If True, acquire a read lock to prevent races with file moves on FUSE mounts (default: False)
    lock_timeout : float
        Timeout for lock acquisition in seconds (default: 5.0)

    Yields
    ------
        h5py.File
        h5py.File object with optimized settings

    Returns
    -------
        None

    Examples
    --------
        >>> with open_uvh5("/data/file.hdf5") as f:
        ...     times = f['time_array'][:]
        ...     data = f['visdata'][:]
    """
    from contextlib import nullcontext

    import h5py

    # Acquire FUSE-aware read lock if requested (only for read modes)
    lock_context = nullcontext()
    if use_fuse_lock and mode in ("r", "r+"):
        try:
            from dsa110_continuum.utils.fuse_lock import get_fuse_lock_manager

            lock_mgr = get_fuse_lock_manager()
            lock_context = lock_mgr.read_lock(str(path), timeout=lock_timeout)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Could not acquire FUSE read lock for {path}: {e}")

    with lock_context:
        # rdcc_nbytes: raw data chunk cache size in bytes
        # rdcc_nslots: number of chunk slots in cache hash table
        # rdcc_w0: preemption policy (0.0 = LRU, 1.0 = evict fully read chunks)
        with h5py.File(
            path,
            mode,
            rdcc_nbytes=cache_size,
            rdcc_nslots=cache_slots,
            rdcc_w0=0.75,  # Balanced preemption
        ) as f:
            yield f


@contextmanager
def open_uvh5_metadata(
    path: str | Path,
    cache_size: int = HDF5_CACHE_SIZE_METADATA,
) -> Iterator[h5py.File]:
    """Open UVH5/HDF5 file for metadata-only access.

        Uses a smaller cache (1 MB) since metadata datasets are typically small.
        Suitable for operations that only read headers, time arrays, etc.

    Parameters
    ----------
    path : str or Path
        Path to HDF5 file
    cache_size : int
        Chunk cache size in bytes (default: 1 MB)

    Yields
    ------
        h5py.File
        h5py.File object

    Returns
    -------
        None

    Examples
    --------
        >>> with open_uvh5_metadata("/data/file.hdf5") as f:
        ...     times = f['time_array'][:]
        ...     dec = f['Header/extra_keywords/phase_center_dec'][()]
    """
    import h5py

    with h5py.File(
        path,
        "r",
        rdcc_nbytes=cache_size,
        rdcc_nslots=HDF5_CACHE_SLOTS,
    ) as f:
        yield f


@contextmanager
def open_uvh5_streaming(
    path: str | Path,
    mode: str = "r",
) -> Iterator[h5py.File]:
    """Open UVH5/HDF5 file for single-pass streaming reads.

        Disables chunk caching entirely since data is read only once.
        This saves memory and is appropriate for bulk data transfers
        where the same chunk is never accessed twice.

    Parameters
    ----------
    path : str or Path
        Path to HDF5 file
    mode : str
        File mode ('r', 'r+', 'w', 'w-', 'a')

    Yields
    ------
        h5py.File
        h5py.File object with caching disabled

    Returns
    -------
        None

    Examples
    --------
        >>> with open_uvh5_streaming("/data/file.hdf5") as f:
        ...     all_data = f['visdata'][:]  # Read entire dataset at once
    """
    import h5py

    with h5py.File(
        path,
        mode,
        rdcc_nbytes=HDF5_CACHE_SIZE_STREAMING,  # Disable cache
        rdcc_nslots=1,  # Minimal slots
    ) as f:
        yield f


@contextmanager
def open_uvh5_large_cache(
    path: str | Path,
    mode: str = "r",
    cache_size: int = HDF5_CACHE_SIZE_LARGE,
) -> Iterator[h5py.File]:
    """Open UVH5/HDF5 file with large chunk cache for intensive I/O.

        Uses a 64 MB cache for operations that repeatedly access multiple
        chunks, such as downsampling or reordering data.

    Parameters
    ----------
    path : str or Path
        Path to HDF5 file
    mode : str
        File mode ('r', 'r+', 'w', 'w-', 'a')
    cache_size : int
        Chunk cache size in bytes (default: 64 MB)

    Yields
    ------
        h5py.File
        h5py.File object with large cache

    Returns
    -------
        None

    Examples
    --------
        >>> with open_uvh5_large_cache("/data/file.hdf5") as f:
        ...     # Intensive random access pattern
        ...     for i in range(1000):
        ...         chunk = f['visdata'][i*100:(i+1)*100]
    """
    import h5py

    with h5py.File(
        path,
        mode,
        rdcc_nbytes=cache_size,
        rdcc_nslots=HDF5_CACHE_SLOTS * 2,  # More slots for large cache
        rdcc_w0=0.5,  # More aggressive eviction
    ) as f:
        yield f


@contextmanager
def open_uvh5_mmap(
    path: str | Path,
    preload: bool = False,
    use_fuse_lock: bool = True,
    lock_timeout: float = 5.0,
) -> Iterator[h5py.File]:
    """Open UVH5/HDF5 file using memory-mapped I/O.

        OPTIMIZATION 2: Uses the 'core' driver to memory-map the entire file,
        avoiding double-buffering overhead. This is particularly efficient for:
        - Files that fit in available RAM
        - Sequential reads of the entire file
        - When chunk caching overhead is undesirable

        RACE CONDITION FIX (Issue #6):
        On FUSE filesystems, if this file is moved/deleted while memory-mapped,
        FUSE creates a .fuse_hidden* file. This function optionally acquires a
        FUSE-aware read lock to coordinate with file move operations.

    Parameters
    ----------
    path : str or Path
        Path to HDF5 file
    preload : bool
        If True, preload entire file into memory (faster access, higher initial latency). If False, load on demand.
    use_fuse_lock : bool
        If True, acquire a read lock to prevent races with file moves on FUSE mounts (default: True)
    lock_timeout : float
        Timeout for lock acquisition in seconds (default: 5.0)

    Yields
    ------
        h5py.File
        h5py.File object with memory-mapped I/O

    Returns
    -------
        None

    Notes
    -----
        - Only works for read-only access
        - File is loaded into memory (watch RAM usage)
        - Best for files < 4GB or when plenty of RAM available

    Examples
    --------
        >>> with open_uvh5_mmap("/data/small_file.hdf5") as f:
        ...     data = f['visdata'][:]  # Very fast sequential read
    """
    from contextlib import nullcontext

    import h5py

    # Acquire FUSE-aware read lock if requested
    lock_context = nullcontext()
    if use_fuse_lock:
        try:
            from dsa110_continuum.utils.fuse_lock import get_fuse_lock_manager

            lock_mgr = get_fuse_lock_manager()
            lock_context = lock_mgr.read_lock(str(path), timeout=lock_timeout)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Could not acquire FUSE read lock for {path}: {e}")

    with lock_context:
        # 'core' driver loads entire file into memory
        # backing_store=False prevents writeback (read-only)
        with h5py.File(
            path,
            "r",
            driver="core",
            backing_store=False,
            # When using core driver, we don't need chunk cache
            rdcc_nbytes=0,
            rdcc_nslots=1,
        ) as f:
            yield f


def get_chunk_info(path: str | Path, dataset_name: str) -> dict | None:
    """Get chunk information for a dataset.

        Useful for diagnosing performance issues and choosing optimal
        cache sizes.

    Parameters
    ----------
    path : Union[str, Path]
        Path to HDF5 file
    dataset_name : str
        Name of dataset (e.g., 'visdata', 'Header/time_array')

    Returns
    -------
        >>> info = get_chunk_info("/data/file.hdf5", "visdata")
        >>> print(f"Chunk size: {info['chunk_size_bytes'] / 1024 / 1024:.1f} MB")
    """
    import h5py
    import numpy as np

    with h5py.File(path, "r") as f:
        if dataset_name not in f:
            return None

        ds = f[dataset_name]
        if not ds.chunks:
            return None

        chunk_shape = ds.chunks
        dtype = ds.dtype
        chunk_size = int(np.prod(chunk_shape)) * dtype.itemsize

        # Get compression filter
        compression = None
        if ds.compression:
            compression = ds.compression

        return {
            "chunks": chunk_shape,
            "chunk_size_bytes": chunk_size,
            "compression": compression,
            "dtype": str(dtype),
        }


# Backwards compatibility: simple wrapper for quick migration
def h5py_open(
    path: str | Path,
    mode: str = "r",
    **kwargs,
) -> h5py.File:
    """Direct h5py.File replacement with optimized defaults.

        This function can be used as a drop-in replacement for h5py.File()
        when you need the file handle outside a context manager.

    WARNING: Caller is responsible for closing the file!

    Parameters
    ----------
    path : Union[str, Path]
        Path to HDF5 file
    mode : str
        File mode
        (Default value = "r")
        **kwargs :
        Additional h5py.File arguments

    """
    import h5py

    # Set optimized defaults if not specified
    if "rdcc_nbytes" not in kwargs:
        kwargs["rdcc_nbytes"] = HDF5_CACHE_SIZE_DEFAULT
    if "rdcc_nslots" not in kwargs:
        kwargs["rdcc_nslots"] = HDF5_CACHE_SLOTS

    return h5py.File(path, mode, **kwargs)


def get_hdf5_time_range(
    path: str | Path,
) -> Tuple[datetime.datetime | None, datetime.datetime | None]:
    """Extract start and end datetime from HDF5 file.

    Reads the 'Header/time_array' dataset to determine the time range.
    Assumes times are in Julian Date (JD) format, which is standard for
    DSA-110 raw visibility data.

    Parameters
    ----------
    path : Union[str, Path]
        Path to HDF5 file

    Returns
    -------
    Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]
        (start_time, end_time) as UTC datetime objects.
        Returns (None, None) if time array cannot be read or is empty.
    """
    from dsa110_continuum.utils.time_utils import jd_to_astropy_time

    try:
        with open_uvh5_metadata(path) as f:
            # Check for Header/time_array
            if "Header" in f and "time_array" in f["Header"]:
                times = f["Header"]["time_array"][()]
            elif "time_array" in f:
                # Fallback to root time_array if present
                times = f["time_array"][()]
            else:
                logger.debug(f"No time_array found in {path}")
                return None, None

            if len(times) == 0:
                return None, None

            t_start_jd = times[0]
            t_end_jd = times[-1]

            # Calculate integration time to get true end time
            dt = 0.0
            if len(times) > 1:
                dt = times[1] - times[0]

            # Convert to datetime using astropy
            # t_start is the start of the first integration
            # t_end is the start of the last integration
            # We want the end of the last integration for the file end time
            start_dt = jd_to_astropy_time(t_start_jd).datetime
            end_dt = jd_to_astropy_time(t_end_jd + dt).datetime

            return start_dt, end_dt

    except Exception as e:
        logger.debug(f"Failed to extract time range from {path}: {e}")
        return None, None


def get_hdf5_pointing(
    path: str | Path,
    *,
    degrees: bool = False,
) -> Tuple[float | None, float | None]:
    """Extract phase center RA and Dec from HDF5 file.

    Tries multiple HDF5 key paths in priority order to handle different
    file format generations:

    1. ``Header/extra_keywords/phase_center_ra`` / ``phase_center_dec`` (newest DSA-110 format)
    2. ``Header/phase_center_app_ra`` / ``phase_center_app_dec`` (older apparent-coordinate format)
    3. ``Header/phase_center_ra`` / ``phase_center_dec`` (standard pyuvdata format)
    4. Root-level ``phase_center_ra`` / ``phase_center_dec`` (fallback)

    Parameters
    ----------
    path : Union[str, Path]
        Path to HDF5 file
    degrees : bool
        If True, return values in degrees.  Default False (radians).

    Returns
    -------
    Tuple[Optional[float], Optional[float]]
        (ra, dec) in radians (or degrees if *degrees* is True).
        Returns (None, None) if not found.
    """
    import numpy as _np

    # Ordered list of (ra_key, dec_key) pairs to try
    _KEY_PAIRS = [
        ("Header/extra_keywords/phase_center_ra", "Header/extra_keywords/phase_center_dec"),
        ("Header/phase_center_app_ra", "Header/phase_center_app_dec"),
    ]
    # These require *both* keys present
    _PAIRED_KEYS = [
        ("Header", "phase_center_ra", "phase_center_dec"),
    ]

    try:
        with open_uvh5_metadata(path) as f:
            ra = None
            dec = None

            # Strategy 1: full-path keys (extra_keywords, app coords)
            for ra_key, dec_key in _KEY_PAIRS:
                if ra is None and ra_key in f:
                    ra = float(f[ra_key][()])
                if dec is None and dec_key in f:
                    dec = float(f[dec_key][()])
                # Dec-only extraction is valid (RA may come from LST later)
                if ra is not None and dec is not None:
                    break

            # Strategy 2: Header group with sub-keys
            if ra is None or dec is None:
                for group_key, ra_sub, dec_sub in _PAIRED_KEYS:
                    if group_key in f:
                        header = f[group_key]
                        if ra is None and ra_sub in header:
                            val = header[ra_sub][()]
                            if hasattr(val, "__len__") and len(val) > 0:
                                val = val[0]
                            if hasattr(val, "item"):
                                val = val.item()
                            ra = float(val)
                        if dec is None and dec_sub in header:
                            val = header[dec_sub][()]
                            if hasattr(val, "__len__") and len(val) > 0:
                                val = val[0]
                            if hasattr(val, "item"):
                                val = val.item()
                            dec = float(val)

            # Strategy 3: root-level fallback
            if ra is None and "phase_center_ra" in f:
                val = f["phase_center_ra"][()]
                if hasattr(val, "__len__") and len(val) > 0:
                    val = val[0]
                if hasattr(val, "item"):
                    val = val.item()
                ra = float(val)
            if dec is None and "phase_center_dec" in f:
                val = f["phase_center_dec"][()]
                if hasattr(val, "__len__") and len(val) > 0:
                    val = val[0]
                if hasattr(val, "item"):
                    val = val.item()
                dec = float(val)

            if ra is None and dec is None:
                logger.debug(f"No pointing info found in {path}")
                return None, None

            if degrees:
                ra = float(_np.degrees(ra)) if ra is not None else None
                dec = float(_np.degrees(dec)) if dec is not None else None

            return ra, dec

    except Exception as e:
        logger.debug(f"Failed to extract pointing from {path}: {e}")
        return None, None


__all__ = [
    "configure_h5py_cache_defaults",
    "get_h5py_cache_info",
    "open_uvh5",
    "open_uvh5_metadata",
    "open_uvh5_streaming",
    "open_uvh5_large_cache",
    "open_uvh5_mmap",
    "get_chunk_info",
    "h5py_open",
    "get_hdf5_time_range",
    "get_hdf5_pointing",
    "HDF5_CACHE_SIZE_DEFAULT",
    "HDF5_CACHE_SIZE_LARGE",
    "HDF5_CACHE_SIZE_METADATA",
    "HDF5_CACHE_SIZE_STREAMING",
]
