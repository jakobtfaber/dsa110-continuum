"""Calibration solve orchestration.

Orchestrates the individual solve functions (delay, pre-bandpass phase,
bandpass, gains) into the full table set for one calibrator MS.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_refant(ms_path: str, refant: str) -> str:
    """Resolve reference antenna, using automatic selection if 'auto'.

    When refant is 'auto', uses MS-based antenna health analysis to select
    the best outrigger antenna (lowest flag fraction). Otherwise returns
    the provided refant value.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set for antenna health analysis.
    refant : str
        Reference antenna specification. Use 'auto' for automatic selection,
        or provide explicit antenna ID(s) like '103' or '104,105,106'.

    Returns
    -------
    str
        Resolved reference antenna chain (CASA-format comma-separated string).
    """
    if refant.lower() == "auto":
        try:
            from dsa110_continuum.calibration.refant_selection import (
                select_best_outrigger_refant,
            )

            result = select_best_outrigger_refant(ms_path)
            logger.info(
                "Auto-selected refant=%s (%s)",
                result["refant_string"],
                result["reason"],
            )
            return result["refant_string"]
        except Exception as e:
            # Fall back to default on error
            from dsa110_continuum.calibration.refant_selection import (
                get_default_outrigger_refants,
            )

            logger.warning(
                "Auto refant selection failed (%s), using default chain", e
            )
            return get_default_outrigger_refants()

    return refant


def solve_calibration_tables(
    ms_path: str,
    table_prefix: str,
    params: dict[str, Any],
    checkpoint: Any | None = None,
) -> dict[str, str]:
    """Orchestrate solving all calibration tables.

        This is the main solve orchestration function that calls individual
        solve functions (delay, pre-bandpass phase, bandpass, gains).

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set (phaseshifted if applicable).
    table_prefix : str
        Prefix for output table names.
    params : Dict[str, Any]
        Parameters for calibration solving.
    checkpoint : Optional[Any], optional
        Optional checkpoint manager, by default None.

    Returns
    -------
        dict
        Dictionary of solved tables {type: path}.
    """
    from dsa110_continuum.calibration.casa_service import CASAService
    from dsa110_continuum.calibration.solve_bandpass import solve_bandpass
    from dsa110_continuum.calibration.solve_delay import solve_delay
    from dsa110_continuum.calibration.solve_gains import solve_prebandpass_phase

    service = CASAService()

    tables = {}
    field = params.get("field", "0")
    refant = _resolve_refant(ms_path, params.get("refant", "auto"))

    # Step 1: Solve delay (K) if requested
    if params.get("solve_delay", False):
        logger.info("Solving delay (K) table...")

        if checkpoint and checkpoint.exists("K"):
            logger.info("Using cached K table")
            tables["K"] = checkpoint.get("K")
        else:
            k_tables = solve_delay(
                ms=ms_path,
                cal_field=field,
                refant=refant,
                table_prefix=table_prefix,
                combine_spw=params.get("k_combine_spw", False),
                minsnr=params.get("k_minsnr", 3.0),
            )
            # solve_delay returns a list of tables
            if k_tables:
                tables["K"] = k_tables[0]
                if checkpoint:
                    checkpoint.set("K", k_tables[0])

    # Step 2: Pre-bandpass phase if requested
    if params.get("prebp_phase", False):
        logger.info("Solving pre-bandpass phase (BA) table...")

        ba_table = solve_prebandpass_phase(
            ms=ms_path,
            cal_field=field,
            refant=refant,
            table_prefix=table_prefix,
            combine_fields=params.get("bp_combine_field", True),
            combine_spw=params.get("k_combine_spw", True),
            minsnr=params.get("prebp_minsnr", 2.0),
        )
        tables["BA"] = ba_table

    # Step 3: Solve bandpass (BP) if requested
    if params.get("solve_bandpass", True):
        logger.info("Solving bandpass (BP) table...")

        bp_tables = solve_bandpass(
            ms=ms_path,
            cal_field=field,
            refant=refant,
            ktable=tables.get("K"),  # K-table for API compat (not actually used in solve)
            table_prefix=table_prefix,
            prebandpass_phase_table=tables.get("BA"),
            combine_fields=params.get("bp_combine_field", True),
            combine_spw=params.get("bp_combine_spw", True),
            minsnr=params.get("bp_minsnr", 3.0),
            uvrange=params.get("bp_uvrange", ">1klambda"),
        )
        # solve_bandpass returns a list of tables
        if bp_tables:
            tables["BP"] = bp_tables[0]

    # Step 4: Solve gains (G) if requested
    if params.get("solve_gains", True):
        logger.info("Solving gains (GA, GP) tables...")
        ga_table = f"{table_prefix}.GA"
        gp_table = f"{table_prefix}.GP"

        gaintable = []
        if "K" in tables:
            gaintable.append(tables["K"])
        if "BA" in tables:
            gaintable.append(tables["BA"])
        if "BP" in tables:
            gaintable.append(tables["BP"])

        # Phase gains
        service.gaincal(
            vis=ms_path,
            caltable=gp_table,
            field=field,
            refant=refant,
            calmode="p",
            solint=params.get("gain_t_short", "60s"),
            minsnr=params.get("gain_minsnr", 3.0),
            gaintable=gaintable,
            combine="field" if params.get("bp_combine_field", True) else "",
        )
        tables["GP"] = gp_table

        # Amplitude gains (apply phase gains)
        gaintable_amp = gaintable + [gp_table]
        service.gaincal(
            vis=ms_path,
            caltable=ga_table,
            field=field,
            refant=refant,
            calmode="a",
            solint=params.get("gain_solint", "inf"),
            minsnr=params.get("gain_minsnr", 3.0),
            gaintable=gaintable_amp,
            combine="field" if params.get("bp_combine_field", True) else "",
        )
        tables["GA"] = ga_table

    return tables
