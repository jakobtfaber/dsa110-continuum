"""Utilities for CASA FIELD direction column layouts."""

from __future__ import annotations

import numpy as np

__all__ = ["extract_field_ra_dec", "set_field_ra_dec"]


def extract_field_ra_dec(direction_col: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Extract per-field RA/Dec radians from a FIELD direction column.

    Supported layouts are the rows-first python-casacore shape ``(nfields, 1, 2)``,
    the CASA column-major adapter shape ``(nfields, 2, 1)``, and a normalized
    two-dimensional ``(nfields, 2)`` fallback.
    """
    arr = np.asarray(direction_col)
    if arr.ndim == 3 and arr.shape[1:] == (1, 2):
        return arr[:, 0, 0], arr[:, 0, 1]
    if arr.ndim == 3 and arr.shape[1:] == (2, 1):
        return arr[:, 0, 0], arr[:, 1, 0]
    if arr.ndim == 2 and arr.shape[1] == 2:
        return arr[:, 0], arr[:, 1]
    raise ValueError(f"Unsupported FIELD direction column shape: {arr.shape}")


def set_field_ra_dec(direction_col: np.ndarray, ra_rad: float, dec_rad: float) -> np.ndarray:
    """Return a copy of ``direction_col`` with every field overwritten by ``ra_rad``/``dec_rad``.

    The same RA/Dec is broadcast to *all* fields in the column; this is the
    intended behavior for phase-centre normalization across a multi-field MS.
    The input array is not mutated. Supported input layouts match those of
    :func:`extract_field_ra_dec`.
    """
    arr = np.array(direction_col, copy=True)
    if arr.ndim == 3 and arr.shape[1:] == (1, 2):
        arr[:, 0, 0] = ra_rad
        arr[:, 0, 1] = dec_rad
        return arr
    if arr.ndim == 3 and arr.shape[1:] == (2, 1):
        arr[:, 0, 0] = ra_rad
        arr[:, 1, 0] = dec_rad
        return arr
    if arr.ndim == 2 and arr.shape[1] == 2:
        arr[:, 0] = ra_rad
        arr[:, 1] = dec_rad
        return arr
    raise ValueError(f"Unsupported FIELD direction column shape: {arr.shape}")
