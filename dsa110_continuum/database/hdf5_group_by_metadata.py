# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# infrastructure/database/hdf5_group_by_metadata.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 5).
"""
Metadata-based subband grouping for DSA-110.

Groups HDF5 subband files by reading ``time_array[0]`` from each file's
HDF5 header.  Because all 16 subbands in an observation share **bit-identical**
``time_array`` values, an exact float64 match is sufficient — no fuzzy
clustering, gap thresholds, or dynamic programming needed.

Additionally computes the RA range (``ra_min_deg``, ``ra_max_deg``) for each
group from the time_array min/max values using the GMST approximation
(``RA ≈ LST`` for DSA-110 drift-scan data).  This enables direct cross-matching
against calibrator source positions to determine which group contains a
calibrator transit without any database lookups.

Cost Model
----------
- ``time_array[0]`` read: ~1 ms/file (single HDF5 scalar slice)
- ``time_array[-1]`` read: ~1 ms/file (for RA range computation)
- Filename regex: ~2 µs/file (subband index extraction)
- GMST formula: ~0.04 ms for all groups combined (vectorized numpy)
- Total for 2,894 files: ~55 s (dominated by HDF5 I/O, negligible for 24h dataset)

This replaces the DP-based ``group_subbands_optimal()`` as the authoritative
production grouping method, providing zero-ambiguity grouping with RA bounds
for calibrator transit matching.
"""

from __future__ import annotations

import logging
import os
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from dsa110_continuum.utils.constants import DSA110_LONGITUDE
from dsa110_continuum.database.hdf5_index import (
    GroupingMetrics,
    GroupingResult,
    SubbandGroup,
    parse_subband_filename,
)

logger = logging.getLogger(__name__)

# Expected number of subbands per observation.
EXPECTED_SUBBANDS = 16

# Each observation spans 24 integrations × 12.885s ≈ 309s ≈ 1.24° of RA.
# A source transit occurs when its RA falls within the group's RA window.
# DSA-110 primary beam FWHM ≈ 3.5° at 1.4 GHz, so the transit peak is
# well-resolved by the ~1.24° observation window.


# =============================================================================
# Fast LST/RA computation
# =============================================================================


def jd_to_ra_deg(
    jd: np.ndarray | float,
    longitude_deg: float = DSA110_LONGITUDE,
) -> np.ndarray | float:
    """Convert Julian Date(s) to beam-center RA in degrees.

    For DSA-110 drift-scan observations, RA = LST (Local Sidereal Time).
    Uses the IAU GMST formula (accurate to ~7 arcsec, negligible vs the
    3.5° beam FWHM).

    Parameters
    ----------
    jd : float or np.ndarray
        Julian Date(s), UTC scale.
    longitude_deg : float
        Observatory east longitude in degrees (default: DSA-110 = -118.2817°).

    Returns
    -------
    float or np.ndarray
        Right Ascension in degrees [0, 360).
    """
    du = np.asarray(jd, dtype=np.float64) - 2451545.0
    gmst_deg = (280.46061837 + 360.98564736629 * du) % 360.0
    ra = (gmst_deg + longitude_deg) % 360.0
    return float(ra) if np.ndim(jd) == 0 else ra


# =============================================================================
# Per-file HDF5 reader
# =============================================================================


def _read_time_bounds(path: str | Path) -> tuple[float, float] | None:
    """Read ``time_array[0]`` and ``time_array[-1]`` from an HDF5 file.

    Returns ``(jd_first, jd_last)`` or ``None`` on error.
    Each read is a single scalar slice (~1 ms), not the full 111k-element array.
    """
    try:
        with h5py.File(str(path), "r") as fh:
            ta = fh["Header/time_array"]
            jd_first = float(ta[0])
            jd_last = float(ta[-1])
            return (jd_first, jd_last)
    except Exception as exc:
        logger.warning("Could not read time_array from %s: %s", path, exc)
        return None


# =============================================================================
# Enhanced SubbandGroup with RA bounds
# =============================================================================


