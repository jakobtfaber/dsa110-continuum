"""Regression and correctness tests for the relative-photometry path (issue #111).

Correctness criteria (hardening-research-code):

1. Analytic closed forms — relative photometry is R_i = T_i / A_i where A_i is
   the weighted ensemble average of neighbor fluxes at epoch i: inverse-variance
   weights (w = 1/sigma^2) when errors are given, manual weights otherwise,
   uniform as fallback; weighted median instead of weighted mean when
   use_robust_stats=True and >= 3 neighbors are valid at that epoch. Every
   expected value below is derived by hand from small integer inputs; the
   derivation is stated in each test.
2. Structural invariants — scaling all neighbor fluxes by k > 0 scales the
   relative flux by 1/k (holds exactly for both mean and median averaging);
   a target identical to its single neighbor gives relative flux == 1.
3. Regression anchor — Source.calculate_relative_lightcurve raised NameError on
   every call reaching the relative-flux step (the neighbor_fluxes transpose
   was missing; fixed on main via #110). The assembly tests here fail on the
   pre-fix code: without `neighbor_fluxes = np.array(neighbor_flux_matrix).T`
   the happy path dies with NameError, and with the transpose dropped it dies
   with ValueError (per-neighbor rows (2, 3) vs expected (n_epochs, n_neighbors)
   = (3, 2) — the test case is deliberately non-square).
4. Variability-metric wiring — calc_variability_metrics must reproduce the
   Mooley et al. (2016) closed forms: eta = reduced chi-squared about the
   weighted mean (pinned canonically in tests/test_metrics_canonical.py),
   Vs = (f_a - f_b)/sqrt(e_a^2 + e_b^2), m = 2(f_a - f_b)/(f_a + f_b) over
   sequential epoch pairs, and v = std(f, ddof=1)/mean(f) via the canonical
   photometry/metrics.py implementation (test_metrics_canonical.py
   TestVMetricConvention, aligned to VAST in #110; source.py consolidated
   onto it in #118 — for f=[1,2,3] that is 0.5). When eta cannot be computed
   the metric is None, never a float coercion of None (issue #118 item 2).
5. Flux-column selection — normalized_flux_jy/normalized_flux_err_jy are used
   when the normalized column carries at least one finite value; otherwise
   peak_jyb/peak_err_jyb are used. This makes the relative-photometry path
   reachable from a raw products DB (issue #118 item 1). MJD recovery is
   analytic: the Unix epoch 1970-01-01T00:00 UTC is MJD 40587.0 exactly and
   both scales count 86400 s civil days (Unix time excludes leap seconds), so
   a row with mjd NULL and measured_at = t seconds loads as mjd = 40587 + t/86400.
6. Neighbor-search geometry — candidate distance uses the exact spherical
   (haversine) separation, so a neighbor within radius_deg is selected
   regardless of RA wrap at 0/360 or proximity to the pole; expected
   separations below are derived from the haversine closed form in each test.

Tolerances: every expected value is exact in a handful of double-precision
operations, so assert_allclose(rtol=1e-12) covers accumulated rounding (~10 eps)
with margin. The 1e-20 epsilon added to neighbor variances in inverse-variance
weighting perturbs weights at the 1e-20 relative level, far inside rtol. atol
is used only for quantities that are identically zero by construction.

No golden data files: synthetic scalars only, per repo test conventions.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from dsa110_continuum.photometry.source import Source, SourceError
from dsa110_continuum.photometry.variability import calculate_relative_flux
from numpy.testing import assert_allclose

EPOCHS = ["e1.fits", "e2.fits", "e3.fits"]
MJDS = [60000.0, 60001.0, 60002.0]


def _measurements_frame(image_paths, mjds, fluxes, errors):
    return pd.DataFrame(
        {
            "ra_deg": 180.0,
            "dec_deg": 16.1,
            "image_path": image_paths,
            "mjd": mjds,
            "peak_jyb": fluxes,
            "peak_err_jyb": errors,
            "normalized_flux_jy": fluxes,
            "normalized_flux_err_jy": errors,
        }
    )


@pytest.fixture()
def make_source(monkeypatch):
    """Build a Source whose measurement loading is served from an in-memory dict.

    Patches Source._load_measurements to look up self.source_id in `frames`
    (KeyError for unknown ids stands in for a neighbor that fails to load) and,
    when neighbor_ids is given, patches find_stable_neighbors to return them.
    """

    def _make(frames, target_id="T", neighbor_ids=None):
        def fake_load(self):
            return frames[self.source_id].copy()

        monkeypatch.setattr(Source, "_load_measurements", fake_load)
        if neighbor_ids is not None:
            monkeypatch.setattr(
                Source, "find_stable_neighbors", lambda self, **kwargs: list(neighbor_ids)
            )
        return Source(target_id, products_db=Path("/nonexistent/products.sqlite3"))

    return _make


class TestRelativeFluxAnalytic:
    """Criterion 1: closed-form relative flux under each weighting mode."""

    def test_uniform_mean_two_neighbors(self):
        """A = [(1+3)/2, (2+4)/2, (3+6)/2] = [2, 3, 4.5]; R = [1, 2, 2].

        mean(R) = 5/3; std(R, ddof=0) = sqrt(((2/3)^2 + (1/3)^2 + (1/3)^2)/3)
        = sqrt(2)/3.
        """
        rel, mean, std = calculate_relative_flux(
            np.array([2.0, 6.0, 9.0]),
            np.array([[1.0, 3.0], [2.0, 4.0], [3.0, 6.0]]),
            use_robust_stats=False,
        )
        assert_allclose(rel, [1.0, 2.0, 2.0], rtol=1e-12)
        assert_allclose(mean, 5.0 / 3.0, rtol=1e-12)
        assert_allclose(std, np.sqrt(2.0) / 3.0, rtol=1e-12)

    def test_inverse_variance_weights(self):
        """w = 1/e^2 = [1, 1/4], normalized [0.8, 0.2]; A = 1.6 + 1.2 = 2.8; R = 10/7."""
        rel, _, _ = calculate_relative_flux(
            np.array([4.0]),
            np.array([[2.0, 6.0]]),
            neighbor_errors=np.array([[1.0, 2.0]]),
            use_robust_stats=False,
        )
        assert_allclose(rel, [10.0 / 7.0], rtol=1e-12)

    def test_manual_weights(self):
        """w = [3, 1], normalized [0.75, 0.25]; A = 0.75 + 1.25 = 2; R = 1.5."""
        rel, _, _ = calculate_relative_flux(
            np.array([3.0]),
            np.array([[1.0, 5.0]]),
            neighbor_weights=np.array([3.0, 1.0]),
            use_robust_stats=False,
        )
        assert_allclose(rel, [1.5], rtol=1e-12)

    def test_errors_take_priority_over_manual_weights(self):
        """Equal manual weights would give A = 4 (R = 1); inverse-variance gives R = 10/7."""
        rel, _, _ = calculate_relative_flux(
            np.array([4.0]),
            np.array([[2.0, 6.0]]),
            neighbor_weights=np.array([1.0, 1.0]),
            neighbor_errors=np.array([[1.0, 2.0]]),
            use_robust_stats=False,
        )
        assert_allclose(rel, [10.0 / 7.0], rtol=1e-12)

    def test_single_neighbor_1d_input(self):
        """A 1D neighbor array is reshaped to (n_epochs, 1); R = T/N elementwise."""
        rel, _, _ = calculate_relative_flux(
            np.array([4.0, 9.0]), np.array([2.0, 3.0]), use_robust_stats=False
        )
        assert_allclose(rel, [2.0, 3.0], rtol=1e-12)


class TestRelativeFluxRobustStats:
    """Criterion 1: the weighted median engages at >= 3 valid neighbors, not below."""

    def test_weighted_median_rejects_outlier(self):
        """Sorted fluxes [1, 2, 10], uniform cumsum [1/3, 2/3, 1]: first bin
        reaching 0.5 is index 1, so A = 2 and the flaring neighbor (10) is
        ignored. The mean would give A = 13/3 (R ~ 0.923) instead of R = 2."""
        rel, _, _ = calculate_relative_flux(
            np.array([4.0]), np.array([[1.0, 10.0, 2.0]]), use_robust_stats=True
        )
        assert_allclose(rel, [2.0], rtol=1e-12)

    def test_two_neighbors_fall_back_to_weighted_mean(self):
        """With 2 neighbors the median branch is skipped: the cumsum median
        would pick the lower flux (A = 1, R = 4); the weighted mean gives
        A = 2, R = 2 — asserting 2 proves the fallback."""
        rel, _, _ = calculate_relative_flux(
            np.array([4.0]), np.array([[1.0, 3.0]]), use_robust_stats=True
        )
        assert_allclose(rel, [2.0], rtol=1e-12)


class TestRelativeFluxMissingData:
    """Criterion 1: NaNs are masked and weights renormalized per epoch."""

    def test_nan_neighbor_masked_and_weights_renormalized(self):
        """Epoch 0 averages the single valid neighbor (A = 1); epoch 1 both (A = 3)."""
        rel, _, _ = calculate_relative_flux(
            np.array([2.0, 6.0]),
            np.array([[1.0, np.nan], [2.0, 4.0]]),
            use_robust_stats=False,
        )
        assert_allclose(rel, [2.0, 2.0], rtol=1e-12)

    def test_all_nan_epoch_yields_nan_and_is_excluded_from_stats(self):
        """An epoch with no valid neighbor is NaN; mean/std cover finite epochs only."""
        rel, mean, std = calculate_relative_flux(
            np.array([5.0, 6.0]),
            np.array([[np.nan, np.nan], [2.0, 4.0]]),
            use_robust_stats=False,
        )
        assert np.isnan(rel[0])
        assert_allclose(rel[1], 2.0, rtol=1e-12)
        assert_allclose(mean, 2.0, rtol=1e-12)
        assert_allclose(std, 0.0, atol=1e-15)


class TestRelativeFluxValidation:
    """Input-contract errors raise ValueError instead of returning garbage."""

    def test_empty_target_raises(self):
        with pytest.raises(ValueError, match="empty"):
            calculate_relative_flux(np.array([]), np.array([[1.0]]))

    def test_1d_neighbor_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="1D neighbor_fluxes"):
            calculate_relative_flux(np.array([1.0, 2.0]), np.array([1.0, 2.0, 3.0]))

    def test_epoch_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="do not match target epochs"):
            calculate_relative_flux(np.array([1.0, 2.0, 3.0]), np.ones((2, 2)))

    def test_errors_shape_mismatch_raises(self):
        with pytest.raises(ValueError, match="neighbor_errors shape"):
            calculate_relative_flux(
                np.array([1.0]), np.ones((1, 2)), neighbor_errors=np.ones((1, 3))
            )

    def test_weights_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="neighbor_weights"):
            calculate_relative_flux(
                np.array([1.0]), np.ones((1, 2)), neighbor_weights=np.array([1.0])
            )


class TestRelativeFluxInvariants:
    """Criterion 2: structural invariants independent of the specific values."""

    def test_neighbor_scale_invariance(self):
        """Scaling all neighbor fluxes by k scales R by exactly 1/k.

        Holds for the weighted mean (linear) and the weighted median (order-
        preserving under positive scaling) alike.
        """
        target = np.array([2.0, 6.0, 9.0, 4.0])
        neighbors = np.array([[1.0, 3.0, 2.0], [2.0, 4.0, 3.0], [3.0, 6.0, 5.0], [1.0, 2.0, 8.0]])
        k = 7.0
        for robust in (False, True):
            base, _, _ = calculate_relative_flux(target, neighbors, use_robust_stats=robust)
            scaled, _, _ = calculate_relative_flux(target, k * neighbors, use_robust_stats=robust)
            assert_allclose(scaled * k, base, rtol=1e-12)

    def test_target_equal_to_single_neighbor_is_unity(self):
        flux = np.array([1.5, 2.5, 3.5])
        rel, mean, std = calculate_relative_flux(flux, flux.reshape(-1, 1))
        assert_allclose(rel, np.ones(3), rtol=1e-12)
        assert_allclose(mean, 1.0, rtol=1e-12)
        assert_allclose(std, 0.0, atol=1e-15)


class TestCalculateRelativeLightcurveAssembly:
    """Criterion 3 (regression anchor) plus criterion 1 on the assembled matrix.

    The hand-built cases are 2 neighbors x 3 epochs so the per-neighbor row
    matrix (2, 3) and the required (n_epochs, n_neighbors) = (3, 2) layout are
    distinguishable by shape, not just by values.
    """

    def test_happy_path_two_neighbors_three_epochs(self, make_source):
        """Equal neighbor errors give equal weights; with 2 neighbors the
        ensemble average is the plain mean: A = [2, 3, 4.5], R = [1, 2, 2]
        (same derivation as test_uniform_mean_two_neighbors)."""
        frames = {
            "T": _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1]),
            "N1": _measurements_frame(EPOCHS, MJDS, [1.0, 2.0, 3.0], [1.0, 1.0, 1.0]),
            "N2": _measurements_frame(EPOCHS, MJDS, [3.0, 4.0, 6.0], [1.0, 1.0, 1.0]),
        }
        target = make_source(frames, neighbor_ids=["N1", "N2"])

        result = target.calculate_relative_lightcurve()

        assert_allclose(result["relative_flux"], [1.0, 2.0, 2.0], rtol=1e-12)
        assert_allclose(result["relative_flux_mean"], 5.0 / 3.0, rtol=1e-12)
        assert_allclose(result["relative_flux_std"], np.sqrt(2.0) / 3.0, rtol=1e-12)
        assert result["n_neighbors"] == 2
        assert result["neighbor_ids"] == ["N1", "N2"]
        assert_allclose(result["mjds"], MJDS)

    def test_neighbor_epoch_alignment_by_image_path(self, make_source):
        """Alignment is by image_path, not row order: N1 arrives with epochs
        reversed and N2 lacks e2 entirely. After reindexing, epoch e2 averages
        over N1 alone: A = [2, 2, 4.5], R = [1, 3, 2]."""
        frames = {
            "T": _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1]),
            "N1": _measurements_frame(
                list(reversed(EPOCHS)), list(reversed(MJDS)), [3.0, 2.0, 1.0], [1.0, 1.0, 1.0]
            ),
            "N2": _measurements_frame(
                ["e1.fits", "e3.fits"], [60000.0, 60002.0], [3.0, 6.0], [1.0, 1.0]
            ),
        }
        target = make_source(frames, neighbor_ids=["N1", "N2"])

        result = target.calculate_relative_lightcurve()

        assert_allclose(result["relative_flux"], [1.0, 3.0, 2.0], rtol=1e-12)
        assert result["n_neighbors"] == 2

    def test_returns_empty_when_no_neighbors_found(self, make_source):
        frames = {"T": _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1])}
        target = make_source(frames, neighbor_ids=[])
        assert target.calculate_relative_lightcurve() == {}

    def test_returns_empty_below_min_neighbors(self, make_source):
        frames = {
            "T": _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1]),
            "N1": _measurements_frame(EPOCHS, MJDS, [1.0, 2.0, 3.0], [1.0, 1.0, 1.0]),
        }
        target = make_source(frames, neighbor_ids=["N1"])
        assert target.calculate_relative_lightcurve(min_neighbors=2) == {}

    def test_returns_empty_when_all_neighbors_fail_to_load(self, make_source, caplog):
        """Neighbors absent from `frames` raise on load; with every neighbor
        failing the method returns {} and logs the count and first error."""
        frames = {"T": _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1])}
        target = make_source(frames, neighbor_ids=["N1", "N2"])

        with caplog.at_level(logging.ERROR):
            result = target.calculate_relative_lightcurve()

        assert result == {}
        assert "All 2 neighbors failed to load" in caplog.text

    def test_failing_neighbor_is_skipped_when_others_load(self, make_source):
        """One bad neighbor of two: the survivor carries the ensemble alone,
        so R = T/N2 = [2, 3, 3] and only N2 is reported."""
        frames = {
            "T": _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1]),
            "N2": _measurements_frame(EPOCHS, MJDS, [1.0, 2.0, 3.0], [1.0, 1.0, 1.0]),
        }
        target = make_source(frames, neighbor_ids=["N1", "N2"])

        result = target.calculate_relative_lightcurve()

        assert_allclose(result["relative_flux"], [2.0, 3.0, 3.0], rtol=1e-12)
        assert result["n_neighbors"] == 1
        assert result["neighbor_ids"] == ["N2"]

    def test_returns_empty_when_required_columns_missing(self, make_source):
        frame = _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1])
        frames = {"T": frame.drop(columns=["image_path"])}
        target = make_source(frames, neighbor_ids=["N1"])
        assert target.calculate_relative_lightcurve() == {}

    def test_max_neighbors_truncates_neighbor_list(self, make_source):
        """With max_neighbors=2 only the first two ids are used: A over N1, N2
        is [2, 3, 4.5] -> R = [1, 2, 2] (same derivation as the happy path).
        If N3 (100x brighter) leaked past the truncation, three valid
        neighbors would also engage the weighted median and change every
        epoch's ensemble average."""
        frames = {
            "T": _measurements_frame(EPOCHS, MJDS, [2.0, 6.0, 9.0], [0.1, 0.1, 0.1]),
            "N1": _measurements_frame(EPOCHS, MJDS, [1.0, 2.0, 3.0], [1.0, 1.0, 1.0]),
            "N2": _measurements_frame(EPOCHS, MJDS, [3.0, 4.0, 6.0], [1.0, 1.0, 1.0]),
            "N3": _measurements_frame(EPOCHS, MJDS, [100.0, 100.0, 100.0], [1.0, 1.0, 1.0]),
        }
        target = make_source(frames, neighbor_ids=["N1", "N2", "N3"])

        result = target.calculate_relative_lightcurve(max_neighbors=2)

        assert result["neighbor_ids"] == ["N1", "N2"]
        assert result["n_neighbors"] == 2
        assert_allclose(result["relative_flux"], [1.0, 2.0, 2.0], rtol=1e-12)


