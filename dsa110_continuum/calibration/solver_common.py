"""Shared helpers for CASA calibration solvers."""

# ruff: noqa

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

from dsa110_continuum.utils import timed

from dsa110_continuum.calibration.casa_service import CASAService
from dsa110_continuum.conversion.merge_spws import get_spw_count

logger = logging.getLogger(__name__)

try:
    from dsa110_continuum.adapters import casa_tables as _casatables  # type: ignore

    table = _casatables.table  # noqa: N816
    _CASA_TABLES_IMPORT_ERROR: ImportError | None = None
except ImportError as _exc:
    _casatables = None
    _CASA_TABLES_IMPORT_ERROR = _exc

    def table(*args: Any, **kwargs: Any):  # noqa: N816
        raise ImportError(
            "dsa110_continuum.adapters.casa_tables is unavailable, so CASA table "
            "access is not possible. Run inside the casa6 conda environment "
            "(/opt/miniforge/envs/casa6/bin/python)."
        ) from _CASA_TABLES_IMPORT_ERROR


def _call_gaincal(**kwargs) -> None:
    """Call gaincal task via CASAService."""
    service = CASAService()
    service.gaincal(**kwargs)


def _call_gaincal_with_progress(
    stage_name: str,
    ms: str,
    caltable: str,
    **kwargs,
) -> None:
    """Call gaincal with progress monitoring.

    Parameters
    ----------
    stage_name :
        Human-readable name for progress display (e.g., "Delay solve")
    ms :
        Path to Measurement Set
    caltable :
        Path to output calibration table
    **kwargs :
        Arguments to pass to gaincal
    """
    from dsa110_continuum.utils.progress import StageProgressMonitor, estimate_calibration_time

    # Get MS info for progress estimation
    try:
        with table(ms, ack=False) as t:
            n_rows = t.nrows()
        with table(f"{ms}::SPECTRAL_WINDOW", ack=False) as tspw:
            n_spws = tspw.nrows()
        with table(f"{ms}::ANTENNA", ack=False) as tant:
            n_ant = tant.nrows()
    except Exception:
        n_rows, n_spws, n_ant = 1_000_000, 16, 110

    # Estimate expected runtime (gaincal is generally faster than bandpass)
    estimated_seconds = estimate_calibration_time(n_rows, n_spws, n_ant) * 0.5

    # Create progress monitor
    monitor = StageProgressMonitor(
        stage_name,
        output_path=caltable,
        poll_interval=5.0,
        estimated_seconds=estimated_seconds,
    )
    monitor.set_context(rows=n_rows, SPWs=n_spws, antennas=n_ant)

    service = CASAService()
    with monitor:
        service.gaincal(vis=ms, caltable=caltable, **kwargs)


def _get_caltable_spw_count(caltable_path: str) -> int | None:
    """Get the number of unique spectral windows in a calibration table.

    Parameters
    ----------
    caltable_path :
        Path to calibration table

    Returns
    -------
        Number of unique SPWs, or None if unable to read

    """
    import numpy as np  # type: ignore[import]

    # use module-level table

    try:
        with table(caltable_path, readonly=True) as tb:
            if "SPECTRAL_WINDOW_ID" not in tb.colnames():
                return None
            spw_ids = tb.getcol("SPECTRAL_WINDOW_ID")
            return len(np.unique(spw_ids))
    except (OSError, RuntimeError, KeyError):
        return None


# QA thresholds for calibration quality assessment (Issue #5 fix)
QA_SNR_MIN_THRESHOLD = 3.0  # Minimum acceptable mean SNR
QA_SNR_WARN_THRESHOLD = 10.0  # SNR below this triggers warning
QA_FLAGGED_MAX_THRESHOLD = 0.5  # Maximum acceptable flagged fraction
QA_FLAGGED_WARN_THRESHOLD = 0.2  # Flagged fraction above this triggers warning
QA_MIN_ANTENNAS = 10  # Minimum antennas for valid calibration


