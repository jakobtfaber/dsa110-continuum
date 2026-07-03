# ruff: noqa: D100,D103,I001
# CASA import moved to function level to prevent logs in workspace root
# See: docs/dev-notes/analysis/casa_log_handling_investigation.md
import logging

from dsa110_continuum.calibration.casa_service import CASAService, suppress_subprocess_stderr
from dsa110_continuum.calibration.flagging_amplitude import (
    flag_clip,
    flag_clip_amplitude,
    flag_residual_rfi_clip,
)
from dsa110_continuum.calibration.flagging_bad_pols import (
    _detect_bad_pols_from_caltable,
    _detect_bad_pols_from_ms_coherence,
    detect_and_flag_bad_polarizations,
)
from dsa110_continuum.calibration.flagging_preflight import (
    PreflightError,
    preflight_check_all,
    preflight_check_aoflagger,
    preflight_check_aoflagger_docker_mounts,
    preflight_check_casa,
    preflight_check_disk_space,
    preflight_check_memory,
    preflight_check_output_dir,
    preflight_check_strategy_file,
    preflight_check_wsclean,
)
from dsa110_continuum.calibration.flagging_rfi import (
    _extend_flags_direct,
    _get_default_aoflagger_strategy,
    flag_extend,
    flag_rfi,
    flag_rfi_aoflagger,
)

__all__ = [
    "PreflightError",
    "_detect_bad_pols_from_caltable",
    "_detect_bad_pols_from_ms_coherence",
    "_extend_flags_direct",
    "_get_default_aoflagger_strategy",
    "analyze_channel_flagging_stats",
    "detect_and_flag_bad_polarizations",
    "detect_and_flag_dead_antennas",
    "flag_antenna",
    "flag_autocorrelations",
    "flag_baselines",
    "flag_clip",
    "flag_clip_amplitude",
    "flag_elevation",
    "flag_extend",
    "flag_manual",
    "flag_problematic_channels",
    "flag_quack",
    "flag_residual_rfi_clip",
    "flag_rfi",
    "flag_rfi_aoflagger",
    "flag_shadow",
    "flag_summary",
    "flag_zeros",
    "preflight_check_all",
    "preflight_check_aoflagger",
    "preflight_check_aoflagger_docker_mounts",
    "preflight_check_casa",
    "preflight_check_disk_space",
    "preflight_check_memory",
    "preflight_check_output_dir",
    "preflight_check_strategy_file",
    "preflight_check_wsclean",
    "reset_flags",
    "run_pre_calibration_flagging",
    "suppress_subprocess_stderr",
]

try:
    from dsa110_continuum.utils.error_context import format_ms_error_with_suggestions
except ImportError:
    def format_ms_error_with_suggestions(
        error: Exception, ms: str, operation: str, suggestions: list[str]
    ) -> str:
        suggestion_text = "\n".join(f"  - {suggestion}" for suggestion in suggestions)
        return f"{operation} failed for {ms}: {error}\n\nSuggestions:\n{suggestion_text}"

def reset_flags(ms: str) -> None:
    service = CASAService()
    service.flagdata(vis=ms, mode="unflag")


def flag_zeros(ms: str, datacolumn: str = "data") -> None:
    service = CASAService()
    service.flagdata(vis=ms, mode="clip", datacolumn=datacolumn, clipzeros=True)


def flag_autocorrelations(ms: str, datacolumn: str = "data") -> None:
    """Flag autocorrelation baselines (antenna with itself).

    Autocorrelations (ant1 == ant2) contain Tsys information but are not
    useful for interferometric calibration and imaging. Flagging them
    reduces data volume and prevents them from contaminating solutions.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    datacolumn :
        Data column to use (default: "data")
    """
    service = CASAService()
    # CASA flagdata with autocorr=True flags only autocorrelations
    service.flagdata(vis=ms, mode="manual", autocorr=True, datacolumn=datacolumn)


