# ruff: noqa: D103
"""Amplitude-clipping flagging helpers."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def CASAService(*args, **kwargs):
    from dsa110_continuum.calibration import flagging

    return flagging.CASAService(*args, **kwargs)


def flag_summary(*args, **kwargs):
    from dsa110_continuum.calibration import flagging

    return flagging.flag_summary(*args, **kwargs)


@contextmanager
def suppress_subprocess_stderr():
    from dsa110_continuum.calibration import flagging

    with flagging.suppress_subprocess_stderr():
        yield


def flag_clip_amplitude(
    ms_path: str,
    threshold_max: float = 0.5,
    threshold_min: float | None = None,
    datacolumn: str = "data",
) -> dict:
    """Flag data with amplitude outside a valid range to remove RFI and bad data.

        Uses CASA's flagdata with mode='clip' to identify and flag data points
        where the amplitude exceeds the maximum threshold or falls below the
        minimum threshold. This is effective for removing:
        - Strong RFI that can corrupt bandpass calibration (high amplitude)
        - Bad/noisy data that won't contribute to solutions (low amplitude)

        For DSA-110 data, typical amplitudes are ~0.05, while RFI can reach
        amplitudes of 100+. A max threshold of 0.5 typically catches 1-2% of data
        that is RFI-contaminated.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    threshold_max : float, optional
        Maximum amplitude threshold; data above this is flagged.
    Default: 0.5 (about 10x typical amplitude, catches strong RFI)
    threshold_min : Optional[float], optional
        Minimum amplitude threshold; data below this is flagged.
    Default: None (no minimum clipping). Set to e.g. 0.001 to remove
        very low amplitude data that may be noise or bad correlator output.
    datacolumn : str, optional
        Data column to check. Default: "data"

    Returns
    -------
        dict
        Dictionary with keys:
        - threshold_max: The max threshold used
        - threshold_min: The min threshold used (or None)
        - flagged_fraction: Fraction of data newly flagged by clipping
        - total_flagged_after: Total flag fraction after clipping
    """
    # Use CASAService for lazy import and environment protection
    service = CASAService()

    logger = logging.getLogger(__name__)

    min_val = threshold_min if threshold_min is not None else 0.0
    logger.info(f"Clipping amplitudes outside [{min_val}, {threshold_max}] in {ms_path}")

    # Get flag fraction before
    stats_before = flag_summary(ms_path)
    frac_before = stats_before.get("total_fraction_flagged", 0.0)

    # Use flagdata with mode='clip' to flag data outside valid range
    # clipminmax=[min, max] with clipoutside=True flags anything outside that range
    service.flagdata(
        vis=ms_path,
        mode="clip",
        datacolumn=datacolumn,
        clipminmax=[min_val, threshold_max],
        clipoutside=True,  # Flag data OUTSIDE the range [min, max]
        action="apply",
    )

    # Get flag fraction after
    stats_after = flag_summary(ms_path)
    frac_after = stats_after.get("total_fraction_flagged", 0.0)
    flagged_fraction = frac_after - frac_before

    logger.info(f"Amplitude clipping complete: {flagged_fraction * 100:.2f}% newly flagged")

    return {
        "threshold_max": threshold_max,
        "threshold_min": threshold_min,
        "flagged_fraction": flagged_fraction,
        "total_flagged_after": frac_after,
    }


def flag_residual_rfi_clip(
    ms: str,
    datacolumn: str = "data",
    sigma: float = 7.0,
    *,
    per_channel: bool = False,
) -> dict[str, Any]:
    """Flag residual RFI via MAD-based amplitude clipping on cross-correlations.

    AOFlagger's SumThreshold algorithm requires extended time–frequency structure
    to detect RFI.  DSA-110 drift-scan observations have only 24 time samples,
    leaving a significant residual (kurtosis >> 3).  This function applies a
    robust sigma-clip on the *unflagged* visibility amplitudes *after* AOFlagger
    has already run, catching the remaining broadband and short-baseline RFI that
    SumThreshold misses.

    Algorithm (per polarisation):
        1. Select cross-correlation rows only (``ANTENNA1 != ANTENNA2``).
        2. Compute amplitudes of unflagged visibilities in the selected data column.
        3. Estimate the centre and scale using the median and MAD
           (``σ_MAD = 1.4826 × median(|x − median(x)|)``).
        4. Flag any visibility whose amplitude exceeds ``median + sigma × σ_MAD``.

    When ``per_channel=False`` (default, recommended), a single threshold is
    computed from *all* unflagged cross-correlation amplitudes in each
    polarisation.  This corresponds to the "global 7σ clip" strategy validated
    on DSA-110 data (kurtosis 535 → 2.8 with only +1.2 % data cost).

    When ``per_channel=True``, the threshold is computed independently for each
    frequency channel, which is more sensitive to narrow-band RFI at the expense
    of a slightly higher flag fraction.

    Parameters
    ----------
    ms :
        Path to Measurement Set.
    datacolumn :
        Column to read visibilities from (default ``"data"``).
    sigma :
        Clipping threshold in MAD-based σ units (default 7.0).
    per_channel :
        If *True*, compute a per-channel threshold instead of a global one.

    Returns
    -------
    dict
        Summary with keys ``"new_flags"``, ``"total_flagged_pct"``,
        ``"pre_clip_flagged_pct"``, and ``"threshold"`` (or ``"thresholds"``).

    Raises
    ------
    FileNotFoundError
        If *ms* does not exist.

    """
    import numpy as np

    logger = logging.getLogger(__name__)

    ms_path = Path(ms)
    if not ms_path.exists():
        raise FileNotFoundError(f"Measurement Set not found: {ms}")

    try:
        from dsa110_continuum.adapters import casa_tables as ct
    except ImportError as exc:
        raise ImportError(
            "dsa110_continuum.adapters.casa_tables is required for flag_residual_rfi_clip"
        ) from exc

    MAD_TO_SIGMA = 1.4826  # MAD → Gaussian-σ conversion factor

    with ct.table(str(ms), readonly=False) as t:
        data = t.getcol(datacolumn.upper())     # (nrow, nchan, npol)
        flags = t.getcol("FLAG")                # (nrow, nchan, npol)
        ant1 = t.getcol("ANTENNA1")
        ant2 = t.getcol("ANTENNA2")

        cross = ant1 != ant2
        _nrow, nchan, npol = data.shape

        cross_data = data[cross]                # (n_cross, nchan, npol)
        cross_flags = flags[cross].copy()       # writable copy
        total_new = 0

        threshold_info: dict[str, Any] = {}

        for pol in range(npol):
            amp = np.abs(cross_data[:, :, pol])          # (n_cross, nchan)
            fl = cross_flags[:, :, pol]                   # (n_cross, nchan)

            if per_channel:
                # Per-channel thresholds
                thresholds = np.empty(nchan, dtype=np.float64)
                for ch in range(nchan):
                    unflagged = amp[:, ch][~fl[:, ch]]
                    if unflagged.size < 10:
                        thresholds[ch] = np.inf
                        continue
                    med = np.median(unflagged)
                    mad_sig = np.median(np.abs(unflagged - med)) * MAD_TO_SIGMA
                    thresholds[ch] = med + sigma * mad_sig

                new = (amp > thresholds[np.newaxis, :]) & ~fl
                threshold_info[f"pol{pol}_thresholds_median"] = float(
                    np.median(thresholds[np.isfinite(thresholds)])
                )
            else:
                # Global threshold across all channels
                unflagged = amp[~fl]
                if unflagged.size < 10:
                    logger.warning("Pol %d: too few unflagged visibilities for sigma-clip", pol)
                    continue
                med = float(np.median(unflagged))
                mad_sig = float(np.median(np.abs(unflagged - med))) * MAD_TO_SIGMA
                thresh = med + sigma * mad_sig
                threshold_info[f"pol{pol}_threshold"] = thresh
                threshold_info[f"pol{pol}_median"] = med
                threshold_info[f"pol{pol}_mad_sigma"] = mad_sig

                new = (amp > thresh) & ~fl

            n_new = int(new.sum())
            total_new += n_new
            cross_flags[:, :, pol] |= new
            logger.info(
                "Post-AOFlagger %s%.0fσ clip pol %d: %d new flags (%.3f%%)",
                "per-ch " if per_channel else "",
                sigma,
                pol,
                n_new,
                100.0 * n_new / amp.size if amp.size > 0 else 0.0,
            )

        # Write back only the cross-correlation flag rows
        full_flags = flags.copy()
        full_flags[cross] = cross_flags
        t.putcol("FLAG", full_flags)

    pre_pct = 100.0 * flags[cross].sum() / flags[cross].size
    post_pct = 100.0 * full_flags[cross].sum() / full_flags[cross].size

    result = {
        "new_flags": total_new,
        "pre_clip_flagged_pct": round(pre_pct, 4),
        "total_flagged_pct": round(post_pct, 4),
        **threshold_info,
    }
    logger.info(
        "Post-AOFlagger sigma-clip: %d new flags, %.2f%% → %.2f%% cross-corr flagged",
        total_new,
        pre_pct,
        post_pct,
    )
    return result


def flag_clip(
    ms: str,
    clipminmax: list[float],
    clipoutside: bool = True,
    correlation: str = "ABS_ALL",
    datacolumn: str = "data",
    channelavg: bool = False,
    timeavg: bool = False,
    chanbin: int | None = None,
    timebin: str | None = None,
) -> None:
    """Flag data outside specified amplitude thresholds.

    Flags visibility amplitudes that fall outside acceptable ranges.
    Useful for identifying extreme outliers, strong RFI, or systematic problems.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    clipminmax :
        [min, max] amplitude range in Jy
    clipoutside :
        If True, flag outside range; if False, flag inside range
    correlation :
        Correlation product ('ABS_ALL', 'RR', 'LL', etc.)
    datacolumn :
        Data column to use (default: 'data')
    channelavg :
        Average channels before clipping
    timeavg :
        Average time before clipping
    chanbin :
        Channel binning factor
    timebin :
        Time binning (e.g., '30s')
    """
    kwargs = {
        "vis": ms,
        "mode": "clip",
        "datacolumn": datacolumn,
        "clipminmax": clipminmax,
        "clipoutside": clipoutside,
        "correlation": correlation,
    }
    if channelavg or chanbin:
        kwargs["channelavg"] = channelavg
        if chanbin:
            kwargs["chanbin"] = chanbin
    if timeavg or timebin:
        kwargs["timeavg"] = timeavg
        if timebin:
            kwargs["timebin"] = timebin
    service = CASAService()
    with suppress_subprocess_stderr():
        service.flagdata(**kwargs)