@dataclass
class SubbandGroupWithRA(SubbandGroup):
    """SubbandGroup extended with RA bounds derived from observation time.

    Attributes
    ----------
    jd_start : float
        Julian Date of the first integration (``time_array[0]``).
    jd_end : float
        Julian Date of the last integration (``time_array[-1]``).
    ra_min_deg : float
        RA of beam center at observation start, in degrees [0, 360).
    ra_max_deg : float
        RA of beam center at observation end, in degrees [0, 360).
    ra_center_deg : float
        RA of beam center at observation midpoint.
    dec_deg : float or None
        Declination in degrees (if read from header).
    """

    jd_start: float = 0.0
    jd_end: float = 0.0
    ra_min_deg: float = 0.0
    ra_max_deg: float = 0.0
    ra_center_deg: float = 0.0
    dec_deg: float | None = None

    @property
    def ra_span_deg(self) -> float:
        """RA span in degrees, handling 360° wrap-around."""
        delta = self.ra_max_deg - self.ra_min_deg
        if delta < 0:
            delta += 360.0
        return delta

    def contains_ra(self, ra_deg: float, margin_deg: float = 0.0) -> bool:
        """Check whether a source RA falls within this group's RA window.

        Parameters
        ----------
        ra_deg : float
            Source RA in degrees.
        margin_deg : float
            Extra margin on each side (e.g., half-beam-width for beam crossing).

        Returns
        -------
        bool
            True if the source RA is within [ra_min - margin, ra_max + margin].
        """
        lo = (self.ra_min_deg - margin_deg) % 360.0
        hi = (self.ra_max_deg + margin_deg) % 360.0
        ra = ra_deg % 360.0
        if lo <= hi:
            return lo <= ra <= hi
        else:
            # Wraps around 0°/360°
            return ra >= lo or ra <= hi

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = super().to_dict()
        d.update({
            "jd_start": self.jd_start,
            "jd_end": self.jd_end,
            "ra_min_deg": self.ra_min_deg,
            "ra_max_deg": self.ra_max_deg,
            "ra_center_deg": self.ra_center_deg,
            "ra_span_deg": self.ra_span_deg,
            "dec_deg": self.dec_deg,
        })
        return d


# =============================================================================
# Core grouping function
# =============================================================================


def group_subbands_by_metadata(
    file_paths: list[str | Path],
    *,
    expected_subbands: int = EXPECTED_SUBBANDS,
    read_dec: bool = False,
) -> GroupingResult:
    """Group subband files by exact ``time_array[0]`` match with RA bounds.

    Algorithm
    ---------
    1. For each file, read ``time_array[0]`` and ``time_array[-1]``
       (two scalar HDF5 slices, ~2 ms total).
    2. Extract subband index from filename (~2 µs).
    3. Group files by exact ``time_array[0]`` value — all 16 subbands share
       bit-identical time arrays, so this is an exact-match dict key.
    4. For each group, compute RA bounds from the JD extremes using fast GMST.
    5. Return ``GroupingResult`` with ``SubbandGroupWithRA`` objects.

    Parameters
    ----------
    file_paths : list[str | Path]
        HDF5 file paths to group.
    expected_subbands : int
        Subbands expected per complete observation (default: 16).
    read_dec : bool
        If True, also read ``phase_center_dec`` from the first file
        in each group (adds ~1 ms/group). Default: False.

    Returns
    -------
    GroupingResult
        Groups (as ``SubbandGroupWithRA``) and metrics.
    """
    t_wall = _time.perf_counter()

    # Phase 1: Read time bounds and parse filenames.
    # {jd_first: [(path, sb_idx, jd_last), ...]}
    jd_groups: dict[float, list[tuple[str, int, float]]] = defaultdict(list)
    n_read_errors = 0
    n_parse_errors = 0

    for fpath in file_paths:
        fpath_str = str(fpath)
        basename = os.path.basename(fpath_str)

        parsed = parse_subband_filename(basename)
        if parsed is None:
            n_parse_errors += 1
            logger.warning("Could not parse filename: %s", basename)
            continue
        _ts_iso, sb_idx = parsed

        bounds = _read_time_bounds(fpath_str)
        if bounds is None:
            n_read_errors += 1
            continue
        jd_first, jd_last = bounds

        jd_groups[jd_first].append((fpath_str, sb_idx, jd_last))

    # Phase 2: Build groups with RA bounds.
    expected_set = set(range(expected_subbands))
    groups: list[SubbandGroupWithRA] = []
    total_missing = 0
    total_duplicates = 0

    # Collect all unique JD pairs for vectorized RA computation.
    sorted_jds = sorted(jd_groups.keys())
    if sorted_jds:
        jd_starts = np.array(sorted_jds)
        # For jd_end, take max jd_last within each group (should be identical
        # across subbands, but max() is a safe guard).
        jd_ends = np.array([
            max(jd_last for _, _, jd_last in jd_groups[jd])
            for jd in sorted_jds
        ])
        jd_mids = (jd_starts + jd_ends) / 2.0

        # Vectorized RA computation — ~0.04 ms for all groups combined.
        ra_starts = jd_to_ra_deg(jd_starts)
        ra_ends = jd_to_ra_deg(jd_ends)
        ra_centers = jd_to_ra_deg(jd_mids)
    else:
        ra_starts = ra_ends = ra_centers = np.array([])

    for i, jd in enumerate(sorted_jds):
        members = jd_groups[jd]
        files_sorted = sorted(members, key=lambda x: x[1])  # sort by subband
        sb_indices = [sb for _, sb, _ in files_sorted]
        sb_set = set(sb_indices)

        missing = sorted(expected_set - sb_set)
        duplicates = len(sb_indices) - len(sb_set)

        # Representative time from first file's filename.
        parsed = parse_subband_filename(os.path.basename(files_sorted[0][0]))
        rep_time = parsed[0] if parsed else ""

        # Optionally read Dec from the first file in the group.
        dec_deg = None
        if read_dec:
            try:
                with h5py.File(files_sorted[0][0], "r") as fh:
                    ek = fh["Header/extra_keywords"]
                    if "phase_center_dec" in ek:
                        dec_deg = float(np.degrees(float(ek["phase_center_dec"][()])))
            except Exception:
                pass

        group = SubbandGroupWithRA(
            files=[f for f, _, _ in files_sorted],
            representative_time=rep_time,
            num_subbands=len(sb_set),
            missing_subbands=missing,
            duplicate_count=duplicates,
            time_span_s=(jd_ends[i] - jd_starts[i]) * 86400.0,
            jd_start=float(jd_starts[i]),
            jd_end=float(jd_ends[i]),
            ra_min_deg=float(ra_starts[i]),
            ra_max_deg=float(ra_ends[i]),
            ra_center_deg=float(ra_centers[i]),
            dec_deg=dec_deg,
        )

        groups.append(group)
        total_missing += len(missing)
        total_duplicates += duplicates

    elapsed = _time.perf_counter() - t_wall
    n_complete = sum(1 for g in groups if g.is_complete)
    total_files = sum(len(g) for g in groups)

    metrics = GroupingMetrics(
        sigma=0.0,  # No jitter — exact match.
        W_max=0.0,
        status="metadata_exact_match",
        total_files=total_files,
        total_groups=len(groups),
        complete_groups=n_complete,
        fraction_complete=n_complete / len(groups) if groups else 0.0,
        total_missing=total_missing,
        total_duplicates=total_duplicates,
    )

    logger.info(
        "Metadata grouping: %d files → %d groups (%d complete) in %.2fs"
        "  [%d read errors, %d parse errors]",
        len(file_paths),
        len(groups),
        n_complete,
        elapsed,
        n_read_errors,
        n_parse_errors,
    )

    return GroupingResult(groups=groups, metrics=metrics)