def _extract_quality_metrics(
    caltable_path: str,
    *,
    snr_min: float = QA_SNR_MIN_THRESHOLD,
    snr_warn: float = QA_SNR_WARN_THRESHOLD,
    flagged_max: float = QA_FLAGGED_MAX_THRESHOLD,
    flagged_warn: float = QA_FLAGGED_WARN_THRESHOLD,
    min_antennas: int = QA_MIN_ANTENNAS,
) -> dict[str, Any] | None:
    """Extract quality metrics from a calibration table with QA assessment.

    This function fixes Issue #5: No calibration QA before registration.
    It extracts metrics AND performs quality assessment, adding qa_passed
    and any issues/warnings to the metrics dict.

    Parameters
    ----------
    caltable_path :
        Path to calibration table
    snr_min :
        Minimum acceptable mean SNR (default: 5.0)
    snr_warn :
        SNR threshold for warnings (default: 10.0)
    flagged_max :
        Maximum acceptable flagged fraction (default: 0.5)
    flagged_warn :
        Flagged fraction threshold for warnings (default: 0.2)
    min_antennas :
        Minimum number of antennas (default: 10)

    Returns
    -------
        Dictionary with quality metrics (SNR, flagged_fraction, etc.),
        qa_passed (bool), and any issues/warnings. Returns None on read error.

    """
    import time

    import numpy as np  # type: ignore[import]

    try:
        with table(caltable_path, readonly=True) as tb:
            metrics: dict[str, Any] = {
                "qa_passed": True,  # Assume pass until proven otherwise
                "issues": [],
                "warnings": [],
                "assessed_at": time.time(),
            }

            # Number of solutions
            nrows = tb.nrows()
            metrics["n_solutions"] = nrows

            if nrows == 0:
                metrics["qa_passed"] = False
                metrics["issues"].append("Calibration table has zero solutions")
                return metrics

            # Check for FLAG column
            if "FLAG" in tb.colnames():
                flags = tb.getcol("FLAG")
                if flags.size > 0:
                    flagged_count = np.sum(flags)
                    total_count = flags.size
                    flagged_fraction = float(flagged_count / total_count)
                    metrics["flagged_fraction"] = flagged_fraction

                    # QA check: flagged fraction
                    if flagged_fraction > flagged_max:
                        metrics["qa_passed"] = False
                        metrics["issues"].append(
                            f"Flagged fraction too high: {flagged_fraction:.1%} "
                            f"(max: {flagged_max:.1%})"
                        )
                    elif flagged_fraction > flagged_warn:
                        metrics["warnings"].append(f"High flagged fraction: {flagged_fraction:.1%}")

            # Check for SNR column
            if "SNR" in tb.colnames():
                snr = tb.getcol("SNR")
                if snr.size > 0:
                    snr_flat = snr.flatten()
                    snr_valid = snr_flat[~np.isnan(snr_flat)]
                    if len(snr_valid) > 0:
                        snr_mean = float(np.mean(snr_valid))
                        snr_median = float(np.median(snr_valid))
                        snr_min_val = float(np.min(snr_valid))
                        snr_max_val = float(np.max(snr_valid))

                        metrics["snr_mean"] = snr_mean
                        metrics["snr_median"] = snr_median
                        metrics["snr_min"] = snr_min_val
                        metrics["snr_max"] = snr_max_val

                        # QA check: mean SNR
                        if snr_mean < snr_min:
                            metrics["qa_passed"] = False
                            metrics["issues"].append(
                                f"Mean SNR too low: {snr_mean:.1f} (min: {snr_min:.1f})"
                            )
                        elif snr_mean < snr_warn:
                            metrics["warnings"].append(f"Low mean SNR: {snr_mean:.1f}")

            # Number of antennas
            if "ANTENNA1" in tb.colnames():
                ant1 = tb.getcol("ANTENNA1")
                unique_ants = np.unique(ant1)
                n_antennas = len(unique_ants)
                metrics["n_antennas"] = n_antennas

                # QA check: minimum antennas
                if n_antennas < min_antennas:
                    metrics["qa_passed"] = False
                    metrics["issues"].append(
                        f"Too few antennas: {n_antennas} (min: {min_antennas})"
                    )

            # Number of spectral windows
            if "SPECTRAL_WINDOW_ID" in tb.colnames():
                spw_ids = tb.getcol("SPECTRAL_WINDOW_ID")
                unique_spws = np.unique(spw_ids)
                metrics["n_spws"] = len(unique_spws)

            # Clean up empty lists
            if not metrics["issues"]:
                del metrics["issues"]
            if not metrics["warnings"]:
                del metrics["warnings"]

            return metrics

    except Exception as e:
        logger.warning(f"Failed to extract quality metrics from {caltable_path}: {e}")
        return None


def _track_calibration_provenance(
    ms_path: str,
    caltable_path: str,
    task_name: str,
    params: dict[str, Any],
    registry_db: str | None = None,
) -> None:
    """Track calibration provenance after successful solve.

        This function captures and stores provenance information (source MS,
        solver command, version, parameters, quality metrics) for a calibration table.

    Parameters
    ----------
    ms_path : str
        Path to the input MS that generated this caltable.
    caltable_path : str
        Path to the calibration table.
    task_name : str
        CASA task name used (e.g., "gaincal", "bandpass").
    params : dict[str, Any]
        Parameters used in the calibration task.
    registry_db : str or None, optional
        Optional path to registry database. Default is None.

    Returns
    -------
        None
    """
    try:
        from pathlib import Path as PathLib

        from dsa110_contimg.infrastructure.database.provenance import track_calibration_provenance

        # Use CASAService for version and command string
        service = CASAService()
        casa_version = service.get_version()

        # Build command string
        command_str = service.build_command_string(task_name, params)

        # Extract quality metrics
        quality_metrics = _extract_quality_metrics(caltable_path)

        # Determine registry DB path (unified pipeline database)
        if registry_db is None:
            # Use unified pipeline database
            registry_db_path = PathLib(
                os.environ.get(
                    "PIPELINE_DB",
                    os.environ.get(
                        "CAL_REGISTRY_DB",  # Legacy fallback
                        os.environ.get(
                            "PIPELINE_DB", "/data/dsa110-contimg/state/db/pipeline.sqlite3"
                        ),
                    ),
                )
            )
        else:
            registry_db_path = PathLib(registry_db)

        # Track provenance
        track_calibration_provenance(
            registry_db=registry_db_path,
            ms_path=ms_path,
            caltable_path=caltable_path,
            params=params,
            metrics=quality_metrics,
            solver_command=command_str,
            solver_version=casa_version,
        )

        logger.debug(
            f"Tracked provenance for {caltable_path} (source: {ms_path}, version: {casa_version})"
        )

    except Exception as e:
        # Don't fail calibration if provenance tracking fails
        logger.warning(
            f"Failed to track provenance for {caltable_path}: {e}. "
            f"Calibration succeeded but provenance not recorded."
        )