class TestNullColumnRobustness:
    """All-NULL SQLite REAL columns arrive from pandas as object-dtype None
    arrays (findings of the #124 adversarial review). The loader must coerce
    every flux/err column to float, normalized columns are selected only when
    flux AND error carry finite values, and neighbors with no usable flux or
    error are skipped instead of crashing or silently producing all-NaN
    relative fluxes. Partially normalized tables are an expected mid-pipeline
    state (photometry/normalize.py filters WHERE normalized_flux_jy IS NOT
    NULL), so these are realistic DB states, not corruption."""

    def test_all_null_columns_load_as_float(self, tmp_path):
        db = _make_products_db(
            tmp_path / "products.sqlite3",
            [
                ("SRC1", 180.0, 16.1, 500.0, 0.5, None, 1.7e9, 60000.0, "e1.fits", None, None),
                (
                    "SRC1",
                    180.0,
                    16.1,
                    500.0,
                    0.6,
                    None,
                    1.7e9 + 3600,
                    60000.042,
                    "e2.fits",
                    None,
                    None,
                ),
            ],
            with_normalized=True,
        )
        src = Source("SRC1", products_db=db)
        for col in (
            "peak_jyb",
            "peak_err_jyb",
            "normalized_flux_jy",
            "normalized_flux_err_jy",
        ):
            assert src.measurements[col].dtype == np.float64, col

    def test_neighbor_with_all_null_normalized_is_skipped(self, tmp_path):
        """Target fully normalized; the only selected neighbor has NULL
        normalized columns. No usable neighbor remains, so the result is {}
        (pre-fix: TypeError from None**2 on the object-dtype error matrix,
        uncaught because it fires after the per-neighbor try/except)."""
        rows = [
            ("T0", 180.0, 16.1, 500.0, 0.4, 0.01, 1.7e9, 60000.0, "e1.fits", 0.4, 0.01),
            (
                "T0",
                180.0,
                16.1,
                500.0,
                0.6,
                0.01,
                1.7e9 + 3600,
                60000.042,
                "e2.fits",
                0.6,
                0.01,
            ),
            ("GOOD", 180.1, 16.15, 200.0, 0.2, 0.02, 1.7e9, 60000.0, "e1.fits", None, None),
            (
                "GOOD",
                180.1,
                16.15,
                200.0,
                0.2,
                0.02,
                1.7e9 + 3600,
                60000.042,
                "e2.fits",
                None,
                None,
            ),
        ]
        stats = [("GOOD", 180.1, 16.15, 200.0, 0.5, 20)]
        db = _make_products_db(tmp_path / "products.sqlite3", rows, stats, with_normalized=True)
        src = Source("T0", products_db=db)

        assert src.calculate_relative_lightcurve() == {}

    def test_target_normalized_err_all_null_falls_back_to_peak(self, tmp_path):
        """normalized_flux_jy populated but normalized_flux_err_jy all NULL:
        weighted metrics are impossible in the normalized system, so column
        selection falls back to peak — v = 0.5 and eta = 100 from the peak
        f=[1,2,3], e=0.1 derivation (criterion 4). Pre-fix: TypeError from
        np.isfinite on the object-dtype error column."""
        rows = [
            ("SRC1", 180.0, 16.1, 500.0, 1.0, 0.1, 1.7e9, 60000.0, "e1.fits", 1.0, None),
            ("SRC1", 180.0, 16.1, 500.0, 2.0, 0.1, 1.7e9 + 3600, 60000.042, "e2.fits", 2.0, None),
            ("SRC1", 180.0, 16.1, 500.0, 3.0, 0.1, 1.7e9 + 7200, 60000.083, "e3.fits", 3.0, None),
        ]
        db = _make_products_db(tmp_path / "products.sqlite3", rows, with_normalized=True)
        src = Source("SRC1", products_db=db)

        metrics = src.calc_variability_metrics()

        assert_allclose(metrics["v"], 0.5, rtol=1e-12)
        assert_allclose(metrics["eta"], 100.0, rtol=1e-12)

    def test_raw_db_neighbor_with_null_errors_is_skipped(self, tmp_path):
        """Raw DB: the neighbor's peak_err_jyb is all NULL, so inverse-
        variance weighting cannot use it; the neighbor is skipped and with no
        usable neighbor left the result is {} (pre-fix: TypeError None**2)."""
        rows = [
            ("T0", 180.0, 16.1, 500.0, 0.4, 0.01, 1.7e9, 60000.0, "e1.fits"),
            ("T0", 180.0, 16.1, 500.0, 0.6, 0.01, 1.7e9 + 3600, 60000.042, "e2.fits"),
            ("GOOD", 180.1, 16.15, 200.0, 0.2, None, 1.7e9, 60000.0, "e1.fits"),
            ("GOOD", 180.1, 16.15, 200.0, 0.2, None, 1.7e9 + 3600, 60000.042, "e2.fits"),
        ]
        stats = [("GOOD", 180.1, 16.15, 200.0, 0.5, 20)]
        db = _make_products_db(tmp_path / "products.sqlite3", rows, stats)
        src = Source("T0", products_db=db)

        assert src.calculate_relative_lightcurve() == {}


