"""Helpers to compare calibration tables and summarize deltas for reporting/plots."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def compare_caltables(caltable_a: str, caltable_b: str) -> dict[str, object]:
    """
    Compare two calibration tables using existing QA metrics.

    Returns a dictionary with per-table metrics and simple deltas to drive
    plotting/reporting (e.g., gain comparison overlays).
    """
    # Function-scope import: qa/__init__ -> qa.calibration_quality ->
    # calibration/__init__ -> qa_compare would otherwise re-enter the
    # still-partial qa.calibration_quality (circular import; the package
    # guard swallows the ImportError and compare_caltables silently
    # vanishes from dsa110_continuum.calibration when qa is imported first).
    from dsa110_continuum.qa.calibration_quality import validate_caltable_quality

    caltable_a = str(Path(caltable_a))
    caltable_b = str(Path(caltable_b))

    logger.info("Comparing calibration tables:\n  A: %s\n  B: %s", caltable_a, caltable_b)

    qa_a = validate_caltable_quality(caltable_a)
    qa_b = validate_caltable_quality(caltable_b)

    metrics_a = qa_a.to_dict()
    metrics_b = qa_b.to_dict()

    deltas = {
        "fraction_flagged": metrics_b["solution_quality"]["fraction_flagged"]
        - metrics_a["solution_quality"]["fraction_flagged"],
        "median_amplitude": metrics_b["solution_quality"]["median_amplitude"]
        - metrics_a["solution_quality"]["median_amplitude"],
        "median_phase_deg": metrics_b["solution_quality"]["median_phase_deg"]
        - metrics_a["solution_quality"]["median_phase_deg"],
        "rms_phase_deg": metrics_b["solution_quality"]["rms_phase_deg"]
        - metrics_a["solution_quality"]["rms_phase_deg"],
    }

    return {
        "a": metrics_a,
        "b": metrics_b,
        "deltas": deltas,
    }
