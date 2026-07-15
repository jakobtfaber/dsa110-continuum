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
   Mooley et al. (2016) closed forms already pinned canonically in
   tests/test_metrics_canonical.py: v = std(f, ddof=0)/mean(f), eta = reduced
   chi-squared about the weighted mean, Vs = (f_a - f_b)/sqrt(e_a^2 + e_b^2),
   m = 2(f_a - f_b)/(f_a + f_b) over sequential epoch pairs.

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


class TestCalcVariabilityMetrics:
    """Criterion 4: metric wiring reproduces the Mooley et al. closed forms."""

    def test_metrics_match_hand_derivation(self, make_source):
        """f = [1, 2, 3], e = 0.1 everywhere:

        v  = std(f, ddof=0)/mean(f) = sqrt(2/3)/2
        eta = reduced chi-squared about the weighted mean (= 2):
              ((-1)^2 + 0 + 1^2)/0.01 / (N-1) = 200/2 = 100
        Vs pairs (1,2), (2,3): each (f_a - f_b)/sqrt(2*0.01) = -1/sqrt(0.02)
        m  pairs: 2(-1)/3 and 2(-1)/5, mean = -8/15
        """
        frames = {"T": _measurements_frame(EPOCHS, MJDS, [1.0, 2.0, 3.0], [0.1, 0.1, 0.1])}
        src = make_source(frames)

        metrics = src.calc_variability_metrics()

        assert_allclose(metrics["v"], np.sqrt(2.0 / 3.0) / 2.0, rtol=1e-12)
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


def _make_products_db(path, photometry_rows, stats_rows=()):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE photometry (
            source_id TEXT, ra_deg REAL, dec_deg REAL, nvss_flux_mjy REAL,
            peak_jyb REAL, peak_err_jyb REAL, measured_at REAL, mjd REAL,
            image_path TEXT)"""
    )
    conn.executemany("INSERT INTO photometry VALUES (?,?,?,?,?,?,?,?,?)", photometry_rows)
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
            [
                ("SRC1", 180.0, 16.1, 500.0, 0.5, 0.01, 1.7e9, 60000.0, "e1.fits"),
                ("SRC1", 180.0, 16.1, 500.0, 0.6, 0.01, 1.7e9 + 3600, 60000.042, "e2.fits"),
            ],
        )
        src = Source("SRC1", products_db=db)

        assert src.n_epochs == 2
        assert src.ra_deg == 180.0
        assert src.dec_deg == 16.1
        assert list(src.measurements["image_path"]) == ["e1.fits", "e2.fits"]
        assert_allclose(src.measurements["peak_jyb"], [0.5, 0.6], rtol=1e-12)
        # The loader does not populate normalized fluxes from the photometry
        # table; the columns exist but are NaN until a caller normalizes.
        assert src.measurements["normalized_flux_jy"].isna().all()

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
        # The loader leaves normalized_flux_jy NaN; populate it the way the
        # normalization step would before neighbor selection.
        src.measurements["normalized_flux_jy"] = src.measurements["peak_jyb"]
        assert src.find_stable_neighbors() == ["GOOD"]

    def test_returns_empty_when_target_flux_unknown(self, tmp_path):
        """With normalized_flux_jy all-NaN the flux-ratio filter is undefined,
        so neighbor selection refuses to guess and returns no neighbors."""
        src = self._target(tmp_path)
        assert src.find_stable_neighbors() == []
