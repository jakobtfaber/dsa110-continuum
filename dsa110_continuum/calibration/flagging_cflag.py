"""ASKAPSoft cflag-style dynamic amplitude flagging (Python port).

Pass 1 (selection + flat amp) is Stage 0 elsewhere. This module implements Pass 2
(dynamic amplitude with robust IQR scale + integrateSpectra) for XX/YY-only DSA-110
data, then runs the mandatory Stage 2 MAD clip and Stage 3 flag extension.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
from dsa110_continuum.adapters.casa_tables import table
from dsa110_continuum.calibration.flagging_amplitude import flag_residual_rfi_clip
from dsa110_continuum.calibration.flagging_rfi import flag_extend

logger = logging.getLogger(__name__)

IQR_TO_SIGMA = 1.349  # ASKAP/YandaSoft robust scale (IQR → σ)


def iqr_sigma(values: np.ndarray) -> float:
    """Return ASKAP-style robust σ = 1.349 × IQR, or NaN if under-sampled."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 8:
        return float("nan")
    q25, q75 = np.percentile(x, [25.0, 75.0])
    return float(IQR_TO_SIGMA * (q75 - q25))


def dynamic_amplitude_mask(amplitudes: np.ndarray, threshold: float = 4.0) -> np.ndarray:
    """Two-sided robust amplitude mask: |amp − median| > threshold × IQR-σ."""
    amp = np.asarray(amplitudes, dtype=float)
    finite = np.isfinite(amp)
    if not np.any(finite):
        return np.zeros(amp.shape, dtype=bool)
    vals = amp[finite]
    med = float(np.median(vals))
    sig = iqr_sigma(vals)
    if not np.isfinite(sig) or sig <= 0:
        # Degenerate IQR (nearly constant spectrum): fall back to MAD scale.
        mad = float(np.median(np.abs(vals - med)))
        sig = 1.4826 * mad if mad > 0 else 0.0
    if sig <= 0:
        # Still degenerate — flag strict outliers vs a tiny floor.
        floor = max(abs(med) * 1e-6, 1e-6)
        return finite & (np.abs(amp - med) > threshold * floor)
    lo, hi = med - threshold * sig, med + threshold * sig
    return finite & ((amp < lo) | (amp > hi))


def integrate_spectra_mask(amp_time_chan: np.ndarray, threshold: float = 4.0) -> np.ndarray:
    """Flag hot channels after scalar averaging over time (ASKAP integrateSpectra).

    Parameters
    ----------
    amp_time_chan :
        Amplitudes with shape ``(ntime, nchan)``. Non-finite samples are ignored
        in the time average.
    threshold :
        Robust threshold in IQR-σ units applied to the time-averaged spectrum.
    """
    amp = np.asarray(amp_time_chan, dtype=float)
    if amp.ndim != 2:
        raise ValueError(f"expected (ntime, nchan), got {amp.shape}")
    with np.errstate(all="ignore"):
        spectrum = np.nanmean(np.where(np.isfinite(amp), amp, np.nan), axis=0)
    return dynamic_amplitude_mask(spectrum, threshold=threshold)


def _as_nrow_nchan_npol(values: np.ndarray) -> tuple[np.ndarray, str]:
    """Normalize visibility cube to ``(nrow, nchan, npol)``.

    casatools cell shape is ``(npol, nchan)`` → after row-first: ``(nrow, npol, nchan)``.
    python-casacore historically used ``(nrow, nchan, npol)``. Detect both.
    """
    if values.ndim != 3:
        raise ValueError(f"expected 3-D visibility cube, got {values.shape}")
    _nrow, a, b = values.shape
    # Typical: npol ∈ {1,2,4}, nchan ≫ npol (e.g. 48).
    if a in (1, 2, 4) and b not in (1, 2, 4):
        return np.swapaxes(values, 1, 2), "npol_nchan"
    if b in (1, 2, 4) and a not in (1, 2, 4):
        return values, "nchan_npol"
    if a <= 4 and b > a:
        return np.swapaxes(values, 1, 2), "npol_nchan"
    return values, "nchan_npol"


def _from_nrow_nchan_npol(values: np.ndarray, layout: str) -> np.ndarray:
    if layout == "npol_nchan":
        return np.swapaxes(values, 1, 2)
    return values


def _row_quantiles(work: np.ndarray, probs: tuple[float, ...]) -> list[np.ndarray]:
    """Fast per-row quantiles for ``(nrow, nchan)`` with NaNs (sort-based).

    ``np.nanpercentile`` on ~1e6×48 arrays is ~50× slower than sort + gather.
    """
    work = np.asarray(work, dtype=float)
    nrow, nchan = work.shape
    n_valid = np.sum(np.isfinite(work), axis=1).astype(np.intp)
    # NaNs sort to the end for float dtypes.
    sorted_w = np.sort(work, axis=1)
    rows = np.arange(nrow)
    out: list[np.ndarray] = []
    for p in probs:
        idx = np.maximum((n_valid - 1).astype(float) * float(p), 0.0)
        lo = np.floor(idx).astype(np.intp)
        hi = np.ceil(idx).astype(np.intp)
        lo = np.clip(lo, 0, nchan - 1)
        hi = np.clip(hi, 0, nchan - 1)
        w = idx - lo.astype(float)
        q = (1.0 - w) * sorted_w[rows, lo] + w * sorted_w[rows, hi]
        q = np.where(n_valid > 0, q, np.nan)
        out.append(q)
    return out


