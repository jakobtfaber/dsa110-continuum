# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""
FITS file utilities for proper format compliance.

Ensures FITS headers conform to FITS standard format requirements.
"""

from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def get_2d_data_and_wcs(fits_path: str | Path) -> tuple[np.ndarray, WCS, dict[str, Any]]:
    """Load FITS file and extract 2D data with WCS.

        Handles 4D FITS cubes by squeezing degenerate axes.

    Parameters
    ----------
    fits_path : str or Path
        Path to FITS file

    Returns
    -------
        tuple
        2D data array and WCS object extracted from the FITS file
    """
    with fits.open(fits_path, memmap=True) as hdul:
        data = hdul[0].data
        header = hdul[0].header

        if data is None:
            raise ValueError(f"No image data in {fits_path}")

        # Squeeze to 2D
        data = np.asarray(data, dtype=np.float64)
        while data.ndim > 2:
            data = data[0]

        # Get 2D WCS
        try:
            wcs = WCS(header, naxis=2)
        except Exception:
            wcs = WCS(header).celestial

        # Extract useful header info
        header_info = {
            "bunit": header.get("BUNIT", "Jy/beam"),
            "bmaj": header.get("BMAJ"),
            "bmin": header.get("BMIN"),
            "bpa": header.get("BPA"),
            "object": header.get("OBJECT", ""),
            "date_obs": header.get("DATE-OBS", ""),
        }

    return data, wcs, header_info


def format_fits_header_value(value: float, precision: int = 10) -> float:
    """Format a FITS header value to conform to FITS fixed format.

        FITS fixed format requires values to be written in a specific way.
        High-precision floating point values can cause warnings.

    Parameters
    ----------
    value : float
        The numeric value to format
    precision : int, optional
        Number of decimal places (default: 10)
        (Default value = 10)

    Returns
    -------
        str
        Formatted FITS header value string
    """
    if not isinstance(value, (int, float, np.number)):
        return value

    # Round to specified precision
    return round(float(value), precision)


def fix_cdelt_in_header(header: fits.Header) -> fits.Header:
    """Fix CDELT1 and CDELT2 values in FITS header to conform to FITS format.

        Rounds CDELT values to reasonable precision (10 decimal places).
        This prevents CASA warnings about non-conforming FITS format.

    Parameters
    ----------
    header : fits.Header
        FITS header to fix

    Returns
    -------
        None
    """
    for key in ["CDELT1", "CDELT2"]:
        if key in header:
            original_value = header[key]
            formatted_value = format_fits_header_value(original_value, precision=10)
            header[key] = formatted_value

    return header


def create_fits_hdu(
    data: np.ndarray, header: fits.Header | None = None, fix_cdelt: bool = True
) -> fits.PrimaryHDU:
    """Create a FITS PrimaryHDU with properly formatted header.

    Parameters
    ----------
    data : np.ndarray
        Image data array
    header : Optional[fits.Header], optional
        FITS header (will be created if None), by default None
    fix_cdelt : bool, optional
        If True, fix CDELT values in header, by default True

    Returns
    -------
        astropy.io.fits.PrimaryHDU
        FITS PrimaryHDU object with data and header
    """
    if header is None:
        header = fits.Header()

    if fix_cdelt:
        header = fix_cdelt_in_header(header)

    return fits.PrimaryHDU(data=data, header=header)


def write_fits(
    filename: str,
    data: np.ndarray,
    header: fits.Header | None = None,
    overwrite: bool = False,
    fix_cdelt: bool = True,
) -> None:
    """Write FITS file with properly formatted header.

    Parameters
    ----------
    filename :
        Output FITS filename
    data :
        Image data array
    header :
        Optional FITS header
    overwrite :
        Overwrite existing file (default: False)
    fix_cdelt :
        If True, fix CDELT values in header (default: True)
    """
    hdu = create_fits_hdu(data, header, fix_cdelt=fix_cdelt)
    hdu.writeto(filename, overwrite=overwrite)