def group_subbands_from_db_rows(
    rows: list[tuple[str, str, int, float]],
    *,
    expected_subbands: int = EXPECTED_SUBBANDS,
    read_dec: bool = False,
) -> GroupingResult:
    """Build ``SubbandGroupWithRA`` objects from pre-loaded database rows.

    This is the fast path for ``query_subband_groups()`` when the database
    already has ``jd_start`` populated.  Instead of opening every HDF5 file
    to read ``time_array[0]``, it uses the value stored in the DB.  Only one
    file per group is opened (to read ``time_array[-1]`` for the RA end bound).

    Parameters
    ----------
    rows : list of (path, timestamp_iso, subband_num, jd_start)
        Pre-fetched rows from ``hdf5_files``.
    expected_subbands : int
        Subbands expected per complete observation (default: 16).
    read_dec : bool
        If True, also read ``phase_center_dec`` from one file per group.

    Returns
    -------
    GroupingResult
    """
    t_wall = _time.perf_counter()

    # Phase 1: Group by jd_start (already known).
    jd_groups: dict[float, list[tuple[str, int]]] = defaultdict(list)
    for path, _ts_iso, sb_idx, jd_start in rows:
        jd_groups[jd_start].append((path, sb_idx))

    # Phase 2: Build groups with RA bounds.
    expected_set = set(range(expected_subbands))
    groups: list[SubbandGroupWithRA] = []
    total_missing = 0
    total_duplicates = 0

    sorted_jds = sorted(jd_groups.keys())
    if not sorted_jds:
        return GroupingResult(
            groups=[],
            metrics=GroupingMetrics(
                sigma=0.0, W_max=0.0, status="no_files",
                total_files=0, total_groups=0,
            ),
        )

    # Read jd_end from one representative file per group (~1 ms each).
    jd_ends_list: list[float] = []
    for jd in sorted_jds:
        members = jd_groups[jd]
        rep_path = min(members, key=lambda x: x[1])[0]  # sb00 preferred
        bounds = _read_time_bounds(rep_path)
        jd_ends_list.append(bounds[1] if bounds else jd)

    jd_starts_arr = np.array(sorted_jds)
    jd_ends_arr = np.array(jd_ends_list)
    jd_mids = (jd_starts_arr + jd_ends_arr) / 2.0

    ra_starts = jd_to_ra_deg(jd_starts_arr)
    ra_ends = jd_to_ra_deg(jd_ends_arr)
    ra_centers = jd_to_ra_deg(jd_mids)

    for i, jd in enumerate(sorted_jds):
        members = jd_groups[jd]
        files_sorted = sorted(members, key=lambda x: x[1])
        sb_indices = [sb for _, sb in files_sorted]
        sb_set = set(sb_indices)

        missing = sorted(expected_set - sb_set)
        duplicates = len(sb_indices) - len(sb_set)

        parsed = parse_subband_filename(os.path.basename(files_sorted[0][0]))
        rep_time = parsed[0] if parsed else ""

        dec_deg = None
        if read_dec:
            try:
                with h5py.File(files_sorted[0][0], "r") as fh:
                    ek = fh["Header/extra_keywords"]
                    if "phase_center_dec" in ek:
                        dec_deg = float(np.degrees(float(ek["phase_center_dec"][()])))
            except Exception:
                pass

        group = SubbandGroupWithRA(
            files=[f for f, _ in files_sorted],
            representative_time=rep_time,
            num_subbands=len(sb_set),
            missing_subbands=missing,
            duplicate_count=duplicates,
            time_span_s=(jd_ends_arr[i] - jd_starts_arr[i]) * 86400.0,
            jd_start=float(jd_starts_arr[i]),
            jd_end=float(jd_ends_arr[i]),
            ra_min_deg=float(ra_starts[i]),
            ra_max_deg=float(ra_ends[i]),
            ra_center_deg=float(ra_centers[i]),
            dec_deg=dec_deg,
        )
        groups.append(group)
        total_missing += len(missing)
        total_duplicates += duplicates

    elapsed = _time.perf_counter() - t_wall
    n_complete = sum(1 for g in groups if g.is_complete)
    total_files = sum(len(g) for g in groups)

    metrics = GroupingMetrics(
        sigma=0.0,
        W_max=0.0,
        status="db_jd_start",
        total_files=total_files,
        total_groups=len(groups),
        complete_groups=n_complete,
        fraction_complete=n_complete / len(groups) if groups else 0.0,
        total_missing=total_missing,
        total_duplicates=total_duplicates,
    )

    logger.info(
        "DB-accelerated grouping: %d files → %d groups (%d complete) in %.2fs"
        "  [%d jd_end reads]",
        total_files, len(groups), n_complete, elapsed, len(sorted_jds),
    )

    return GroupingResult(groups=groups, metrics=metrics)


