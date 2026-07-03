# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Fast UVH5 metadata reader using pyuvdata's FastUVH5Meta.

FastUVH5Meta provides lazy, read-on-demand access to UVH5 file metadata
without loading the entire header. This is significantly faster for
operations that only need a few attributes (e.g., times, frequencies).

Performance Comparison:
    UVData.read(read_data=False): ~0.5-1.0s per file (full header)
    FastUVH5Meta.times:           ~0.01-0.05s (only time_array)

Preferred Usage (context manager)::

    >>> from dsa110_continuum.utils.fast_meta import FastMeta
    >>> with FastMeta("/path/to/file.hdf5") as meta:
    ...     times = meta.unique_times
    ...     freqs = meta.freq_array
    ...     mid = meta.mid_time_mjd

Deprecated convenience wrappers (get_uvh5_times, get_uvh5_freqs,
get_uvh5_mid_mjd) are still available but will be removed in a future
release. Prefer using the FastMeta context manager directly.

    # Or use the class directly for multiple attributes
    >>> from dsa110_continuum.utils.fast_meta import FastMeta
    >>> with FastMeta("/path/to/file.hdf5") as meta:
    ...     times = meta.times
    ...     freqs = meta.freq_array
    ...     npol = meta.Npols

Reference:
    https://pyuvdata.readthedocs.io/en/latest/fast_uvh5_meta.html
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

if TYPE_CHECKING:
    if HAS_NUMPY:
        from numpy.typing import NDArray
    else:
        NDArray = any

logger = logging.getLogger(__name__)

# Offset between JD and MJD (JD = MJD + 2400000.5)
JD_TO_MJD_OFFSET = 2400000.5

# Import FastUVH5Meta - available in pyuvdata >= 2.4
try:
    from pyuvdata.uvdata import FastUVH5Meta

    HAS_FAST_META = True
except ImportError:
    HAS_FAST_META = False
    logger.warning("FastUVH5Meta not available. Install pyuvdata >= 2.4 for faster metadata reads.")


