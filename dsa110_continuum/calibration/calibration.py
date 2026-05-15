"""Compatibility exports for DSA-110 calibration solvers.

Solver implementations live in role-specific modules:
``solve_delay.py``, ``solve_bandpass.py``, and ``solve_gains.py``.
This module preserves the historical import path.
"""

# ruff: noqa: F401,I001

from dsa110_continuum.calibration.solve_bandpass import (
    _build_bandpass_combine_string,
    _check_coherent_phasing,
    _check_flag_fraction,
    _determine_field_selector,
    _flag_fraction_excluding_dead_receptors,
    _log_spw_verification,
    _print_bandpass_solution_summary,
    _run_bandpass_diagnostics,
    _run_bandpass_with_progress,
    _validate_bandpass_model_data,
    solve_bandpass,
)
from dsa110_continuum.calibration.solve_delay import (
    _resolve_field_ids,
    _run_delay_gaincal,
    _validate_delay_solve_preconditions,
    solve_delay,
)
from dsa110_continuum.calibration.solve_gains import solve_gains, solve_prebandpass_phase
from dsa110_continuum.calibration.solver_common import (
    CASAService,
    QA_FLAGGED_MAX_THRESHOLD,
    QA_FLAGGED_WARN_THRESHOLD,
    QA_MIN_ANTENNAS,
    QA_SNR_MIN_THRESHOLD,
    QA_SNR_WARN_THRESHOLD,
    _call_gaincal,
    _call_gaincal_with_progress,
    _determine_spwmap_for_bptables,
    _extract_quality_metrics,
    _get_caltable_spw_count,
    _track_calibration_provenance,
    _validate_solve_success,
    table,
)
from dsa110_continuum.calibration.validate import validate_caltables_for_use
from dsa110_continuum.conversion.merge_spws import get_spw_count

__all__ = [
    # Public solvers
    "solve_delay",
    "solve_prebandpass_phase",
    "solve_bandpass",
    "solve_gains",
    # Bandpass internals (re-exported for backward-compatible imports)
    "_build_bandpass_combine_string",
    "_check_coherent_phasing",
    "_check_flag_fraction",
    "_determine_field_selector",
    "_flag_fraction_excluding_dead_receptors",
    "_log_spw_verification",
    "_print_bandpass_solution_summary",
    "_run_bandpass_diagnostics",
    "_run_bandpass_with_progress",
    "_validate_bandpass_model_data",
    # Delay internals
    "_resolve_field_ids",
    "_run_delay_gaincal",
    "_validate_delay_solve_preconditions",
    # Shared solver helpers
    "CASAService",
    "QA_FLAGGED_MAX_THRESHOLD",
    "QA_FLAGGED_WARN_THRESHOLD",
    "QA_MIN_ANTENNAS",
    "QA_SNR_MIN_THRESHOLD",
    "QA_SNR_WARN_THRESHOLD",
    "_call_gaincal",
    "_call_gaincal_with_progress",
    "_determine_spwmap_for_bptables",
    "_extract_quality_metrics",
    "_get_caltable_spw_count",
    "_track_calibration_provenance",
    "_validate_solve_success",
    "table",
    # Validation / SPW helpers
    "validate_caltables_for_use",
    "get_spw_count",
]