def flag_antenna(ms: str, antenna: str, datacolumn: str = "data", pol: str | None = None) -> None:
    antenna_sel = antenna if pol is None else f"{antenna}&{pol}"
    service = CASAService()
    with suppress_subprocess_stderr():
        service.flagdata(vis=ms, mode="manual", antenna=antenna_sel, datacolumn=datacolumn)


def flag_baselines(ms: str, uvrange: str = "2~50m", datacolumn: str = "data") -> None:
    service = CASAService()
    with suppress_subprocess_stderr():
        service.flagdata(vis=ms, mode="manual", uvrange=uvrange, datacolumn=datacolumn)


def flag_manual(
    ms: str,
    antenna: str | None = None,
    scan: str | None = None,
    spw: str | None = None,
    field: str | None = None,
    uvrange: str | None = None,
    timerange: str | None = None,
    correlation: str | None = None,
    datacolumn: str = "data",
) -> None:
    """Manual flagging with selection parameters.

        Flags data matching the specified selection criteria using CASA's
        standard selection syntax. All parameters are optional - specify any
        combination to flag matching data.

    Parameters
    ----------
    ms : str
        Path to Measurement Set
    antenna : Optional[str], optional
        Antenna selection (e.g., '0,1,2' or 'ANT01,ANT02')
    scan : Optional[str], optional
        Scan selection (e.g., '1~5' or '1,3,5')
    spw : Optional[str], optional
        Spectral window selection (e.g., '0:10~20')
    field : Optional[str], optional
        Field selection (field IDs or names)
    uvrange : Optional[str], optional
        UV range selection (e.g., '>100m' or '10~50m')
    timerange : Optional[str], optional
        Time range selection (e.g., '2025/01/01/10:00:00~10:05:00')
    correlation : Optional[str], optional
        Correlation product selection (e.g., 'RR,LL')
    datacolumn : str, optional
        Data column to use (default: 'data')

    Note: At least one selection parameter must be provided.

    """
    kwargs = {"vis": ms, "mode": "manual", "datacolumn": datacolumn}
    if antenna:
        kwargs["antenna"] = antenna
    if scan:
        kwargs["scan"] = scan
    if spw:
        kwargs["spw"] = spw
    if field:
        kwargs["field"] = field
    if uvrange:
        kwargs["uvrange"] = uvrange
    if timerange:
        kwargs["timerange"] = timerange
    if correlation:
        kwargs["correlation"] = correlation

    if len([k for k in [antenna, scan, spw, field, uvrange, timerange, correlation] if k]) == 0:
        suggestions = [
            "Provide at least one selection parameter (antenna, time, baseline, etc.)",
            "Check manual flagging command syntax",
            "Review flagging documentation for parameter requirements",
        ]
        error_msg = format_ms_error_with_suggestions(
            ValueError("At least one selection parameter must be provided for manual flagging"),
            ms,
            "manual flagging",
            suggestions,
        )
        raise ValueError(error_msg)

    service = CASAService()
    with suppress_subprocess_stderr():
        service.flagdata(**kwargs)


def flag_shadow(ms: str, tolerance: float = 0.0) -> None:
    """Flag geometrically shadowed baselines.

    Flags data where one antenna physically blocks the line of sight
    between another antenna and the source. This is particularly important
    for low-elevation observations and compact array configurations.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    tolerance :
        Shadowing tolerance in degrees (default: 0.0)
    """
    service = CASAService()
    with suppress_subprocess_stderr():
        service.flagdata(vis=ms, mode="shadow", tolerance=tolerance)


def flag_quack(
    ms: str,
    quackinterval: float = 2.0,
    quackmode: str = "beg",
    datacolumn: str = "data",
) -> None:
    """Flag beginning/end of scans to remove antenna settling transients.

    After slewing to a new source, antennas require time to stabilize
    thermally and mechanically. This function flags the specified duration
    from the beginning or end of each scan.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    quackinterval :
        Duration in seconds to flag (default: 2.0)
    quackmode :
        'beg' (beginning), 'end', 'tail', or 'endb' (default: 'beg')
    datacolumn :
        Data column to use (default: 'data')
    """
    service = CASAService()
    with suppress_subprocess_stderr():
        service.flagdata(
            vis=ms,
            mode="quack",
            datacolumn=datacolumn,
            quackinterval=quackinterval,
            quackmode=quackmode,
        )


