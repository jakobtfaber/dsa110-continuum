# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Angle utilities for consistent radiansdegrees conversion and wrapping.

Provides helpers to wrap phase angles to conventional ranges to avoid
discontinuities (e.g., around ±180°) when computing statistics or plotting.
"""

from __future__ import annotations

from typing import Union

import numpy as np

ArrayLike = Union[float, np.ndarray]


def wrap_phase_deg(angles_deg: ArrayLike) -> ArrayLike:
    """Wrap phase angle(s) in degrees to the range [-180, 180).

    Parameters
    ----------
    angles_deg : scalar or array_like
        Scalar or array of angles in degrees.

    Returns
    -------
        scalar or array_like
        Angles wrapped to [-180, 180) with the same shape/type semantics as input.
    """
    # Use numpy modulo that works for scalars and arrays; ensure numpy array ops
    arr = np.asarray(angles_deg)
    wrapped = ((arr + 180.0) % 360.0) - 180.0
    # Preserve scalar type if input was scalar
    if np.isscalar(angles_deg):
        return float(wrapped)
    return wrapped


def wrap_0_360_deg(angles_deg: ArrayLike) -> ArrayLike:
    """Wrap angle(s) in degrees to the range [0, 360).

    Useful for RA-like quantities; do not use for phases (prefer wrap_phase_deg).
    """
    arr = np.asarray(angles_deg)
    wrapped = arr % 360.0
    if np.isscalar(angles_deg):
        return float(wrapped)
    return wrapped
