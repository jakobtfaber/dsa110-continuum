# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
Memory-efficient Measurement Set utilities.

This module provides optimized MS access patterns using sampling and chunking
to reduce memory usage for validation and QA operations.
"""

# Note: cache_info() methods from lru_cache don't take arguments, but pylint
# incorrectly infers parameters from the cached function signatures.

import os
from functools import lru_cache
from typing import Any

import numpy as np


def sample_ms_column(
    ms_path: str,
    column: str,
    sample_size: int = 10000,
    seed: int | None = None,
    start_row: int = 0,
    end_row: int | None = None,
) -> np.ndarray:
    """Sample a column from MS without loading entire column into memory.

        Uses random or sequential sampling depending on MS size to provide
        representative statistics while minimizing memory usage.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    column : str
        Column name to sample
    sample_size : int, optional
        Target number of samples (default: 10000)
    seed : int or None, optional
        Random seed for reproducible sampling (None for sequential)
    start_row : int, optional
        Starting row (default: 0)
    end_row : int or None, optional
        Ending row (None for all rows)

    Returns
    -------
        numpy.ndarray
        Sampled column data as numpy array

    Raises
    ------
        ValueError
        If column doesn't exist or MS is invalid
    """
    # Ensure CASAPATH is set before importing CASA modules
    from dsa110_continuum.utils.casa_init import ensure_casa_path

    ensure_casa_path()

    try:
        import casacore.tables as casatables

        table = casatables.table  # noqa: N816
    except ImportError:
        raise ImportError("casacore.tables required for MS operations")

    with table(ms_path, readonly=True) as tb:  # type: ignore[import]
        if column not in tb.colnames():
            from dsa110_continuum.utils.exceptions import ValidationError

            raise ValidationError(
                errors=[f"Column '{column}' not found in MS: {ms_path}"],
                context={
                    "ms_path": ms_path,
                    "column": column,
                    "operation": "sample_column",
                },
                suggestion="Check that the column name is correct and the MS is valid",
            )

        n_rows = tb.nrows()
        if n_rows == 0:
            return np.array([])

        # Adjust for row range
        if end_row is None:
            end_row = n_rows
        n_rows_available = min(end_row, n_rows) - start_row

        if n_rows_available <= 0:
            return np.array([])

        # Adjust sample size to available rows
        actual_sample_size = min(sample_size, n_rows_available)

        # For small MS, just read directly
        if n_rows_available <= sample_size:
            return tb.getcol(column, startrow=start_row, nrow=n_rows_available)

        # For larger MS, use sampling
        if seed is not None:
            np.random.seed(seed)

        # Use random sampling for better representation
        indices = np.random.choice(n_rows_available, size=actual_sample_size, replace=False)
        indices.sort()

        # Read in chunks to avoid memory spikes
        chunk_size = 1000
        samples = []

        for i in range(0, len(indices), chunk_size):
            chunk_indices = indices[i : i + chunk_size]
            chunk_start = chunk_indices[0] + start_row
            chunk_nrow = chunk_indices[-1] - chunk_indices[0] + 1

            # Read chunk
            chunk_data = tb.getcol(column, startrow=chunk_start, nrow=chunk_nrow)

            # Extract samples from chunk
            chunk_samples = chunk_data[chunk_indices - chunk_indices[0]]
            samples.append(chunk_samples)

        return np.concatenate(samples)


@lru_cache(maxsize=64)
def _validate_ms_unflagged_fraction_cached(
    ms_path: str,
    mtime: float,
    sample_size: int = 10000,
    datacolumn: str = "DATA",  # noqa: ARG001
) -> float:
    """
    Internal cached function for validate_ms_unflagged_fraction.

    Cache key includes file modification time for automatic invalidation.
    """
    # Ensure CASAPATH is set before importing CASA modules
    from dsa110_continuum.utils.casa_init import ensure_casa_path

    ensure_casa_path()

    try:
        import casacore.tables as casatables

        table = casatables.table  # noqa: N816
    except ImportError:
        raise ImportError("casacore.tables required for MS operations")

    with table(ms_path, readonly=True) as tb:
        n_rows = tb.nrows()
        if n_rows == 0:
            return 0.0

        if "FLAG" not in tb.colnames():
            # No flags means all data is unflagged
            return 1.0

        # OPTIMIZATION: Use vectorized sampling instead of row-by-row reads
        # Calculate sample indices
        sample_size = min(sample_size, n_rows)
        step = max(1, n_rows // sample_size)
        sample_indices = np.arange(0, n_rows, step)[:sample_size]

        # Read in chunks to balance memory and efficiency
        chunk_size = 1000
        flags_sample = []
        for i in range(0, len(sample_indices), chunk_size):
            chunk_indices = sample_indices[i : i + chunk_size]
            chunk_start = int(chunk_indices[0])
            chunk_end = int(chunk_indices[-1]) + 1
            chunk_nrow = chunk_end - chunk_start
            # Read chunk of flags
            chunk_flags = tb.getcol("FLAG", startrow=chunk_start, nrow=chunk_nrow)
            # Extract sampled rows from chunk
            chunk_sample = chunk_flags[chunk_indices - chunk_start]
            flags_sample.append(chunk_sample)

        flags_sample = np.concatenate(flags_sample) if flags_sample else np.array([])

        # Calculate unflagged fraction
        unflagged_fraction = float(np.mean(~flags_sample))
        return unflagged_fraction


def validate_ms_unflagged_fraction(
    ms_path: str, sample_size: int = 10000, datacolumn: str = "DATA"
) -> float:
    """Validate unflagged data fraction using memory-efficient sampling.

    OPTIMIZATION: Uses LRU cache to avoid redundant flag validation when
        flags haven't changed. Cache automatically invalidates when MS is modified.

        Uses sampling to estimate unflagged fraction without loading entire
        FLAG column into memory. Suitable for large MS files.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    sample_size : int, optional
        Number of rows to sample (default: 10000)
    datacolumn : str, optional
        Data column to check flags for (default: "DATA")

    Returns
    -------
        float
        Fraction of unflagged data (0.0 to 1.0)
    """
    if not os.path.exists(ms_path):
        raise FileNotFoundError(f"MS not found: {ms_path}")
    mtime = os.path.getmtime(ms_path)
    return _validate_ms_unflagged_fraction_cached(ms_path, mtime, sample_size, datacolumn)


def get_antennas_cached(ms_path: str) -> list[str]:
    """Get antenna list from MS with simple caching.

        Note
    ----
        This is a simple implementation. For production, consider
        using functools.lru_cache with proper cache invalidation.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set

    Returns
    -------
        list of str
        List of antenna names
    """
    # Ensure CASAPATH is set before importing CASA modules
    from dsa110_continuum.utils.casa_init import ensure_casa_path

    ensure_casa_path()

    try:
        import casacore.tables as casatables

        table = casatables.table  # noqa: N816
    except ImportError:
        raise ImportError("casacore.tables required for MS operations")

    with table(f"{ms_path}::ANTENNA", readonly=True) as tb:
        return tb.getcol("NAME").tolist()


def get_fields_cached(ms_path: str) -> list[tuple[str, float, float]]:
    """Get field info from MS (name, RA, Dec) with simple caching.

        Note
    ----
        This is a simple implementation. For production, consider
        using functools.lru_cache with proper cache invalidation.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set

    Returns
    -------
        list of tuple
        List of tuples: (field_name, ra_deg, dec_deg)
    """
    # Ensure CASAPATH is set before importing CASA modules
    from dsa110_continuum.utils.casa_init import ensure_casa_path

    ensure_casa_path()

    try:
        import casacore.tables as casatables

        table = casatables.table  # noqa: N816
    except ImportError:
        raise ImportError("casacore.tables required for MS operations")

    with table(f"{ms_path}::FIELD", readonly=True) as tb:
        names = tb.getcol("NAME")

        # Get phase center (prefer REFERENCE_DIR, fallback to PHASE_DIR)
        if "REFERENCE_DIR" in tb.colnames():
            phase_dir = tb.getcol("REFERENCE_DIR")
        elif "PHASE_DIR" in tb.colnames():
            phase_dir = tb.getcol("PHASE_DIR")
        else:
            raise ValueError("MS has neither REFERENCE_DIR nor PHASE_DIR columns")

        # Convert to degrees
        fields = []
        for i, name in enumerate(names):
            # phase_dir shape: (nfields, 1, 2) -> (ra_rad, dec_rad)
            ra_rad, dec_rad = phase_dir[i][0]
            fields.append((name, np.rad2deg(ra_rad), np.rad2deg(dec_rad)))

        return fields


def estimate_ms_size(ms_path: str) -> dict:
    """Estimate MS size and data characteristics without loading full data.

        Returns metadata about MS size, useful for estimating processing time
        and memory requirements.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set

    Returns
    -------
        dict
        Dictionary with size estimates:
        - n_rows: Number of data rows
        - n_antennas: Number of antennas
        - n_fields: Number of fields
        - n_spws: Number of spectral windows
        - n_channels: Number of channels (per SPW)
        - estimated_memory_gb: Rough estimate of memory usage (GB)
    """
    # Ensure CASAPATH is set before importing CASA modules
    from dsa110_continuum.utils.casa_init import ensure_casa_path

    ensure_casa_path()

    try:
        import casacore.tables as casatables

        table = casatables.table  # noqa: N816
    except ImportError:
        raise ImportError("casacore.tables required for MS operations")

    with table(ms_path, readonly=True) as tb:
        n_rows = tb.nrows()

        # Get column shapes to estimate data size
        if "DATA" in tb.colnames():
            # Sample first row to get data shape
            data_sample = tb.getcol("DATA", startrow=0, nrow=1)
            if len(data_sample) > 0:
                data_shape = data_sample[0].shape  # (n_chan, n_pol)
                n_channels = data_shape[0]
                n_pols = data_shape[1]
            else:
                n_channels = 1
                n_pols = 1
        else:
            n_channels = 1
            n_pols = 1

        # Get antenna count
        try:
            with table(f"{ms_path}::ANTENNA", readonly=True) as ant_tb:
                n_antennas = ant_tb.nrows()
        except (OSError, RuntimeError):
            n_antennas = 0

        # Get field count
        try:
            with table(f"{ms_path}::FIELD", readonly=True) as field_tb:
                n_fields = field_tb.nrows()
        except (OSError, RuntimeError):
            n_fields = 0

        # Get SPW count
        try:
            with table(f"{ms_path}::SPECTRAL_WINDOW", readonly=True) as spw_tb:
                n_spws = spw_tb.nrows()
        except (OSError, RuntimeError):
            n_spws = 1

        # Rough memory estimate: DATA + FLAG + MODEL_DATA + CORRECTED_DATA
        # Complex64 = 8 bytes per value, bool = 1 byte
        bytes_per_row = (n_channels * n_pols * 8 * 4) + (n_channels * n_pols * 1)  # 4 columns
        estimated_memory_gb = (n_rows * bytes_per_row) / (1024**3)

    return {
        "n_rows": n_rows,
        "n_antennas": n_antennas,
        "n_fields": n_fields,
        "n_spws": n_spws,
        "n_channels": n_channels,
        "n_pols": n_pols,
        "estimated_memory_gb": estimated_memory_gb,
    }


@lru_cache(maxsize=128)
def get_ms_metadata_cached(ms_path: str, mtime: float) -> dict[str, Any]:  # noqa: ARG001
    """Get and cache MS metadata (SPW, FIELD, ANTENNA) to avoid redundant reads.

    OPTIMIZATION: Uses LRU cache to store frequently accessed MS metadata,
        reducing redundant table opens and getcol() calls. Cache key includes
        file modification time to automatically invalidate when MS is modified.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    mtime : float
        File modification time (for cache invalidation)

    Returns
    -------
        dict
        Dictionary with cached metadata:
        - chan_freq: Channel frequencies (numpy array)
        - nspw: Number of spectral windows
        - phase_dir: Phase direction (numpy array)
        - field_names: Field names (list)
        - nfields: Number of fields
        - antenna_names: Antenna names (list)
        - nantennas: Number of antennas

    Examples
    --------
        import os
        mtime = os.path.getmtime(ms_path)
        metadata = get_ms_metadata_cached(ms_path, mtime)
        chan_freq = metadata['chan_freq']
        phase_dir = metadata['phase_dir']
    """
    # Ensure CASAPATH is set before importing CASA modules
    from dsa110_continuum.utils.casa_init import ensure_casa_path

    ensure_casa_path()

    try:
        import casacore.tables as casatables

        table = casatables.table  # noqa: N816
    except ImportError:
        raise ImportError("casacore.tables required for MS operations")

    metadata = {}

    # Read SPW metadata
    try:
        with table(f"{ms_path}::SPECTRAL_WINDOW", readonly=True) as spw:
            metadata["chan_freq"] = spw.getcol("CHAN_FREQ")
            metadata["nspw"] = spw.nrows()
    except (OSError, RuntimeError, KeyError):
        metadata["chan_freq"] = np.array([])
        metadata["nspw"] = 0

    # Read FIELD metadata
    try:
        with table(f"{ms_path}::FIELD", readonly=True) as fld:
            metadata["phase_dir"] = (
                fld.getcol("PHASE_DIR")
                if "PHASE_DIR" in fld.colnames()
                else fld.getcol("REFERENCE_DIR")
            )
            metadata["field_names"] = fld.getcol("NAME").tolist()
            metadata["nfields"] = fld.nrows()
    except (OSError, RuntimeError, KeyError):
        metadata["phase_dir"] = np.array([])
        metadata["field_names"] = []
        metadata["nfields"] = 0

    # Read ANTENNA metadata
    try:
        with table(f"{ms_path}::ANTENNA", readonly=True) as ant:
            metadata["antenna_names"] = ant.getcol("NAME").tolist()
            metadata["nantennas"] = ant.nrows()
    except (OSError, RuntimeError, KeyError):
        metadata["antenna_names"] = []
        metadata["nantennas"] = 0

    return metadata


def get_ms_metadata(ms_path: str) -> dict[str, Any]:
    """Get MS metadata with automatic cache invalidation based on file mtime.

        This is a convenience wrapper around get_ms_metadata_cached() that
        automatically includes the file modification time for cache invalidation.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set

    Returns
    -------
        dict
        Dictionary with cached metadata (see get_ms_metadata_cached)
    """
    if not os.path.exists(ms_path):
        raise FileNotFoundError(f"MS not found: {ms_path}")
    mtime = os.path.getmtime(ms_path)
    return get_ms_metadata_cached(ms_path, mtime)


def clear_ms_metadata_cache() -> None:
    """
    Clear MS metadata cache.

    Call this after modifying MS files to ensure cached metadata is invalidated.
    """
    get_ms_metadata_cached.cache_clear()


def clear_flag_validation_cache() -> None:
    """
    Clear flag validation cache.

    Call this after modifying flags in MS files to ensure cached
    validation results are invalidated.
    """
    _validate_ms_unflagged_fraction_cached.cache_clear()


# pylint: disable=no-value-for-parameter
def get_cache_stats() -> dict[str, dict[str, Any]]:
    """Get cache statistics for monitoring and debugging.

        Returns dictionary with cache info for both metadata and flag validation caches.
        Useful for monitoring cache effectiveness and identifying cache issues.

    Returns
    -------
        dict
        Dictionary with cache statistics:
        - 'ms_metadata': Cache info for MS metadata cache
        - 'flag_validation': Cache info for flag validation cache

    Examples
    --------
        ```python
        stats = get_cache_stats()
        print(f"MS metadata cache hits: {stats['ms_metadata']['hits']}")
        print(f"Cache size: {stats['ms_metadata']['currsize']}/{stats['ms_metadata']['maxsize']}")
        ```

    Notes
    -----
        cache_info() is a method on lru_cache that takes no arguments.
        Pylint incorrectly infers mtime parameter is required.
    """
    stats = {}

    # MS metadata cache stats
    # cache_info() is a method on lru_cache that takes no arguments
    ms_cache = get_ms_metadata_cached.cache_info()
    stats["ms_metadata"] = {
        "hits": ms_cache.hits,
        "misses": ms_cache.misses,
        "maxsize": ms_cache.maxsize,
        "currsize": ms_cache.currsize,
        "hit_rate": (
            ms_cache.hits / (ms_cache.hits + ms_cache.misses)
            if (ms_cache.hits + ms_cache.misses) > 0
            else 0.0
        ),
    }

    # Flag validation cache stats
    # cache_info() is a method on lru_cache that takes no arguments
    flag_cache = _validate_ms_unflagged_fraction_cached.cache_info()
    stats["flag_validation"] = {
        "hits": flag_cache.hits,
        "misses": flag_cache.misses,
        "maxsize": flag_cache.maxsize,
        "currsize": flag_cache.currsize,
        "hit_rate": (
            flag_cache.hits / (flag_cache.hits + flag_cache.misses)
            if (flag_cache.hits + flag_cache.misses) > 0
            else 0.0
        ),
    }

    return stats
