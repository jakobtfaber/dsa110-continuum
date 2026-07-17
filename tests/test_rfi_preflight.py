"""Synthetic tests for the conditional RFI hard thresholds."""

import dsa110_continuum.adapters.casa_tables as casa_tables
import dsa110_continuum.calibration.rfi_preflight as rfi_preflight
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


def test_measurement_skips_visibility_reads_for_irrelevant_spws(monkeypatch):
    data_reads = []

    class FakeTable:
        def __init__(self, path, **_kwargs):
            self.data_description = str(path).endswith("/DATA_DESCRIPTION")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def nrows(self):
            return 16

        def getcol(self, name, startrow=0, nrow=-1):
            if self.data_description:
                return np.arange(16)
            stop = 16 if nrow == -1 else startrow + nrow
            rows = np.arange(16)[startrow:stop]
            if name == "DATA_DESC_ID":
                return rows
            if name == "DATA":
                data_reads.append(startrow)
                return np.full((len(rows), 1001, 1), 0.05 + 0j)
            if name == "FLAG":
                return np.zeros((len(rows), 1001, 1), dtype=bool)
            if name == "ANTENNA1":
                return np.zeros(len(rows), dtype=int)
            if name == "ANTENNA2":
                return np.ones(len(rows), dtype=int)
            if name == "TIME":
                return np.zeros(len(rows))
            raise AssertionError(name)

    monkeypatch.setattr(casa_tables, "table", FakeTable)

    result = rfi_preflight.measure_rfi_preflight(
        "fake.ms",
        chunk_rows=1,
        spw_ids=(6, 7, 8, 9, 14, 15),
    )

    assert sorted(result.spw_stats) == [6, 7, 8, 9, 14, 15]
    assert data_reads == [6, 7, 8, 9, 14, 15] * 2

    data_reads.clear()
    result = rfi_preflight.measure_rfi_preflight("fake.ms", chunk_rows=1)

    assert sorted(result.spw_stats) == list(range(16))
    assert data_reads == list(range(16)) * 2