def flag_rfi_cflag_pass2(
    ms: str,
    *,
    threshold: float = 4.0,
    datacolumn: str = "DATA",
) -> dict[str, Any]:
    """Apply cflag Pass2: per-row dynamic amp + integrateSpectra channel flags.

    Cross-correlations only. Operates per polarisation (XX/YY). Writes ``FLAG``
    in place.
    """
    ms_path = Path(ms)
    if not ms_path.exists():
        raise FileNotFoundError(f"Measurement Set not found: {ms}")

    col = datacolumn.upper()
    t0 = time.perf_counter()
    with table(str(ms_path), readonly=False, ack=False) as tb:
        data_raw = tb.getcol(col)
        flags_raw = tb.getcol("FLAG")
        data, layout = _as_nrow_nchan_npol(data_raw)
        flags, _ = _as_nrow_nchan_npol(flags_raw)
        ant1 = np.asarray(tb.getcol("ANTENNA1"))
        ant2 = np.asarray(tb.getcol("ANTENNA2"))
        ddids = np.asarray(tb.getcol("DATA_DESC_ID"), dtype=int)

        cross = ant1 != ant2
        cross_idx = np.flatnonzero(cross)
        n_cross = int(cross_idx.size)
        _nchan, npol = data.shape[1], data.shape[2]
        new_flags = np.zeros_like(flags, dtype=bool)
        n_new_row = 0
        n_new_int = 0

        for pol in range(npol):
            amp = np.abs(data[:, :, pol])
            fl = flags[:, :, pol]
            cross_amp = amp[cross_idx]
            cross_fl = fl[cross_idx]
            # Pass2a: vectorized per-row spectrum dynamic bounds
            work = np.where(cross_fl | ~np.isfinite(cross_amp), np.nan, cross_amp.astype(float))
            with np.errstate(all="ignore"):
                med, q25, q75 = _row_quantiles(work, (0.50, 0.25, 0.75))
            sig = IQR_TO_SIGMA * (q75 - q25)
            n_valid = np.sum(np.isfinite(work), axis=1)
            # MAD fallback where IQR collapses (nearly flat spectra)
            mad_med = _row_quantiles(np.abs(work - med[:, None]), (0.50,))[0]
            mad_sig = 1.4826 * mad_med
            use_mad = (~np.isfinite(sig)) | (sig <= 0)
            sig = np.where(use_mad, mad_sig, sig)
            valid_row = (n_valid >= 8) & np.isfinite(sig) & (sig > 0)
            # Absolute floor for still-degenerate rows
            floor = np.maximum(np.abs(med) * 1e-6, 1e-6)
            sig = np.where(valid_row, sig, floor)
            valid_row = n_valid >= 8
            lo = med - threshold * sig
            hi = med + threshold * sig
            row_dyn = (
                valid_row[:, None]
                & np.isfinite(cross_amp)
                & ~cross_fl
                & ((cross_amp < lo[:, None]) | (cross_amp > hi[:, None]))
            )
            n_new_row += int(row_dyn.sum())
            new_flags[cross_idx, :, pol] |= row_dyn

            # Pass2b: integrateSpectra per DATA_DESC_ID (SPW) — scalar time×baseline
            # average, then flag hot channels for all rows in that DDID.
            for ddid in np.unique(ddids[cross_idx]):
                ddid = int(ddid)
                rows = cross_idx[ddids[cross_idx] == ddid]
                if rows.size == 0:
                    continue
                block = amp[rows].astype(float, copy=True)
                block_fl = fl[rows] | new_flags[rows, :, pol]
                block[block_fl | ~np.isfinite(block)] = np.nan
                chan_mask = integrate_spectra_mask(block, threshold=threshold)
                if not np.any(chan_mask):
                    continue
                add = chan_mask[None, :] & ~fl[rows] & ~new_flags[rows, :, pol]
                n_new_int += int(add.sum())
                new_flags[rows, :, pol] |= add

        flags_out = flags | new_flags
        tb.putcol("FLAG", _from_nrow_nchan_npol(flags_out, layout))

    elapsed = time.perf_counter() - t0
    cross_flags = flags_out[cross]
    frac = float(np.mean(cross_flags)) if cross_flags.size else 0.0
    summary = {
        "new_flags_row_dynamic": n_new_row,
        "new_flags_integrate_spectra": n_new_int,
        "new_flags": n_new_row + n_new_int,
        "flag_fraction_crosscorr": frac,
        "n_cross": n_cross,
        "elapsed_s": elapsed,
        "threshold": threshold,
        "layout": layout,
    }
    logger.info(
        "cflag Pass2: +%d row-dynamic, +%d integrateSpectra flags "
        "(cross-corr flagged %.2f%%) in %.1fs",
        n_new_row,
        n_new_int,
        100.0 * frac,
        elapsed,
    )
    return summary


def flag_rfi_cflag(
    ms: str,
    *,
    datacolumn: str = "data",
    threshold: float = 4.0,
    clip_sigma: float = 7.0,
) -> dict[str, Any]:
    """Run cflag Pass2 followed by mandatory Stage 2 MAD and Stage 3 extension."""
    timings: dict[str, float] = {}
    t_all = time.perf_counter()

    t0 = time.perf_counter()
    pass2 = flag_rfi_cflag_pass2(
        ms,
        threshold=threshold,
        datacolumn=datacolumn.upper(),
    )
    timings["pass2_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    stage2 = flag_residual_rfi_clip(ms, datacolumn=datacolumn, sigma=clip_sigma)
    timings["stage2_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    flag_extend(
        ms,
        flagnearfreq=True,
        flagneartime=True,
        extendpols=True,
        datacolumn=datacolumn,
    )
    timings["stage3_s"] = time.perf_counter() - t0

    timings["total_s"] = time.perf_counter() - t_all
    return {"pass2": pass2, "stage2": stage2, "timings": timings}