def flag_elevation(
    ms: str,
    lowerlimit: float | None = None,
    upperlimit: float | None = None,
    datacolumn: str = "data",
) -> None:
    """Flag observations below/above specified elevation limits.

    Low-elevation observations suffer from increased atmospheric opacity,
    phase instability, and reduced sensitivity. High-elevation observations
    may have other issues. This function flags data outside specified limits.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    lowerlimit :
        Minimum elevation in degrees (flag data below this)
    upperlimit :
        Maximum elevation in degrees (flag data above this)
    datacolumn :
        Data column to use (default: 'data')
    """
    kwargs = {"vis": ms, "mode": "elevation", "datacolumn": datacolumn}
    if lowerlimit is not None:
        kwargs["lowerlimit"] = lowerlimit
    if upperlimit is not None:
        kwargs["upperlimit"] = upperlimit
    service = CASAService()
    with suppress_subprocess_stderr():
        service.flagdata(**kwargs)


def analyze_channel_flagging_stats(ms_path: str, threshold: float = 0.5) -> dict[int, list[int]]:
    """Analyze flagging statistics per channel across all SPWs.

    After RFI flagging, this function identifies channels that have high flagging
    rates and should be flagged entirely before calibration. This is more precise
    than SPW-level flagging since SPWs are arbitrary subdivisions for data processing.

    Parameters
    ----------
    ms_path : str
        Path to Measurement Set
    threshold : float, optional
        Fraction of flagged data to consider channel problematic (default is 0.5)

    Returns
    -------
    dict
        Dictionary mapping SPW ID to list of problematic channel indices.

    Examples
    --------
    >>> problematic = analyze_channel_flagging_stats('data.ms', threshold=0.5)
    >>> # Returns: {1: [5, 10, 15, 20], 12: [3, 7, 11]}
    """
    from dsa110_continuum.adapters import casa_tables as casatables
    import numpy as np

    table = casatables.table

    logger = logging.getLogger(__name__)
    problematic_channels = {}

    try:
        with table(ms_path, readonly=True) as tb:
            flags = tb.getcol("FLAG")  # Shape: (nrows, nchannels, npol)
            data_desc_id = tb.getcol("DATA_DESC_ID")

            # Get SPW mapping from DATA_DESCRIPTION table
            with table(f"{ms_path}::DATA_DESCRIPTION", readonly=True) as dd:
                spw_ids = dd.getcol("SPECTRAL_WINDOW_ID")

            # Get unique SPWs present in data
            unique_ddids = np.unique(data_desc_id)
            unique_spws = np.unique([spw_ids[ddid] for ddid in unique_ddids])

            logger.debug(f"Analyzing channel flagging for {len(unique_spws)} SPW(s)")

            for spw in unique_spws:
                # Get rows for this SPW
                spw_mask = np.array([spw_ids[ddid] == spw for ddid in data_desc_id])
                spw_flags = flags[spw_mask]

                if len(spw_flags) == 0:
                    continue

                # Calculate flagging fraction per channel
                # flags shape: (nrows, nchannels, npol)
                # Average across rows and polarizations
                channel_flagging = np.mean(spw_flags, axis=(0, 2))

                # Find channels above threshold
                problematic = np.where(channel_flagging > threshold)[0].tolist()

                if problematic:
                    problematic_channels[int(spw)] = problematic
                    logger.debug(
                        f"SPW {spw}: {len(problematic)}/{len(channel_flagging)} channels "
                        f"above {threshold * 100:.1f}% flagging threshold"
                    )

    except Exception as e:
        logger.warning(f"Failed to analyze channel flagging statistics: {e}")
        logger.warning("Skipping channel-level flagging analysis")

    return problematic_channels