class TestRawDbEndToEnd:
    """Issue #118 item 1: the full path DB -> stable neighbors -> relative
    flux must run on a raw products DB (peak fluxes only, nothing
    pre-normalized)."""

    def test_relative_lightcurve_from_raw_products_db(self, tmp_path):
        """T0 peak fluxes [0.4, 0.6] (median 0.5 Jy -> 500 mJy); single
        neighbor GOOD with constant peak 0.2 at (180.1, 16.15), separation
        ~0.11 deg, flux ratio 0.4 — passes every filter. One neighbor makes
        the ensemble average the neighbor itself: R = [2, 3], mean 2.5,
        std(ddof=0) = 0.5."""
        rows = [
            ("T0", 180.0, 16.1, 500.0, 0.4, 0.01, 1.7e9, 60000.0, "e1.fits"),
            ("T0", 180.0, 16.1, 500.0, 0.6, 0.01, 1.7e9 + 3600, 60000.042, "e2.fits"),
            ("GOOD", 180.1, 16.15, 200.0, 0.2, 0.02, 1.7e9, 60000.0, "e1.fits"),
            ("GOOD", 180.1, 16.15, 200.0, 0.2, 0.02, 1.7e9 + 3600, 60000.042, "e2.fits"),
        ]
        stats = [("GOOD", 180.1, 16.15, 200.0, 0.5, 20)]
        db = _make_products_db(tmp_path / "products.sqlite3", rows, stats)
        src = Source("T0", products_db=db)

        result = src.calculate_relative_lightcurve()

        assert result["neighbor_ids"] == ["GOOD"]
        assert result["n_neighbors"] == 1
        assert_allclose(result["relative_flux"], [2.0, 3.0], rtol=1e-12)
        assert_allclose(result["relative_flux_mean"], 2.5, rtol=1e-12)
        assert_allclose(result["relative_flux_std"], 0.5, rtol=1e-12)
        assert_allclose(result["mjds"], [60000.0, 60000.042], rtol=1e-12)


