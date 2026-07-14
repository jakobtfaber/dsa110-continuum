"""Synthetic tests for the conditional RFI hard thresholds."""

import numpy as np
from dsa110_continuum.calibration.rfi_preflight import (
    SpwStats,
    evaluate_preflight_thresholds,
)


def _stats(high_median: float, high_frac: float) -> dict[int, SpwStats]:
    stats = {spw: SpwStats(0.05, 0.005, 0.09, 0.01, 0.02, 20_000) for spw in range(16)}
    stats[14] = SpwStats(high_median * 0.95, 0.01, 1.2, 0.1, high_frac, 20_000)
    stats[15] = SpwStats(high_median, 0.01, 1.5, 0.1, high_frac, 20_000)
    return stats


def test_quiet_tile_does_not_trigger():
    decision = evaluate_preflight_thresholds(
        _stats(high_median=0.0685, high_frac=0.034),
        np.array([1.1] * 23 + [2.1]),
    )

    assert not decision.triggered
    assert decision.metrics_available


def test_1159_like_tile_triggers_all_incident_signals():
    decision = evaluate_preflight_thresholds(
        _stats(high_median=0.26, high_frac=0.153),
        np.full(24, 5.2),
    )

    assert decision.triggered
    assert decision.high_mid_ratio >= 5.0
    assert decision.integration_trigger_fraction == 1.0
    assert len(decision.reasons) == 3


def test_1204_like_episode_tail_still_triggers():
    decision = evaluate_preflight_thresholds(
        _stats(high_median=0.12, high_frac=0.085),
        np.array([2.4] * 8 + [1.6] * 16),
    )

    assert decision.triggered
    assert decision.high_mid_ratio >= 2.0
    assert decision.integration_trigger_fraction >= 0.25


def test_missing_required_metrics_fail_closed():
    stats = _stats(high_median=0.06, high_frac=0.01)
    del stats[15]

    decision = evaluate_preflight_thresholds(stats, np.array([]))

    assert decision.triggered
    assert not decision.metrics_available