def flag_problematic_channels(
    ms_path: str, problematic_channels: dict[int, list[int]], datacolumn: str = "data"
) -> None:
    """Flag problematic channels using CASA flagdata.

    Parameters
    ----------
    ms_path :
        Path to Measurement Set
    problematic_channels :
        Dict mapping SPW ID -> list of channel indices
    datacolumn :
        Data column to flag (default: "data")

    Raises
    ------
    RuntimeError
        If flagdata fails

    """
    logger = logging.getLogger(__name__)

    if not problematic_channels:
        logger.debug("No problematic channels to flag")
        return

    # Build SPW selection string for CASA flagdata
    # Format: "spw:chan1,chan2,chan3;spw:chan1,chan2"
    spw_selections = []
    total_channels = 0

    for spw, channels in sorted(problematic_channels.items()):
        # Sort channels for cleaner output
        channels_sorted = sorted(channels)
        chan_str = ",".join(map(str, channels_sorted))
        spw_selections.append(f"{spw}:{chan_str}")
        total_channels += len(channels_sorted)
        logger.info(
            f"  SPW {spw}: {len(channels_sorted)} problematic channels "
            f"({channels_sorted[:5]}{'...' if len(channels_sorted) > 5 else ''})"
        )

    spw_sel = ";".join(spw_selections)

    logger.info(
        f"Flagging {total_channels} problematic channel(s) across "
        f"{len(problematic_channels)} SPW(s) before calibration"
    )

    try:
        service = CASAService()
        with suppress_subprocess_stderr():
            service.flagdata(
                vis=ms_path,
                spw=spw_sel,
                mode="manual",
                datacolumn=datacolumn,
                flagbackup=False,
            )
        logger.info(f":check: Flagged {total_channels} problematic channel(s) before calibration")
    except Exception as e:
        logger.error(f"Failed to flag problematic channels: {e}")
        raise RuntimeError(f"Channel flagging failed: {e}") from e


def flag_summary(
    ms: str,
    spw: str = "",
    field: str = "",
    antenna: str = "",
    uvrange: str = "",
    correlation: str = "",
    timerange: str = "",
    reason: str = "",
) -> dict:
    """Report flagging statistics without flagging data.

    Provides comprehensive statistics about existing flags, including
    total flagged fraction, breakdowns by antenna, spectral window,
    polarization, and other dimensions. Useful for understanding data quality
    and identifying problematic subsets.

    Parameters
    ----------
    ms :
        Path to Measurement Set
    spw :
        Spectral window selection
    field :
        Field selection
    antenna :
        Antenna selection
    uvrange :
        UV range selection
    correlation :
        Correlation product selection
    timerange :
        Time range selection
    reason :
        Flag reason to query

    Returns
    -------
        Dictionary with flagging statistics

    """
    kwargs = {"vis": ms, "mode": "summary", "display": "report"}
    if spw:
        kwargs["spw"] = spw
    if field:
        kwargs["field"] = field
    if antenna:
        kwargs["antenna"] = antenna
    if uvrange:
        kwargs["uvrange"] = uvrange
    if correlation:
        kwargs["correlation"] = correlation
    if timerange:
        kwargs["timerange"] = timerange
    if reason:
        kwargs["reason"] = reason

    # Skip calling flagdata in summary mode - it triggers casaplotserver which hangs
    # Instead, directly read flags from the MS using casacore.tables
    # This is faster and avoids subprocess issues
    # with suppress_subprocess_stderr():
    #     flagdata(**kwargs)

    # Parse summary statistics directly from MS (faster and avoids casaplotserver)
    try:
        from dsa110_continuum.adapters import casa_tables as casatables
        import numpy as np

        table = casatables.table

        stats = {}
        with table(ms, readonly=True) as tb:
            n_rows = tb.nrows()
            if n_rows > 0:
                flags = tb.getcol("FLAG")
                total_points = flags.size
                flagged_points = np.sum(flags)
                stats["total_fraction_flagged"] = (
                    float(flagged_points / total_points) if total_points > 0 else 0.0
                )
                stats["n_rows"] = int(n_rows)

        return stats
    except (OSError, RuntimeError, KeyError):
        return {}