class TestCalcVariabilityMetrics:
    """Criterion 4: metric wiring reproduces the Mooley et al. closed forms."""

    def test_metrics_match_hand_derivation(self, make_source):
        """f = [1, 2, 3], e = 0.1 everywhere:

        v  = std(f, ddof=1)/mean(f) = 1/2 — the canonical VAST/ddof=1
             convention (see module docstring, criterion 4)
        eta = reduced chi-squared about the weighted mean (= 2):
              ((-1)^2 + 0 + 1^2)/0.01 / (N-1) = 200/2 = 100
        Vs pairs (1,2), (2,3): each (f_a - f_b)/sqrt(2*0.01) = -1/sqrt(0.02)
        m  pairs: 2(-1)/3 and 2(-1)/5, mean = -8/15
        """
        frames = {"T": _measurements_frame(EPOCHS, MJDS, [1.0, 2.0, 3.0], [0.1, 0.1, 0.1])}
        src = make_source(frames)

        metrics = src.calc_variability_metrics()

        assert_allclose(metrics["v"], 0.5, rtol=1e-12)
        assert_allclose(metrics["eta"], 100.0, rtol=1e-12)
        assert_allclose(metrics["vs_mean"], -1.0 / np.sqrt(0.02), rtol=1e-12)
        assert_allclose(metrics["m_mean"], -8.0 / 15.0, rtol=1e-12)
        assert metrics["n_epochs"] == 3

    def test_fewer_than_two_measurements_returns_defaults(self, make_source):
        frames = {"T": _measurements_frame(EPOCHS[:1], MJDS[:1], [1.0], [0.1])}
        src = make_source(frames)
        assert src.calc_variability_metrics() == {
            "v": 0.0,
            "eta": 0.0,
            "vs_mean": None,
            "m_mean": None,
            "n_epochs": 1,
        }

    def test_eta_is_none_when_eta_calculation_fails(self, make_source, monkeypatch, caplog):
        """The eta failure branch deliberately reports None (not 0.0) so an
        uncomputable source is never labeled stable; the other metrics must
        still come back. Pre-#118 this branch raised TypeError on float(None)."""
        frames = {"T": _measurements_frame(EPOCHS, MJDS, [1.0, 2.0, 3.0], [0.1, 0.1, 0.1])}
        src = make_source(frames)

        def boom(*args, **kwargs):
            raise ValueError("synthetic eta failure")

        monkeypatch.setattr("dsa110_continuum.photometry.source.calculate_eta_metric", boom)

        with caplog.at_level(logging.WARNING):
            metrics = src.calc_variability_metrics()

        assert metrics["eta"] is None
        assert_allclose(metrics["v"], 0.5, rtol=1e-12)
        assert_allclose(metrics["vs_mean"], -1.0 / np.sqrt(0.02), rtol=1e-12)
        assert "Failed to calculate" in caplog.text


