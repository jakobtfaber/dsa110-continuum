"""Cheap, fail-closed RFI preflight for DSA-110 drift-scan tiles.

The hard thresholds are anchored to the high-band RFI incident in tile
2026-07-13T11:59:19. Metrics use finite, currently unflagged
cross-correlations and map DATA_DESC_ID through DATA_DESCRIPTION before
grouping by spectral window.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np

HIGH_MID_RATIO_TRIGGER = 2.0
HIGH_BAND_FRAC_GT_1_TRIGGER = 0.08
INTEGRATION_RATIO_TRIGGER = 2.0
INTEGRATION_OCCUPANCY_TRIGGER = 0.25
MID_BAND_SPWS = (6, 7, 8, 9)
HIGH_BAND_SPWS = (14, 15)
MIN_VALID_SAMPLES_PER_SPW = 1_000
MIN_VALID_SAMPLES_PER_INTEGRATION = 100
MIN_VALID_INTEGRATIONS = 4
PREFLIGHT_POLICY_VERSION = "2026-07-13-highband-v1"

_HISTOGRAM_EDGES = np.concatenate(([0.0], np.geomspace(1e-6, 100.0, 4096), [np.inf]))


@dataclass(frozen=True)
class SpwStats:
    """Amplitude summaries for one spectral window."""

    median: float
    mad: float
    p99: float
    frac_gt_0_5: float
    frac_gt_1_0: float
    n_valid: int


@dataclass(frozen=True)
class PreflightDecision:
    """Pure threshold decision produced from per-SPW and per-time metrics."""

    triggered: bool
    reasons: tuple[str, ...]
    high_mid_ratio: float | None
    integration_trigger_fraction: float | None
    metrics_available: bool
    policy_version: str = PREFLIGHT_POLICY_VERSION


@dataclass(frozen=True)
class PreflightResult:
    """Measured preflight statistics and their fail-closed decision."""

    spw_stats: Mapping[int, SpwStats]
    integration_high_mid_ratios: np.ndarray
    decision: PreflightDecision


def evaluate_preflight_thresholds(
    spw_stats: Mapping[int, SpwStats],
    integration_high_mid_ratios: np.ndarray,
) -> PreflightDecision:
    """Evaluate the incident-derived hard thresholds without MS I/O.

    Missing or undersampled required metrics trigger the full chain. Conditional
    science processing may skip AOFlagger only when these metrics prove the tile
    quiet.
    """
    reasons: list[str] = []
    required = MID_BAND_SPWS + HIGH_BAND_SPWS
    missing = [
        spw
        for spw in required
        if spw not in spw_stats
        or spw_stats[spw].n_valid < MIN_VALID_SAMPLES_PER_SPW
        or not np.isfinite(spw_stats[spw].median)
    ]
    ratios = np.asarray(integration_high_mid_ratios, dtype=float)
    ratios = ratios[np.isfinite(ratios)]
    metrics_available = not missing and ratios.size >= MIN_VALID_INTEGRATIONS
    if missing:
        reasons.append(f"unavailable required SPW metrics: {missing}")
    if ratios.size < MIN_VALID_INTEGRATIONS:
        reasons.append(
            f"insufficient per-integration high/mid ratios: "
            f"{ratios.size} < {MIN_VALID_INTEGRATIONS}"
        )

    high_mid_ratio: float | None = None
    integration_fraction: float | None = None
    if not missing:
        mid = float(np.median([spw_stats[spw].median for spw in MID_BAND_SPWS]))
        if not np.isfinite(mid) or mid <= 0:
            metrics_available = False
            reasons.append("invalid mid-band denominator")
        else:
            high = max(spw_stats[14].median, spw_stats[15].median)
            high_mid_ratio = float(high / mid)
            if high_mid_ratio >= HIGH_MID_RATIO_TRIGGER:
                reasons.append(
                    f"high/mid ratio {high_mid_ratio:.3f} >= {HIGH_MID_RATIO_TRIGGER:.1f}"
                )

        high_frac = max(spw_stats[14].frac_gt_1_0, spw_stats[15].frac_gt_1_0)
        if high_frac >= HIGH_BAND_FRAC_GT_1_TRIGGER:
            reasons.append(
                f"high-band frac(|V|>1 Jy) {high_frac:.3f} >= {HIGH_BAND_FRAC_GT_1_TRIGGER:.2f}"
            )

    if ratios.size:
        integration_fraction = float(np.mean(ratios >= INTEGRATION_RATIO_TRIGGER))
        if integration_fraction >= INTEGRATION_OCCUPANCY_TRIGGER:
            reasons.append(
                f"integration ratio occupancy {integration_fraction:.3f} "
                f">= {INTEGRATION_OCCUPANCY_TRIGGER:.2f}"
            )

    return PreflightDecision(
        triggered=bool(reasons),
        reasons=tuple(reasons) if reasons else ("all hard thresholds quiet",),
        high_mid_ratio=high_mid_ratio,
        integration_trigger_fraction=integration_fraction,
        metrics_available=metrics_available,
    )


def _rows_first(values: np.ndarray, nrows: int) -> np.ndarray:
    """Normalize CASA/casacore row-axis layouts to rows first."""
    values = np.asarray(values)
    if values.shape[0] == nrows:
        return values
    if values.shape[-1] == nrows:
        return np.moveaxis(values, -1, 0)
    raise ValueError(f"Cannot identify row axis in shape {values.shape} for {nrows} rows")


def _histogram_quantile(histogram: np.ndarray, quantile: float) -> float:
    total = int(histogram.sum())
    if total == 0:
        return float("nan")
    index = int(np.searchsorted(np.cumsum(histogram), quantile * total, side="left"))
    index = min(index, len(_HISTOGRAM_EDGES) - 2)
    lo = _HISTOGRAM_EDGES[index]
    hi = _HISTOGRAM_EDGES[index + 1]
    if not np.isfinite(hi):
        return float(lo)
    return float((lo + hi) / 2.0)


def _selected_amplitudes(
    data: np.ndarray,
    flags: np.ndarray,
    cross_rows: np.ndarray,
) -> np.ndarray:
    selected_data = data[cross_rows]
    selected_flags = flags[cross_rows]
    amplitudes = np.abs(selected_data)
    good = (~selected_flags) & np.isfinite(amplitudes)
    return amplitudes[good]


def measure_rfi_preflight(
    ms_path: str | Path,
    *,
    datacolumn: str = "DATA",
    chunk_rows: int = 65_536,
) -> PreflightResult:
    """Measure chunked per-SPW and per-integration raw-amplitude statistics."""
    from dsa110_continuum.adapters.casa_tables import table

    ms_path = str(ms_path)
    with table(f"{ms_path}/DATA_DESCRIPTION", readonly=True, ack=False) as dd_table:
        dd_to_spw = np.asarray(dd_table.getcol("SPECTRAL_WINDOW_ID"), dtype=int)

    histograms: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    counts_gt_0_5: dict[int, int] = {}
    counts_gt_1_0: dict[int, int] = {}
    time_histograms: dict[tuple[int, float], np.ndarray] = {}

    with table(ms_path, readonly=True, ack=False) as main:
        nrows_total = int(main.nrows())
        for start in range(0, nrows_total, chunk_rows):
            nrows = min(chunk_rows, nrows_total - start)
            data = _rows_first(main.getcol(datacolumn, startrow=start, nrow=nrows), nrows)
            flags = _rows_first(main.getcol("FLAG", startrow=start, nrow=nrows), nrows)
            ant1 = np.asarray(main.getcol("ANTENNA1", startrow=start, nrow=nrows))
            ant2 = np.asarray(main.getcol("ANTENNA2", startrow=start, nrow=nrows))
            ddids = np.asarray(main.getcol("DATA_DESC_ID", startrow=start, nrow=nrows), dtype=int)
            times = np.asarray(main.getcol("TIME", startrow=start, nrow=nrows), dtype=float)
            spws = dd_to_spw[ddids]
            cross = ant1 != ant2

            for spw in np.unique(spws[cross]):
                spw = int(spw)
                spw_rows = cross & (spws == spw)
                amplitudes = _selected_amplitudes(data, flags, spw_rows)
                if amplitudes.size:
                    histograms.setdefault(spw, np.zeros(len(_HISTOGRAM_EDGES) - 1, dtype=np.int64))
                    histograms[spw] += np.histogram(amplitudes, bins=_HISTOGRAM_EDGES)[0]
                    counts[spw] = counts.get(spw, 0) + int(amplitudes.size)
                    counts_gt_0_5[spw] = counts_gt_0_5.get(spw, 0) + int(
                        np.count_nonzero(amplitudes > 0.5)
                    )
                    counts_gt_1_0[spw] = counts_gt_1_0.get(spw, 0) + int(
                        np.count_nonzero(amplitudes > 1.0)
                    )

                if spw in required_spws_for_time_metrics():
                    for timestamp in np.unique(times[spw_rows]):
                        rows = spw_rows & (times == timestamp)
                        time_amplitudes = _selected_amplitudes(data, flags, rows)
                        if time_amplitudes.size:
                            key = (spw, float(timestamp))
                            time_histograms.setdefault(
                                key, np.zeros(len(_HISTOGRAM_EDGES) - 1, dtype=np.int64)
                            )
                            time_histograms[key] += np.histogram(
                                time_amplitudes, bins=_HISTOGRAM_EDGES
                            )[0]

    medians = {spw: _histogram_quantile(hist, 0.5) for spw, hist in histograms.items()}
    deviation_histograms = {
        spw: np.zeros(len(_HISTOGRAM_EDGES) - 1, dtype=np.int64) for spw in histograms
    }
    with table(ms_path, readonly=True, ack=False) as main:
        nrows_total = int(main.nrows())
        for start in range(0, nrows_total, chunk_rows):
            nrows = min(chunk_rows, nrows_total - start)
            data = _rows_first(main.getcol(datacolumn, startrow=start, nrow=nrows), nrows)
            flags = _rows_first(main.getcol("FLAG", startrow=start, nrow=nrows), nrows)
            ant1 = np.asarray(main.getcol("ANTENNA1", startrow=start, nrow=nrows))
            ant2 = np.asarray(main.getcol("ANTENNA2", startrow=start, nrow=nrows))
            ddids = np.asarray(main.getcol("DATA_DESC_ID", startrow=start, nrow=nrows), dtype=int)
            spws = dd_to_spw[ddids]
            cross = ant1 != ant2
            for spw in np.unique(spws[cross]):
                spw = int(spw)
                amplitudes = _selected_amplitudes(data, flags, cross & (spws == spw))
                if amplitudes.size and spw in medians:
                    deviation_histograms[spw] += np.histogram(
                        np.abs(amplitudes - medians[spw]), bins=_HISTOGRAM_EDGES
                    )[0]

    spw_stats = {
        spw: SpwStats(
            median=medians[spw],
            mad=_histogram_quantile(deviation_histograms[spw], 0.5),
            p99=_histogram_quantile(histograms[spw], 0.99),
            frac_gt_0_5=counts_gt_0_5.get(spw, 0) / counts[spw],
            frac_gt_1_0=counts_gt_1_0.get(spw, 0) / counts[spw],
            n_valid=counts[spw],
        )
        for spw in histograms
        if counts.get(spw, 0) > 0
    }

    integration_ratios: list[float] = []
    timestamps = sorted({timestamp for _, timestamp in time_histograms})
    for timestamp in timestamps:
        per_spw = {
            spw: _histogram_quantile(time_histograms[(spw, timestamp)], 0.5)
            for spw in required_spws_for_time_metrics()
            if (spw, timestamp) in time_histograms
            and time_histograms[(spw, timestamp)].sum() >= MIN_VALID_SAMPLES_PER_INTEGRATION
        }
        if all(spw in per_spw for spw in MID_BAND_SPWS + HIGH_BAND_SPWS):
            mid = float(np.median([per_spw[spw] for spw in MID_BAND_SPWS]))
            if np.isfinite(mid) and mid > 0:
                integration_ratios.append(max(per_spw[14], per_spw[15]) / mid)

    ratio_array = np.asarray(integration_ratios, dtype=float)
    decision = evaluate_preflight_thresholds(spw_stats, ratio_array)
    return PreflightResult(spw_stats, ratio_array, decision)


def required_spws_for_time_metrics() -> tuple[int, ...]:
    """Return SPWs needed for the high/mid per-integration trigger."""
    return MID_BAND_SPWS + HIGH_BAND_SPWS