# =============================================================================
# Calibrator transit matching
# =============================================================================


def find_transit_group(
    groups: list[SubbandGroupWithRA],
    source_ra_deg: float,
    beam_radius_deg: float = 1.75,
) -> SubbandGroupWithRA | None:
    """Find the group whose RA window contains a source transit.

    Parameters
    ----------
    groups : list[SubbandGroupWithRA]
        Groups from ``group_subbands_by_metadata()``.
    source_ra_deg : float
        RA of the source in degrees.
    beam_radius_deg : float
        Half the primary beam FWHM (default: 1.75° for DSA-110 at 1.4 GHz).
        Groups within this margin of the source RA are considered matches.

    Returns
    -------
    SubbandGroupWithRA or None
        The group closest to the transit peak, or None if no group matches.
    """
    candidates: list[tuple[float, SubbandGroupWithRA]] = []

    for group in groups:
        if group.contains_ra(source_ra_deg, margin_deg=beam_radius_deg):
            # Angular distance from source to group center.
            delta = abs(group.ra_center_deg - source_ra_deg)
            if delta > 180:
                delta = 360 - delta
            candidates.append((delta, group))

    if not candidates:
        return None

    # Return the group whose center is closest to the source RA.
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def find_all_transit_groups(
    groups: list[SubbandGroupWithRA],
    source_ra_deg: float,
    beam_radius_deg: float = 1.75,
) -> list[SubbandGroupWithRA]:
    """Find all groups whose RA window overlaps a source position.

    Like ``find_transit_group`` but returns all matches sorted by proximity,
    not just the closest.  Useful for selecting multiple observations around
    a calibrator transit (e.g., before/after transit for bandpass stability).

    Parameters
    ----------
    groups : list[SubbandGroupWithRA]
        Groups from ``group_subbands_by_metadata()``.
    source_ra_deg : float
        RA of the source in degrees.
    beam_radius_deg : float
        Half the primary beam FWHM (default: 1.75°).

    Returns
    -------
    list[SubbandGroupWithRA]
        Matching groups sorted by angular distance from source RA.
    """
    candidates: list[tuple[float, SubbandGroupWithRA]] = []

    for group in groups:
        if group.contains_ra(source_ra_deg, margin_deg=beam_radius_deg):
            delta = abs(group.ra_center_deg - source_ra_deg)
            if delta > 180:
                delta = 360 - delta
            candidates.append((delta, group))

    candidates.sort(key=lambda x: x[0])
    return [g for _, g in candidates]