class FastMeta:
    """Context manager wrapper for FastUVH5Meta.

        Provides a clean interface with automatic resource management.
        Falls back to UVData.read(read_data=False) if FastUVH5Meta unavailable.

    Performance: ~700x faster than UVData.read(read_data=False) for
        accessing individual attributes like time_array or freq_array.

        Example
    -------
        >>> with FastMeta("file.hdf5") as meta:
        ...     print(f"Times: {meta.time_array}")
        ...     print(f"Freqs: {meta.freq_array.shape}")
    """

    def __init__(self, path: str | Path):
        """Initialize with path to UVH5 file.

        Parameters
        ----------
        path : str or Path
            Path to UVH5 file
        """
        self.path = Path(path)
        self._meta = None
        self._uvdata = None  # Fallback

    def __enter__(self) -> FastMeta:
        """Open file and create metadata reader."""
        if HAS_FAST_META:
            # Don't use blt_order="determine" - it's slow
            self._meta = FastUVH5Meta(str(self.path))
        else:
            # Fallback to UVData
            from pyuvdata import UVData

            self._uvdata = UVData()
            self._uvdata.read(
                str(self.path),
                file_type="uvh5",
                read_data=False,
                run_check=False,
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up resources."""
        # FastUVH5Meta doesn't need explicit cleanup
        self._meta = None
        self._uvdata = None
        return False

    def __getattr__(self, name: str):
        """Proxy attribute access to underlying metadata object."""
        if self._meta is not None:
            return getattr(self._meta, name)
        elif self._uvdata is not None:
            return getattr(self._uvdata, name)
        raise AttributeError("FastMeta not initialized. Use as context manager.")

    @property
    def unique_times(self) -> NDArray[np.float64]:
        """Get unique times from file (JD). Use time_array for raw access."""
        if not HAS_NUMPY:
            raise RuntimeError("Numpy is required for unique_times")
        if self._meta is not None:
            return np.unique(self._meta.time_array)
        elif self._uvdata is not None:
            return np.unique(self._uvdata.time_array)
        raise RuntimeError("FastMeta not initialized")

    @property
    def mid_time_mjd(self) -> float:
        """Get middle time as MJD."""
        times = self.time_array  # Use raw array, faster
        mid_jd = (times.min() + times.max()) / 2
        return mid_jd - JD_TO_MJD_OFFSET  # JD to MJD


def get_uvh5_times(path: str | Path, unique: bool = True) -> NDArray[np.float64]:
    """Get times from UVH5 file.

    .. deprecated::
        Use ``with FastMeta(path) as meta: meta.unique_times`` instead.

    Parameters
    ----------
    path : str or Path
        Path to UVH5 file
    unique : bool, optional
        If True, return unique times; if False, return raw time_array
        (Default value = True)

    Returns
    -------
        array_like
        Times extracted from the UVH5 file
    """
    import warnings

    warnings.warn(
        "get_uvh5_times() is deprecated. Use 'with FastMeta(path) as meta: "
        "meta.unique_times' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    with FastMeta(path) as meta:
        if unique:
            return meta.unique_times
        return meta.time_array


def get_uvh5_mid_mjd(path: str | Path) -> float:
    """Get middle time as MJD from UVH5 file.

    .. deprecated::
        Use ``with FastMeta(path) as meta: meta.mid_time_mjd`` instead.

    Parameters
    ----------
    path : str or Path
        Path to UVH5 file

    Returns
    -------
        float
        Middle time as Modified Julian Date (MJD)
    """
    import warnings

    warnings.warn(
        "get_uvh5_mid_mjd() is deprecated. Use 'with FastMeta(path) as meta: "
        "meta.mid_time_mjd' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    with FastMeta(path) as meta:
        return meta.mid_time_mjd


def get_uvh5_freqs(path: str | Path) -> NDArray[np.float64]:
    """Get frequency array from UVH5 file.

    .. deprecated::
        Use ``with FastMeta(path) as meta: meta.freq_array`` instead.

    Parameters
    ----------
    path : str or Path
        Path to UVH5 file

    Returns
    -------
        array_like
        Frequency array from the UVH5 file
    """
    import warnings

    warnings.warn(
        "get_uvh5_freqs() is deprecated. Use 'with FastMeta(path) as meta: "
        "meta.freq_array' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    with FastMeta(path) as meta:
        return meta.freq_array


def peek_uvh5_phase_and_midtime(path: str | Path) -> tuple[float, float, float]:
    """Get phase center (RA, Dec in radians) and mid-time (MJD) from UVH5 file.

    This is a fast peek operation that reads minimal metadata.
    
    For phased data: reads phase_center_app_ra/dec arrays.
    For drift scans: reads phase_center_app_dec and computes RA from LST using astropy.

    Parameters
    ----------
    path : str or Path
        Path to UVH5 file

    Returns
    -------
        tuple
        (RA, Dec) in radians and mid-time as MJD

    Raises
    ------
    KeyError
        If phase center information is not found in the file
    """
    import h5py
    
    with h5py.File(str(path), 'r') as f:
        header = f['Header']
        
        # Get mid time (needed for all paths)
        times = header['time_array'][:]
        mid_jd = float((times.min() + times.max()) / 2)
        mid_mjd = mid_jd - JD_TO_MJD_OFFSET
        
        # Case 1: Explicit phase_center_app_ra/dec arrays (phased observations)
        if 'phase_center_app_ra' in header and 'phase_center_app_dec' in header:
            ra_arr = header['phase_center_app_ra']
            dec_arr = header['phase_center_app_dec']
            
            # Handle both scalar and array cases
            if ra_arr.shape == ():
                ra_rad = float(ra_arr[()])
            else:
                ra_rad = float(ra_arr[0])
            
            if dec_arr.shape == ():
                dec_rad = float(dec_arr[()])
            else:
                dec_rad = float(dec_arr[0])
            
            return (ra_rad, dec_rad, mid_mjd)
        
        # Case 2: Drift scan - only phase_center_app_dec exists
        # For drift scans with HA=0, RA equals LST at observation time
        if 'phase_center_app_dec' in header:
            dec_arr = header['phase_center_app_dec']
            
            # Handle both scalar and array cases
            if dec_arr.shape == ():
                dec_rad = float(dec_arr[()])
            else:
                dec_rad = float(dec_arr[0])
            
            # Check if this is a drift scan (ha_phase_center = 0)
            is_drift = False
            if 'extra_keywords' in header:
                ek = header['extra_keywords']
                if 'ha_phase_center' in ek:
                    ha = float(ek['ha_phase_center'][()])
                    is_drift = abs(ha) < 0.01  # HA ≈ 0 means drift scan
            
            # Also check phase_type
            if 'phase_type' in header:
                phase_type = header['phase_type'][()]
                if isinstance(phase_type, bytes):
                    phase_type = phase_type.decode()
                if phase_type.lower() == 'drift':
                    is_drift = True
            
            if is_drift:
                # Use astropy to compute LST from JD and telescope location
                from astropy.time import Time
                from dsa110_continuum.utils.constants import DSA110_LOCATION
                
                if DSA110_LOCATION is None:
                    raise ImportError("astropy not available for LST calculation")
                
                obs_time = Time(mid_jd, format='jd', scale='utc', location=DSA110_LOCATION)
                lst = obs_time.sidereal_time('apparent')
                ra_rad = float(lst.rad)
                
                return (ra_rad, dec_rad, mid_mjd)
        
        # Case 3: Fallback - Try phase_center_catalog (but check if it's actually RA/Dec not AltAz)
        with FastMeta(path) as meta:
            catalog = meta.phase_center_catalog
            
            if catalog and 0 in catalog:
                entry = catalog[0]
                # Only use catalog if it's NOT in AltAz frame
                if entry.get("cat_frame") not in ("altaz", "az_el", "horizontal"):
                    ra_rad = entry.get("cat_lon")
                    dec_rad = entry.get("cat_lat")
                    if ra_rad is not None and dec_rad is not None:
                        return (float(ra_rad), float(dec_rad), mid_mjd)
        
        raise KeyError(f"No phase center information found in {path}")