def _make_products_db(path, photometry_rows, stats_rows=(), with_normalized=False):
    conn = sqlite3.connect(str(path))
    normalized_cols = (
        ", normalized_flux_jy REAL, normalized_flux_err_jy REAL" if with_normalized else ""
    )
    conn.execute(
        f"""CREATE TABLE photometry (
            source_id TEXT, ra_deg REAL, dec_deg REAL, nvss_flux_mjy REAL,
            peak_jyb REAL, peak_err_jyb REAL, measured_at REAL, mjd REAL,
            image_path TEXT{normalized_cols})"""
    )
    placeholders = ",".join("?" * (11 if with_normalized else 9))
    conn.executemany(f"INSERT INTO photometry VALUES ({placeholders})", photometry_rows)
    if stats_rows:
        conn.execute(
            """CREATE TABLE variability_stats (
                source_id TEXT, ra_deg REAL, dec_deg REAL, mean_flux_mjy REAL,
                eta_metric REAL, n_obs INTEGER)"""
        )
        conn.executemany("INSERT INTO variability_stats VALUES (?,?,?,?,?,?)", stats_rows)
    conn.commit()
    conn.close()
    return path


class TestLoadMeasurements:
    """DB-backed loading: schema handling, ordering, and failure modes."""

    def test_loads_rows_and_coordinates_from_db(self, tmp_path):
        db = _make_products_db(
            tmp_path / "products.sqlite3",
            # inserted out of chronological order so the expected row order
            # can only come from ORDER BY measured_at, not insertion order
            [
                ("SRC1", 180.0, 16.1, 500.0, 0.6, 0.01, 1.7e9 + 3600, 60000.042, "e2.fits"),
                ("SRC1", 180.0, 16.1, 500.0, 0.5, 0.01, 1.7e9, 60000.0, "e1.fits"),
            ],
        )
        src = Source("SRC1", products_db=db)

        assert src.n_epochs == 2
        assert src.ra_deg == 180.0
        assert src.dec_deg == 16.1
        assert list(src.measurements["image_path"]) == ["e1.fits", "e2.fits"]
        assert_allclose(src.measurements["peak_jyb"], [0.5, 0.6], rtol=1e-12)
        # This table has no normalized columns, so the loader NaN-fills them;
        # downstream column selection falls back to peak_jyb (criterion 5).
        assert src.measurements["normalized_flux_jy"].isna().all()

    def test_loads_normalized_columns_when_present_in_db(self, tmp_path):
        """A products DB whose photometry table carries normalized columns
        must surface their values (pre-#118 the loader NaN-filled them
        unconditionally, so no DB could ever feed the normalized path)."""
        db = _make_products_db(
            tmp_path / "products.sqlite3",
            [
                ("SRC1", 180.0, 16.1, 500.0, 0.5, 0.01, 1.7e9, 60000.0, "e1.fits", 0.45, 0.02),
                (
                    "SRC1",
                    180.0,
                    16.1,
                    500.0,
                    0.6,
                    0.01,
                    1.7e9 + 3600,
                    60000.042,
                    "e2.fits",
                    0.55,
                    0.03,
                ),
            ],
            with_normalized=True,
        )
        src = Source("SRC1", products_db=db)

        assert_allclose(src.measurements["normalized_flux_jy"], [0.45, 0.55], rtol=1e-12)
        assert_allclose(src.measurements["normalized_flux_err_jy"], [0.02, 0.03], rtol=1e-12)

    def test_mjd_recomputed_from_measured_at_when_all_null(self, tmp_path):
        """mjd = 40587 + measured_at/86400 exactly (module docstring,
        criterion 5: Unix epoch = MJD 40587.0, shared 86400 s civil days)."""
        t0 = 1.7e9
        db = _make_products_db(
            tmp_path / "products.sqlite3",
            [
                ("SRC1", 180.0, 16.1, 500.0, 0.5, 0.01, t0, None, "e1.fits"),
                ("SRC1", 180.0, 16.1, 500.0, 0.6, 0.01, t0 + 3600, None, "e2.fits"),
            ],
        )
        src = Source("SRC1", products_db=db)

        expected = [40587.0 + t0 / 86400.0, 40587.0 + (t0 + 3600.0) / 86400.0]
        assert_allclose(src.measurements["mjd"].astype(float), expected, rtol=1e-12)

    def test_photometry_table_without_source_id_yields_empty(self, tmp_path):
        """A schema without source_id cannot be queried per-source; the loader
        returns no measurements rather than guessing (coords supplied so the
        constructor does not raise)."""
        db = tmp_path / "products.sqlite3"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE photometry (ra_deg REAL, dec_deg REAL, peak_jyb REAL)")
        conn.commit()
        conn.close()

        src = Source("SRC1", ra_deg=180.0, dec_deg=16.1, products_db=db)
        assert src.measurements.empty

    def test_missing_photometry_table_yields_empty(self, tmp_path):
        db = tmp_path / "products.sqlite3"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unrelated (x REAL)")
        conn.commit()
        conn.close()

        src = Source("SRC1", ra_deg=180.0, dec_deg=16.1, products_db=db)
        assert src.measurements.empty

    def test_missing_db_raises(self, tmp_path):
        with pytest.raises(SourceError, match="not found"):
            Source("SRC1", products_db=tmp_path / "missing.sqlite3")

    def test_no_measurements_and_no_coords_raises(self, tmp_path):
        db = _make_products_db(
            tmp_path / "products.sqlite3",
            [("OTHER", 180.0, 16.1, 500.0, 0.5, 0.01, 1.7e9, 60000.0, "e1.fits")],
        )
        with pytest.raises(SourceError, match="No measurements found"):
            Source("SRC1", products_db=db)


