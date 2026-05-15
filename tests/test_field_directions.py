"""Tests for FIELD direction column shape handling."""

from __future__ import annotations

import numpy as np
import pytest
from dsa110_continuum.calibration.field_directions import (
    extract_field_ra_dec,
    set_field_ra_dec,
)


@pytest.mark.parametrize(
    ("direction_col", "expected_ra_deg", "expected_dec_deg"),
    [
        (
            np.array([
                [[np.radians(10.0), np.radians(20.0)]],
                [[np.radians(11.0), np.radians(21.0)]],
            ]),
            [10.0, 11.0],
            [20.0, 21.0],
        ),
        (
            np.array([
                [[np.radians(10.0)], [np.radians(20.0)]],
                [[np.radians(11.0)], [np.radians(21.0)]],
            ]),
            [10.0, 11.0],
            [20.0, 21.0],
        ),
        (
            np.array([
                [np.radians(10.0), np.radians(20.0)],
                [np.radians(11.0), np.radians(21.0)],
            ]),
            [10.0, 11.0],
            [20.0, 21.0],
        ),
    ],
)
def test_extract_field_ra_dec_supported_shapes(direction_col, expected_ra_deg, expected_dec_deg):
    ra_rad, dec_rad = extract_field_ra_dec(direction_col)

    np.testing.assert_allclose(np.degrees(ra_rad), expected_ra_deg)
    np.testing.assert_allclose(np.degrees(dec_rad), expected_dec_deg)


@pytest.mark.parametrize(
    "direction_col",
    [
        np.zeros((2, 1, 2), dtype=np.float64),
        np.zeros((2, 2, 1), dtype=np.float64),
        np.zeros((2, 2), dtype=np.float64),
    ],
)
def test_set_field_ra_dec_preserves_supported_shapes(direction_col):
    updated = set_field_ra_dec(direction_col, np.radians(12.0), np.radians(34.0))

    assert updated.shape == direction_col.shape
    ra_rad, dec_rad = extract_field_ra_dec(updated)
    np.testing.assert_allclose(np.degrees(ra_rad), [12.0, 12.0])
    np.testing.assert_allclose(np.degrees(dec_rad), [34.0, 34.0])


def test_extract_field_ra_dec_unsupported_shape_raises():
    with pytest.raises(ValueError, match="Unsupported FIELD direction column shape"):
        extract_field_ra_dec(np.zeros((2, 3, 4)))


def test_set_field_ra_dec_unsupported_shape_raises():
    with pytest.raises(ValueError, match="Unsupported FIELD direction column shape"):
        set_field_ra_dec(np.zeros((2, 3, 4)), np.radians(12.0), np.radians(34.0))