def detect_and_flag_dead_antennas(
    ms: str,
    threshold: float = 0.95,
    dry_run: bool = False,
) -> dict:
    """Detect antennas with excessive flagging and optionally flag them completely.

    Scans the Measurement Set for per-antenna flagging statistics. Antennas with
    flagged data above the threshold are considered "dead" and can be flagged
    completely to prevent calibration errors (e.g., CASA getcell::TIME errors
    when trying to solve for antennas with no usable data).

    This function should be called AFTER initial flagging (zeros, autocorrelations)
    but BEFORE calibration (bandpass, gaincal).

    Parameters
    ----------
    ms :
        Path to Measurement Set
    threshold :
        Fraction of flagged data above which an antenna is considered
        dead (default: 0.95 = 95% flagged)
    dry_run :
        If True, only report statistics without flagging (default: False)

    Returns
    -------
    Dictionary with
        - 'dead_antennas': List of antenna IDs flagged as dead
        - 'partial_antennas': List of antenna IDs with >50% but <=threshold flagging
        - 'antenna_stats': Dict mapping antenna ID to flagged fraction
        - 'total_flagged_before': Overall flagged fraction before action
        - 'total_flagged_after': Overall flagged fraction after action (same if dry_run)
        - 'n_dead': Number of dead antennas detected
        - 'n_partial': Number of partially bad antennas
        - 'action_taken': Whether antennas were flagged (False if dry_run)

    """
    import logging

    from dsa110_continuum.adapters import casa_tables as casatables
    import numpy as np

    logger = logging.getLogger(__name__)

    result = {
        "dead_antennas": [],
        "partial_antennas": [],
        "antenna_stats": {},
        "total_flagged_before": 0.0,
        "total_flagged_after": 0.0,
        "n_dead": 0,
        "n_partial": 0,
        "action_taken": False,
    }

    try:
        # Read antenna info and compute per-antenna flagging statistics
        with casatables.table(ms, readonly=True) as tb:
            n_rows = tb.nrows()
            if n_rows == 0:
                logger.warning(f"MS {ms} has no rows")
                return result

            # Get columns needed for per-antenna stats
            ant1 = tb.getcol("ANTENNA1")
            ant2 = tb.getcol("ANTENNA2")
            flags = tb.getcol("FLAG")  # shape: (n_rows, n_chan, n_pol)

            # Compute overall flag fraction
            total_points = flags.size
            flagged_points = np.sum(flags)
            result["total_flagged_before"] = float(flagged_points / total_points)

            # Get unique antenna IDs
            all_antennas = np.unique(np.concatenate([ant1, ant2]))

            # Compute per-antenna flagging
            # An antenna participates in a row if it's ant1 or ant2
            antenna_stats = {}
            for ant_id in all_antennas:
                mask = (ant1 == ant_id) | (ant2 == ant_id)
                ant_flags = flags[mask]
                if ant_flags.size > 0:
                    frac = float(np.sum(ant_flags) / ant_flags.size)
                    antenna_stats[int(ant_id)] = frac

            result["antenna_stats"] = antenna_stats

            # Classify antennas
            dead_antennas = []
            partial_antennas = []
            for ant_id, frac in antenna_stats.items():
                if frac >= threshold:
                    dead_antennas.append(ant_id)
                elif frac >= 0.5:
                    partial_antennas.append(ant_id)

            result["dead_antennas"] = sorted(dead_antennas)
            result["partial_antennas"] = sorted(partial_antennas)
            result["n_dead"] = len(dead_antennas)
            result["n_partial"] = len(partial_antennas)

        # Log findings
        if dead_antennas:
            logger.info(
                f"Detected {len(dead_antennas)} dead antennas (>{threshold * 100:.0f}% flagged): "
                f"{sorted(dead_antennas)}"
            )
        if partial_antennas:
            logger.info(
                f"Detected {len(partial_antennas)} partially bad antennas "
                f"(50-{threshold * 100:.0f}% flagged): "
                f"{sorted(partial_antennas)}"
            )

        # Flag dead antennas if not dry_run
        if dead_antennas and not dry_run:
            antenna_sel = ",".join(str(a) for a in dead_antennas)
            logger.info(f"Flagging dead antennas: {antenna_sel}")
            flag_antenna(ms, antenna_sel)
            result["action_taken"] = True

            # Recompute total flagging after action
            with casatables.table(ms, readonly=True) as tb:
                flags = tb.getcol("FLAG")
                result["total_flagged_after"] = float(np.sum(flags) / flags.size)
        else:
            result["total_flagged_after"] = result["total_flagged_before"]

        # Save results to JSON file alongside the MS for later retrieval
        import json
        from datetime import datetime
        from pathlib import Path

        ms_path = Path(ms)
        report_path = ms_path.parent / f"{ms_path.stem}_antenna_health.json"

        report = {
            "ms_path": str(ms_path.absolute()),
            "ms_name": ms_path.name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "threshold": threshold,
            "dry_run": dry_run,
            "dead_antennas": result["dead_antennas"],
            "partial_antennas": result["partial_antennas"],
            "n_dead": result["n_dead"],
            "n_partial": result["n_partial"],
            "total_flagged_before": result["total_flagged_before"],
            "total_flagged_after": result["total_flagged_after"],
            "action_taken": result["action_taken"],
            "antenna_stats": result["antenna_stats"],
        }

        try:
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)
            result["report_path"] = str(report_path)
            logger.info(f"Antenna health report saved to: {report_path}")
        except OSError as e:
            logger.warning(f"Could not save antenna health report: {e}")
            result["report_path"] = None

        return result

    except (OSError, RuntimeError, KeyError) as e:
        logger.error(f"Error detecting dead antennas in {ms}: {e}")
        return result