class TestFindStableNeighbors:
    """Neighbor selection: radius, flux-ratio, eta, and epoch-count filters."""

    TARGET_ROWS = [
        ("T0", 180.0, 16.1, 500.0, 0.5, 0.01, 1.7e9, 60000.0, "e1.fits"),
        ("T0", 180.0, 16.1, 500.0, 0.5, 0.01, 1.7e9 + 3600, 60000.042, "e2.fits"),
    ]
    # (source_id, ra, dec, mean_flux_mjy, eta, n_obs) against a 500 mJy target
    # at (180.0, 16.1) with defaults radius=0.5 deg, ratio in [0.1, 10],
    # eta < 1.5, n_obs >= 10.
    STATS_ROWS = [
        ("GOOD", 180.1, 16.15, 400.0, 0.5, 20),  # passes every filter
        ("FAR", 185.0, 16.1, 400.0, 0.5, 20),  # 4.8 deg away: outside radius
        ("VARIABLE", 180.1, 16.05, 400.0, 5.0, 20),  # eta 5.0 >= 1.5
        ("FAINT", 180.05, 16.1, 10.0, 0.5, 20),  # ratio 0.02 < 0.1
        ("FEW", 180.05, 16.12, 400.0, 0.5, 3),  # n_obs 3 < 10
        ("T0", 180.0, 16.1, 500.0, 0.5, 20),  # the target itself is excluded
    ]

    def _target(self, tmp_path):
        db = _make_products_db(tmp_path / "products.sqlite3", self.TARGET_ROWS, self.STATS_ROWS)
        return Source("T0", products_db=db)

    def test_selects_only_stable_similar_flux_neighbors_within_radius(self, tmp_path):
        src = self._target(tmp_path)
        # Populate normalized flux the way the normalization step would;
        # selection then prefers the normalized column (criterion 5).
        src.measurements["normalized_flux_jy"] = src.measurements["peak_jyb"]
        assert src.find_stable_neighbors() == ["GOOD"]

    def test_falls_back_to_peak_flux_when_normalized_all_nan(self, tmp_path):
        """A raw products DB has no normalized columns, so the loader NaN-fills
        them; neighbor selection must fall back to the populated peak_jyb
        (median 0.5 Jy -> 500 mJy target flux) instead of refusing with an
        undefined flux-ratio filter. Pre-#118 this returned [] and made
        relative photometry unreachable from a raw DB (issue #118 item 1)."""
        src = self._target(tmp_path)
        assert src.measurements["normalized_flux_jy"].isna().all()
        assert src.find_stable_neighbors() == ["GOOD"]

    def test_ra_wrap_neighbor_across_zero_meridian(self, tmp_path):
        """Target at RA 0.2 deg: the RA box (half-width 0.5/cos(16.1) ~ 0.52)
        wraps below 0, so the SQL clause becomes (ra >= 359.68 OR ra <= 0.72).
        WRAP at RA 359.9 is dRA = 0.3 deg away — haversine separation
        0.3*cos(16.1) ~ 0.288 deg < 0.5 — and must be selected. NOWRAP at
        RA 5.0 (~4.6 deg away) stays excluded by the SQL box. Pre-#118 the
        planar post-filter computed |359.9 - 0.2| = 359.7 deg and rejected
        every wrapped candidate the SQL clause had correctly found
        (criterion 6)."""
        rows = [
            ("T0", 0.2, 16.1, 500.0, 0.5, 0.01, 1.7e9, 60000.0, "e1.fits"),
            ("T0", 0.2, 16.1, 500.0, 0.5, 0.01, 1.7e9 + 3600, 60000.042, "e2.fits"),
        ]
        stats = [
            ("WRAP", 359.9, 16.1, 400.0, 0.5, 20),
            ("NOWRAP", 5.0, 16.1, 400.0, 0.5, 20),
        ]
        db = _make_products_db(tmp_path / "products.sqlite3", rows, stats)
        src = Source("T0", products_db=db)

        assert src.find_stable_neighbors() == ["WRAP"]

    def test_pole_search_finds_neighbor_across_ra(self, tmp_path):
        """Target at dec 89.7 (colatitude 0.3 deg) triggers the all-RA pole
        branch. POLE at the antipodal RA, dec 89.9 (colatitude 0.1) sits on
        the same great circle through the pole: separation 0.3 + 0.1 = 0.4 deg
        < 0.5, so it must be selected. POLEFAR at RA+90, dec 89.25 has
        small-angle separation sqrt(0.3^2 + 0.75^2) ~ 0.808 deg > 0.5 and is
        excluded by the post-filter even though it passes the all-RA box.
        Pre-#118 the planar post-filter rejected POLE — its RA term alone,
        (180*cos 89.7)^2, exceeds radius^2 (criterion 6)."""
        rows = [
            ("T0", 180.0, 89.7, 500.0, 0.5, 0.01, 1.7e9, 60000.0, "e1.fits"),
            ("T0", 180.0, 89.7, 500.0, 0.5, 0.01, 1.7e9 + 3600, 60000.042, "e2.fits"),
        ]
        stats = [
            ("POLE", 0.0, 89.9, 400.0, 0.5, 20),
            ("POLEFAR", 270.0, 89.25, 400.0, 0.5, 20),
        ]
        db = _make_products_db(tmp_path / "products.sqlite3", rows, stats)
        src = Source("T0", products_db=db)

        assert src.find_stable_neighbors() == ["POLE"]