def _determine_spwmap_for_bptables(
    bptables: list[str],
    ms_path: str,
) -> list[int] | None:
    """Determine spwmap parameter for bandpass tables when combine_spw was used.

    When a bandpass table is created with combine_spw=True, it contains solutions
    only for SPW=0 (the aggregate SPW). When applying this table during gain
    calibration, we need to map all MS SPWs to SPW 0 in the bandpass table.

    Parameters
    ----------
    bptables :
        List of bandpass table paths
    ms_path :
        Path to Measurement Set
    bptables: List[str] :

    Returns
    -------
        List of SPW mappings [0, 0, 0, ...] if needed, or None if not needed.
        The length of the list equals the number of SPWs in the MS.

    """
    if not bptables:
        return None

    # Get number of SPWs in MS
    n_ms_spw = get_spw_count(ms_path)
    if n_ms_spw is None or n_ms_spw <= 1:
        return None

    # Check if any bandpass table has only 1 SPW (indicating combine_spw was used)
    for bptable in bptables:
        n_bp_spw = _get_caltable_spw_count(bptable)
        logger.debug(
            f"Checking table {os.path.basename(bptable)}: {n_bp_spw} SPW(s), MS has {n_ms_spw} SPWs"
        )
        if n_bp_spw == 1:
            # This bandpass table was created with combine_spw=True
            # Map all MS SPWs to SPW 0 in the bandpass table
            logger.info(
                f"Detected calibration table {os.path.basename(bptable)} has only 1 SPW (from combine_spw), "
                f"while MS has {n_ms_spw} SPWs. Setting spwmap to map all MS SPWs to SPW 0."
            )
            return [0] * n_ms_spw

    return None


def _validate_solve_success(caltable_path: str, refant: int | str | None = None) -> None:
    """Validate that a calibration solve completed successfully.

    This ensures we follow "measure twice, cut once" - verify solutions exist
    immediately after each solve completes, before proceeding to the next step.

    Parameters
    ----------
    caltable_path :
        Path to calibration table
    refant :
        Optional reference antenna ID to verify has solutions

    Raises
    ------
    RuntimeError
        If table doesn't exist, has no solutions, or refant missing

    """
    # use module-level table

    # Verify table exists
    if not os.path.exists(caltable_path):
        raise RuntimeError(f"Calibration solve failed: table was not created: {caltable_path}")

    # Verify table has solutions
    try:
        with table(caltable_path, readonly=True) as tb:
            if tb.nrows() == 0:
                raise RuntimeError(
                    f"Calibration solve failed: table has no solutions: {caltable_path}"
                )

            # Verify refant has solutions if provided
            if refant is not None:
                # Handle comma-separated refant string (e.g., "103,111,113,115,104")
                # Use the first antenna in the chain for validation
                if isinstance(refant, str):
                    if "," in refant:
                        # Comma-separated list: use first antenna
                        refant_str = refant.split(",")[0].strip()
                        refant_int = int(refant_str)
                    else:
                        # Single antenna ID as string
                        refant_int = int(refant)
                else:
                    refant_int = refant

                antennas = tb.getcol("ANTENNA1")

                # For antenna-based calibration, check ANTENNA1
                # For baseline-based calibration, check both ANTENNA1 and ANTENNA2
                if "ANTENNA2" in tb.colnames():
                    ant2 = tb.getcol("ANTENNA2")
                    # Filter out -1 values (baseline-based calibration uses -1 for antenna-based entries)
                    ant2_valid = ant2[ant2 != -1]
                    all_antennas = set(antennas) | set(ant2_valid)
                else:
                    all_antennas = set(antennas)

                if refant_int not in all_antennas:
                    raise RuntimeError(
                        f"Calibration solve failed: reference antenna {refant} has no solutions "
                        f"in table: {caltable_path}. Available antennas: {sorted(all_antennas)}"
                    )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            f"Calibration solve validation failed: unable to read table {caltable_path}. Error: {e}"
        ) from e