def run_pre_calibration_flagging(
    ms_file: str,
    *,
    do_flagging: bool = True,
    enable_bad_pol_detection: bool = False,
    bad_pol_phase_table: str | None = None,
    bad_pol_dry_run: bool = False,
) -> dict:
    """Run the full pre-calibration flagging sequence on a Measurement Set.

    Wraps the pre-cal flagging steps that previously lived inline in
    ``calibration/runner.py``:

    - Autocorr flag + AOFlagger RFI flag (with CASA tfcrop+rflag fallback) —
      gated by ``do_flagging``.
    - Dead-antenna detection at 95% threshold, applied — always runs.
    - Single-polarization detection — opt-in via ``enable_bad_pol_detection``.

    Parameters
    ----------
    ms_file :
        Path to the Measurement Set.
    do_flagging :
        If True, run autocorr flag + AOFlagger RFI flag with CASA fallback.
        If False, skip those and run only dead-antenna detection.
    enable_bad_pol_detection :
        If True, run ``detect_and_flag_bad_polarizations`` after the dead-antenna
        pass. **OFF by default** — this is a cautious-rollout switch; production
        callers must opt in explicitly until the feature has been validated on
        real data.
    bad_pol_phase_table :
        Path to the pre-bandpass phase calibration table for the strong primary
        detection path. If None or missing, the function falls back to MS-coherence
        analysis (less reliable per the underlying function's docstring).
    bad_pol_dry_run :
        If True, only report bad polarizations without applying CASA flags.

    Returns
    -------
    dict
        - ``"dead_result"`` — return value of ``detect_and_flag_dead_antennas``,
          or ``None`` if it raised.
        - ``"bad_pol_result"`` — return value of ``detect_and_flag_bad_polarizations``
          when ``enable_bad_pol_detection=True``, or ``None`` otherwise (also
          ``None`` if the detection raised — failures are swallowed for
          science-safety, matching the dead-antenna pattern).
    """
    logger = logging.getLogger(__name__)
    result: dict = {"dead_result": None, "bad_pol_result": None}

    if do_flagging:
        try:
            service = CASAService()
            logger.info("Flagging autocorrelations...")
            service.flagdata(vis=ms_file, autocorr=True, flagbackup=False)

            logger.info("Running AOFlagger RFI flagging...")
            try:
                flag_rfi(ms_file, backend="aoflagger")
                logger.info(" AOFlagger RFI flagging complete")
            except Exception as aoflagger_err:  # noqa: BLE001
                logger.warning(
                    "AOFlagger failed (%s), falling back to CASA tfcrop+rflag",
                    aoflagger_err,
                )
                service.flagdata(
                    vis=ms_file,
                    mode="tfcrop",
                    datacolumn="data",
                    timecutoff=4.0,
                    freqcutoff=4.0,
                    extendflags=False,
                    flagbackup=False,
                )
                service.flagdata(
                    vis=ms_file,
                    mode="rflag",
                    datacolumn="data",
                    timedevscale=4.0,
                    freqdevscale=4.0,
                    extendflags=False,
                    flagbackup=False,
                )
                logger.info(" CASA tfcrop+rflag flagging complete")
        except Exception as err:  # noqa: BLE001
            logger.warning("Pre-calibration flagging failed (continuing): %s", err)

    try:
        dead_result = detect_and_flag_dead_antennas(
            ms_file, threshold=0.95, dry_run=False
        )
        result["dead_result"] = dead_result
        n_dead = int(dead_result.get("n_dead", 0))
        before = float(dead_result.get("total_flagged_before", 0.0)) * 100
        after = float(dead_result.get("total_flagged_after", before / 100)) * 100
        if n_dead > 0:
            logger.warning(
                "Pre-cal dead-antenna detection: flagged %d dead antennas %s "
                "(flag fraction %.2f%% -> %.2f%%)",
                n_dead,
                dead_result.get("dead_antennas", []),
                before,
                after,
            )
        else:
            logger.info(
                "Pre-cal dead-antenna detection: 0 dead antennas (flag fraction %.2f%%)",
                before,
            )
    except Exception as err:  # noqa: BLE001 - science-safe: never abort the pipeline here
        logger.warning("Dead-antenna detection failed (continuing): %s", err)

    if enable_bad_pol_detection:
        try:
            bad_pol_result = detect_and_flag_bad_polarizations(
                ms_file,
                phase_table=bad_pol_phase_table,
                dry_run=bad_pol_dry_run,
            )
            result["bad_pol_result"] = bad_pol_result
            n_affected = int(bad_pol_result.get("n_antennas_affected", 0))
            method = bad_pol_result.get("detection_method", "unknown")
            action_taken = bool(bad_pol_result.get("action_taken", False))
            if n_affected > 0:
                # Distinguish "flagged" (action applied) from "detected" (dry_run
                # or detector skipped already-flagged selections). An operator
                # reading the log needs to know whether the MS state changed.
                verb = "flagged" if action_taken else "detected"
                logger.warning(
                    "Pre-cal bad-polarization detection (%s): %s %d antenna-pol pairs "
                    "(action_taken=%s, dry_run=%s) %s",
                    method,
                    verb,
                    n_affected,
                    action_taken,
                    bad_pol_dry_run,
                    bad_pol_result.get("bad_polarizations", []),
                )
            else:
                logger.info(
                    "Pre-cal bad-polarization detection (%s): 0 single-pol failures",
                    method,
                )
        except Exception as err:  # noqa: BLE001 - science-safe: never abort the pipeline here
            logger.warning("Bad-polarization detection failed (continuing): %s", err)

    return result
