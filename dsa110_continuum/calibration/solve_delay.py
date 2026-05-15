"""Delay (K) calibration solver."""

# ruff: noqa

from __future__ import annotations

import fnmatch
import logging
import os

import numpy as np

from dsa110_continuum.calibration.solver_common import (
    _call_gaincal_with_progress,
    _track_calibration_provenance,
    _validate_solve_success,
    table,
    timed,
)

logger = logging.getLogger(__name__)

def _resolve_field_ids(ms: str, field_sel: str) -> list[int]:
    """Resolve CASA-like field selection into a list of FIELD_ID integers.

    Supports numeric indices, comma lists, numeric ranges ("A~B"), and
    name/glob matching against FIELD::NAME.

    Parameters
    ----------
    ms : str
        Path to Measurement Set
    field_sel : str
        CASA-style field selection string
    """
    # use module-level table

    sel = str(field_sel).strip()
    # Try numeric selections first: comma-separated tokens and A~B ranges
    ids: list[int] = []
    numeric_tokens = [tok.strip() for tok in sel.replace(";", ",").split(",") if tok.strip()]

    def _add_numeric(tok: str) -> bool:
        if "~" in tok:
            a, b = tok.split("~", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                ai, bi = int(a), int(b)
                lo, hi = (ai, bi) if ai <= bi else (bi, ai)
                ids.extend(list(range(lo, hi + 1)))
                return True
            return False
        if tok.isdigit():
            ids.append(int(tok))
            return True
        return False

    any_numeric = False
    for tok in numeric_tokens:
        if _add_numeric(tok):
            any_numeric = True

    if any_numeric:
        # Deduplicate and return
        return sorted(set(ids))

    # Fall back to FIELD::NAME glob matching
    patterns = [p for p in numeric_tokens if p]
    # If no separators were present, still try the full selector as a single
    # pattern
    if not patterns:
        patterns = [sel]

    try:
        with table(f"{ms}::FIELD") as tf:
            names = list(tf.getcol("NAME"))
            out = []
            for i, name in enumerate(names):
                for pat in patterns:
                    if fnmatch.fnmatchcase(str(name), pat):
                        out.append(int(i))
                        break
            return sorted(set(out))
    except (OSError, RuntimeError, KeyError):
        return []


def _validate_delay_solve_preconditions(ms: str, cal_field: str, refant: str) -> None:
    """Validate preconditions for delay solve.

    Parameters
    ----------
    ms : str
        Path to Measurement Set
    cal_field : str
        Field selection for calibration
    refant : str
        Reference antenna name or ID

    Raises
    ------
    ValueError
        If any precondition is not met.

    """
    import numpy as np

    logger.info(f"Validating data for delay solve on field(s) {cal_field}...")

    with table(ms) as tb:
        # Check MODEL_DATA exists
        if "MODEL_DATA" not in tb.colnames():
            raise ValueError(
                "MODEL_DATA column does not exist in MS. "
                "This is a required precondition for K-calibration. "
                "Populate MODEL_DATA using setjy, ft(), or a catalog model before "
                "calling solve_delay()."
            )

        # Check MODEL_DATA is populated (not all zeros)
        model_sample = tb.getcol("MODEL_DATA", startrow=0, nrow=min(100, tb.nrows()))
        if np.all(np.abs(model_sample) < 1e-10):
            raise ValueError(
                "MODEL_DATA column exists but is all zeros (unpopulated). "
                "This is a required precondition for K-calibration. "
                "Populate MODEL_DATA using setjy, ft(), or a catalog model before "
                "calling solve_delay()."
            )

        # Resolve and check field selection
        field_ids = tb.getcol("FIELD_ID")
        target_ids = _resolve_field_ids(ms, str(cal_field))
        if not target_ids:
            raise ValueError(f"Unable to resolve field selection: {cal_field}")

        field_mask = np.isin(field_ids, np.asarray(target_ids, dtype=field_ids.dtype))
        if not np.any(field_mask):
            raise ValueError(f"No data found for field selection {cal_field}")

        row_idx = np.nonzero(field_mask)[0]
        if row_idx.size == 0:
            raise ValueError(f"No data found for field selection {cal_field}")

        # Check reference antenna exists in this field
        start_row = int(row_idx[0])
        nrow_sel = int(row_idx[-1] - start_row + 1)
        ant1_slice = tb.getcol("ANTENNA1", startrow=start_row, nrow=nrow_sel)
        ant2_slice = tb.getcol("ANTENNA2", startrow=start_row, nrow=nrow_sel)
        rel_idx = row_idx - start_row
        field_ant1 = ant1_slice[rel_idx]
        field_ant2 = ant2_slice[rel_idx]
        ref_present = np.any((field_ant1 == int(refant)) | (field_ant2 == int(refant)))
        if not ref_present:
            raise ValueError(f"Reference antenna {refant} not found in field {cal_field}")

        # Check for unflagged data
        field_flags = tb.getcol("FLAG", startrow=start_row, nrow=nrow_sel)
        unflagged_count = int(np.sum(~field_flags))
        if unflagged_count == 0:
            raise ValueError(f"All data in field {cal_field} is flagged")

        logger.debug(
            f"Field {cal_field}: {np.sum(field_mask)} rows, {unflagged_count} unflagged points"
        )


def _run_delay_gaincal(
    ms: str,
    caltable: str,
    cal_field: str,
    refant: str,
    solint: str,
    combine: str,
    minsnr: float,
    uvrange: str,
    retry_without_combine: bool = True,
) -> str:
    """Run gaincal for delay (K) solve with optional retry and progress monitoring.

    Parameters
    ----------
    ms :
        Measurement set path
    caltable :
        Output calibration table path
    cal_field :
        Calibrator field selection
    refant :
        Reference antenna
    solint :
        Solution interval
    combine :
        Combine mode (e.g., "spw" or "")
    minsnr :
        Minimum SNR
    uvrange :
        UV range filter
    retry_without_combine :
        If True, retry with combine="" on failure

    Returns
    -------
        Path to created calibration table

    Raises
    ------
    RuntimeError
        If solve fails even after retry

    """
    kwargs = dict(
        field=cal_field,
        solint=solint,
        refant=refant,
        gaintype="K",
        combine=combine,
        minsnr=minsnr,
        selectdata=True,
    )
    if uvrange:
        kwargs["uvrange"] = uvrange
        logger.debug(f"Using uvrange filter: {uvrange}")

    try:
        _call_gaincal_with_progress("Delay (K) solve", ms, caltable, **kwargs)
        _validate_solve_success(caltable, refant=refant)
        _track_calibration_provenance(
            ms_path=ms,
            caltable_path=caltable,
            task_name="gaincal",
            params={"vis": ms, "caltable": caltable, **kwargs},
        )
        return caltable
    except Exception as e:
        if not retry_without_combine or combine == "":
            raise RuntimeError(f"Delay solve failed: {e}") from e

        # Retry with no combination
        logger.error(f"Delay solve failed: {e}")
        logger.info("Retrying with no combination...")
        kwargs["combine"] = ""
        try:
            _call_gaincal_with_progress("Delay (K) solve (retry)", ms, caltable, **kwargs)
            _validate_solve_success(caltable, refant=refant)
            _track_calibration_provenance(
                ms_path=ms,
                caltable_path=caltable,
                task_name="gaincal",
                params={"vis": ms, "caltable": caltable, **kwargs},
            )
            return caltable
        except Exception as e2:
            raise RuntimeError(f"Delay solve failed even with conservative settings: {e2}") from e2


@timed("calibration.solve_delay")
def solve_delay(
    ms: str,
    cal_field: str,
    refant: str,
    table_prefix: str | None = None,
    combine_spw: bool = False,
    t_slow: str = "inf",
    t_fast: str | None = "60s",
    uvrange: str = "",
    minsnr: float = 3.0,
    skip_slow: bool = False,
) -> list[str]:
    """Solve delay (K) on slow and optional fast timescales using CASA gaincal.

    Uses casatasks.gaincal with gaintype='K' to avoid explicit casatools
    calibrater usage, which can be unstable in some notebook environments.

    **PRECONDITION**: MODEL_DATA must be populated before calling this function.

    Parameters
    ----------
    ms : str
        Path to Measurement Set
    field_selector : str
        Field selection string
    combine_spw : bool
        Whether SPWs are combined
    """
    # Validate preconditions (early return on failure)
    _validate_delay_solve_preconditions(ms, cal_field, refant)

    combine = "spw" if combine_spw else ""
    if table_prefix is None:
        table_prefix = f"{os.path.splitext(ms)[0]}_{cal_field}"

    tables: list[str] = []

    # ============================================================================
    # DELAY CALIBRATION (K) - TRANSPARENCY HEADER
    # ============================================================================
    logger.info("\n" + "=" * 80)
    logger.info("DELAY CALIBRATION (gaintype='K') - Stage 1")
    logger.info("=" * 80)
    logger.info("PURPOSE: Solve for geometric path delays and clock offsets")
    logger.info("         between antennas. For DSA-110 (connected-element array),")
    logger.info("         this is often OPTIONAL due to excellent clock sync.")
    logger.info("")
    logger.info("EXPECTED INPUT:")
    logger.info("  - MODEL_DATA: Populated with calibrator source model ✓")
    logger.info(f"  - Field: {cal_field}")
    logger.info(f"  - Reference antenna: {refant}")
    logger.info(f"  - Combine SPWs: {combine_spw}")
    logger.info(f"  - Solution interval: {t_slow} (slow), {t_fast} (fast)")
    logger.info(f"  - Minimum SNR: {minsnr}")
    logger.info("")
    logger.info("EXPECTED SOLUTIONS:")
    logger.info("  - Typical delays: -100 ns to +100 ns for DSA-110")
    logger.info("  - Geometric bounds: ±200 ns (based on max baseline ~5 km)")
    logger.info("  - Reference antenna: Delay = 0 ns (by definition)")
    logger.info("  - Core antennas: Small delays (~10-50 ns), tightly clustered")
    logger.info("  - Outrigger antennas: Larger delays (50-100 ns), more scattered")
    logger.info("")
    logger.info("QUALITY ASSURANCE:")
    logger.info("  1. Geometric validation: All delays within ±200 ns")
    logger.info("  2. SNR validation: Solutions above minimum SNR threshold")
    logger.info("  3. Outlier detection: Flag delays beyond ±500 ns as errors")
    logger.info("  4. Antenna coverage: At least 80% of antennas with valid solutions")
    logger.info("")
    logger.info("HOW SOLUTIONS ARE USED (CURRENT PIPELINE):")
    logger.info("  - Delay (K) solutions are computed and stored for diagnostics and QA")
    logger.info("  - They are NOT automatically applied in later gain calibration stages")
    logger.info("  - See solve_gains() documentation for the actual calibration order")
    logger.info("=" * 80 + "\n")

    # Slow (infinite) delay solve
    if not skip_slow:
        logger.info(f"→ Running SLOW delay solve (solint={t_slow}) on field {cal_field}...")
        logger.info(f"  This captures instrumental delays that don't change on minute timescales")
        caltable = _run_delay_gaincal(
            ms=ms,
            caltable=f"{table_prefix}.k",
            cal_field=cal_field,
            refant=refant,
            solint=t_slow,
            combine=combine,
            minsnr=minsnr,
            uvrange=uvrange,
            retry_without_combine=True,
        )
        tables.append(caltable)
        logger.info(f":check: Delay solve completed: {caltable}")
    else:
        logger.debug("Skipping slow delay solve (fast mode optimization)")

    # Fast (short) delay solve
    if t_fast or skip_slow:
        if skip_slow and not t_fast:
            t_fast = "60s"
            logger.debug(f"Using default fast solution interval: {t_fast}")

        logger.info(f"\n→ Running FAST delay solve (solint={t_fast}) on field {cal_field}...")
        logger.info(f"  This captures time-variable delays (if present)")
        logger.info(f"  Should show smooth evolution, not random jumps")
        try:
            caltable = _run_delay_gaincal(
                ms=ms,
                caltable=f"{table_prefix}.2k",
                cal_field=cal_field,
                refant=refant,
                solint=t_fast,
                combine=combine,
                minsnr=minsnr,
                uvrange=uvrange,
                retry_without_combine=False,  # Fast solve doesn't retry
            )
            tables.append(caltable)
            logger.info(f":check: Fast delay solve completed: {caltable}")
        except Exception as e:
            logger.error(f"Fast delay solve failed: {e}")
            logger.info("Skipping fast delay solve...")

    # ============================================================================
    # QUALITY ASSURANCE: Validate delay solutions
    # ============================================================================
    is_fast_mode = uvrange and uvrange.startswith(">")
    if not is_fast_mode:
        logger.info("\n" + "=" * 80)
        logger.info("DELAY CALIBRATION QA - Validating Solutions")
        logger.info("=" * 80)

        # QA Check 1: General calibration table validation
        try:
            logger.info("→ QA Check 1: Table structure and reference antenna validation")
            from dsa110_continuum.qa.pipeline_quality import check_calibration_quality

            check_calibration_quality(tables, ms_path=ms, alert_on_issues=True)
            logger.info("  ✓ Table structure valid")
            logger.info("  ✓ Reference antenna has valid solutions")
        except Exception as e:
            logger.warning(f"  ⚠ QA validation warning: {e}")

        # QA Check 2: Geometric validation of delay solutions
        try:
            logger.info("\n→ QA Check 2: Geometric validation of delay values")
            logger.info("  Expected: All delays within ±200 ns (geometric bounds)")
            logger.info("  Expected: At least 80% of antennas with valid solutions")
            logger.info("  Expected: Reference antenna delay = 0 ns")
            from dsa110_continuum.qa.delay_validation import check_delay_solutions

            for ktable in tables:
                if ktable.endswith(".k") or ktable.endswith(".2k"):
                    logger.info(f"\n  Validating {ktable}...")
                    result = check_delay_solutions(
                        ktable,
                        refant=refant,
                        raise_on_failure=True,  # Stop pipeline on invalid delays
                        strict=False,  # Allow some outliers
                    )
                    if result.is_valid:
                        logger.info(
                            f"  ✅ Delay validation PASSED: {result.n_within_bounds}/"
                            f"{result.n_antennas - result.n_flagged} antennas within "
                            f"geometric bounds (max {result.max_geometric_delay_ns:.0f} ns)"
                        )
                        logger.info(f"     - Valid solutions: {result.n_within_bounds} antennas")
                        logger.info(f"     - Flagged solutions: {result.n_flagged} antennas")
                        logger.info(f"     - Out of bounds: {result.n_out_of_bounds} antennas")
                        if result.n_out_of_bounds > 0:
                            logger.warning(
                                f"     ⚠ {result.n_out_of_bounds} antennas have delays "
                                f"outside geometric bounds (may indicate hardware issues)"
                            )
                    else:
                        logger.error(f"  ❌ Delay validation FAILED")
                        logger.error(
                            f"     Only {result.n_within_bounds}/{result.n_antennas} antennas "
                            f"within bounds - below 80% threshold"
                        )
        except Exception as e:
            logger.error(f"  ❌ Delay geometric validation failed: {e}")
            logger.error("     This indicates systematic issues with delay calibration")
            logger.error("     Possible causes:")
            logger.error("       - Incorrect antenna positions")
            logger.error("       - Clock synchronization failure")
            logger.error("       - Wrong calibrator model")
            raise  # Re-raise to stop pipeline

        logger.info("\n" + "=" * 80)
        logger.info("DELAY CALIBRATION QA - Complete")
        logger.info("=" * 80 + "\n")
    else:
        logger.debug("Skipping QA validation (fast mode)")

    return tables
