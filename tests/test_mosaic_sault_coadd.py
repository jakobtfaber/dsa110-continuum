"""Tests for per-pixel Sault (inverse-variance PB^2) weighting in the production coadd.

Tiles written here are PB-corrected (`-image-pb.fits`) with a flat-noise
sibling (`-image.fits` = sky * PB), matching the WSClean product layout the
production coadd consumes.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from dsa110_continuum.mosaic.production import (
    PB_CUTOFF,
    _pb_map_for_tile,
    coadd_tiles,
)

SHAPE = (32, 32)


def _tile_wcs(ra_deg: float = 10.0, dec_deg: float = 16.0) -> WCS:
    ny, nx = SHAPE
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [nx / 2 + 0.5, ny / 2 + 0.5]
    wcs.wcs.crval = [ra_deg, dec_deg]
    wcs.wcs.cdelt = [-1.0 / 60.0, 1.0 / 60.0]
    wcs.wcs.ctype = ["RA---SIN", "DEC--SIN"]
    return wcs


def _gaussian_pb(center: tuple[float, float], fwhm_px: float = 20.0) -> np.ndarray:
    yy, xx = np.indices(SHAPE, dtype=np.float64)
    r2 = (yy - center[0]) ** 2 + (xx - center[1]) ** 2
    return np.exp(-4.0 * np.log(2.0) * r2 / fwhm_px**2)


def _write_pair(
    tmp_path: Path,
    stem: str,
    sky: np.ndarray,
    pb: np.ndarray,
    *,
    write_flat: bool = True,
) -> Path:
    """Write `-image-pb.fits` (= sky) and optionally `-image.fits` (= sky * PB)."""
    header = _tile_wcs().to_header()
    pb_path = tmp_path / f"{stem}-image-pb.fits"
    fits.PrimaryHDU(data=sky.astype(np.float64), header=header).writeto(pb_path)
    if write_flat:
        flat_path = tmp_path / f"{stem}-image.fits"
        fits.PrimaryHDU(data=(sky * pb).astype(np.float64), header=header).writeto(flat_path)
    return pb_path


def _coadd(paths: list[Path]) -> np.ndarray:
    wcs = _tile_wcs()
    ny, nx = SHAPE
    return coadd_tiles([str(p) for p in paths], wcs, ny, nx)


def test_pb_ratio_recovers_applied_beam(tmp_path):
    pb = _gaussian_pb((16.0, 16.0))
    sky = np.full(SHAPE, 2.5)
    tile = _write_pair(tmp_path, "t0", sky, pb)

    with fits.open(tile) as hdul:
        data = hdul[0].data.squeeze().astype(np.float64)
    recovered, source = _pb_map_for_tile(str(tile), data)

    assert source == "image/image-pb ratio"
    assert np.allclose(recovered, pb / pb.max(), rtol=1e-10, equal_nan=True)


def test_constant_sky_is_flux_preserved(tmp_path):
    """A constant sky must come back unchanged — catches double PB correction."""
    sky_value = 3.0
    sky = np.full(SHAPE, sky_value)
    tiles = [
        _write_pair(tmp_path, "a", sky, _gaussian_pb((16.0, 10.0))),
        _write_pair(tmp_path, "b", sky, _gaussian_pb((16.0, 22.0))),
    ]

    mosaic = _coadd(tiles)

    covered = np.isfinite(mosaic)
    assert covered.any()
    assert np.allclose(mosaic[covered], sky_value, rtol=1e-6)


def test_overlap_pixel_matches_pb_squared_formula(tmp_path):
    """At an overlap pixel the mosaic must equal sum(PB^2 I) / sum(PB^2)."""
    pb_a = _gaussian_pb((16.0, 10.0))
    pb_b = _gaussian_pb((16.0, 22.0))
    tiles = [
        _write_pair(tmp_path, "a", np.full(SHAPE, 2.0), pb_a),
        _write_pair(tmp_path, "b", np.full(SHAPE, 6.0), pb_b),
    ]

    mosaic = _coadd(tiles)

    # Replicate the coadd's own scalar weight: 1 / std(flat-noise plane)^2,
    # measured over the central box (margin 200 covers the whole 32x32 tile).
    def w_tile(sky_value: float, pb: np.ndarray) -> float:
        return 1.0 / float(np.std(sky_value * pb)) ** 2

    probe = (16, 16)
    wa = w_tile(2.0, pb_a) * pb_a[probe] ** 2
    wb = w_tile(6.0, pb_b) * pb_b[probe] ** 2
    assert pb_a[probe] > PB_CUTOFF and pb_b[probe] > PB_CUTOFF
    expected = (wa * 2.0 + wb * 6.0) / (wa + wb)
    assert mosaic[probe] == pytest.approx(expected, rel=1e-6)


def test_pb_floor_excludes_low_gain_tile(tmp_path):
    """Below PB_CUTOFF a tile's weight is zero — the other tile wins outright."""
    pb_a = _gaussian_pb((16.0, 10.0))
    pb_b = np.full(SHAPE, 0.9)
    probe = (16, 30)
    assert pb_a[probe] < PB_CUTOFF
    tiles = [
        _write_pair(tmp_path, "a", np.full(SHAPE, 2.0), pb_a),
        _write_pair(tmp_path, "b", np.full(SHAPE, 6.0), pb_b),
    ]

    mosaic = _coadd(tiles)

    assert mosaic[probe] == pytest.approx(6.0, rel=1e-6)


def test_all_tiles_below_floor_yields_nan(tmp_path):
    """Preserves the legacy blanking pin: no weight anywhere -> NaN pixel.

    The ratio-derived PB is peak-normalized, so the background is set to 1.0
    to keep the low pixel's post-normalization value below the floor.
    """
    pb = np.full(SHAPE, 1.0)
    pb[8, 8] = PB_CUTOFF - 0.05
    tiles = [_write_pair(tmp_path, "a", np.full(SHAPE, 1.0), pb)]

    mosaic = _coadd(tiles)

    assert np.isnan(mosaic[8, 8])
    assert np.isfinite(mosaic[7, 7])


def test_band_average_beam_fallback(tmp_path):
    """Without a flat sibling, the PB is the mean of all -beam-N maps, not beam-0."""
    sky = np.full(SHAPE, 1.0)
    tile = _write_pair(tmp_path, "t0", sky, np.ones(SHAPE), write_flat=False)
    header = _tile_wcs().to_header()
    beam0 = np.full(SHAPE, 0.4)
    beam9 = np.full(SHAPE, 0.8)
    fits.PrimaryHDU(data=beam0, header=header).writeto(tmp_path / "t0-beam-0.fits")
    fits.PrimaryHDU(data=beam9, header=header).writeto(tmp_path / "t0-beam-9.fits")

    with fits.open(tile) as hdul:
        data = hdul[0].data.squeeze().astype(np.float64)
    pb, source = _pb_map_for_tile(str(tile), data)

    assert "band-average of 2" in source
    # mean = 0.6, normalized to peak -> flat 1.0 map
    assert np.allclose(pb, 1.0)


def test_no_pb_source_falls_back_to_uniform_weight(tmp_path):
    """No flat sibling and no beam maps: tiles combine with uniform weight."""
    tiles = [
        _write_pair(tmp_path, "a", np.full(SHAPE, 1.0), np.ones(SHAPE), write_flat=False),
        _write_pair(tmp_path, "b", np.full(SHAPE, 3.0), np.ones(SHAPE), write_flat=False),
    ]

    mosaic = _coadd(tiles)

    covered = np.isfinite(mosaic)
    assert covered.any()
    assert np.allclose(mosaic[covered], 2.0, rtol=1e-6)
