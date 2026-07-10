"""Tests for the production hourly-epoch coadd package entry."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))


def _write_tile(
    path: Path,
    *,
    ra_deg: float,
    dec_deg: float = 16.0,
    value: float = 1.0,
    shape: tuple[int, int] = (16, 16),
) -> Path:
    wcs = WCS(naxis=2)
    ny, nx = shape
    wcs.wcs.crpix = [nx / 2 + 0.5, ny / 2 + 0.5]
    wcs.wcs.crval = [ra_deg, dec_deg]
    wcs.wcs.cdelt = [-1.0 / 60.0, 1.0 / 60.0]
    wcs.wcs.ctype = ["RA---SIN", "DEC--SIN"]
    header = wcs.to_header()
    header["NAXIS"] = 2
    header["NAXIS1"] = nx
    header["NAXIS2"] = ny
    fits.PrimaryHDU(
        data=np.full(shape, value, dtype=np.float32),
        header=header,
    ).writeto(path)
    return path


def _write_beam_for_tile(tile_path: Path, beam: np.ndarray) -> Path:
    beam_path = Path(str(tile_path).replace("-image-pb.fits", "-beam-0.fits"))
    with fits.open(tile_path) as hdul:
        header = hdul[0].header.copy()
    fits.PrimaryHDU(data=beam.astype(np.float32), header=header).writeto(beam_path)
    return beam_path


def test_production_coadd_blanks_wsclean_beam_pixels_below_twenty_percent(tmp_path):
    from dsa110_continuum.mosaic.production import (
        coadd_tiles,
    )

    tile = _write_tile(tmp_path / "2026-01-25T22:26:05-image-pb.fits", ra_deg=10.0)
    beam = np.ones((16, 16), dtype=np.float32)
    beam[8, 8] = 0.19
    _write_beam_for_tile(tile, beam)

    with fits.open(tile) as hdul:
        out_wcs = WCS(hdul[0].header).celestial
        ny, nx = hdul[0].data.squeeze().shape
    mosaic = coadd_tiles([str(tile)], out_wcs, ny, nx)

    assert np.isnan(mosaic[8, 8])
    assert np.isfinite(mosaic[7, 7])


def test_production_strip_grouping_preserves_wrap_contiguity(tmp_path):
    from dsa110_continuum.mosaic.production import group_tiles_by_ra

    near_wrap_a = _write_tile(tmp_path / "a-image-pb.fits", ra_deg=358.0)
    near_wrap_b = _write_tile(tmp_path / "b-image-pb.fits", ra_deg=2.0)
    far = _write_tile(tmp_path / "c-image-pb.fits", ra_deg=40.0)

    groups = group_tiles_by_ra([str(near_wrap_a), str(near_wrap_b), str(far)])

    assert [len(group) for group in groups] == [2, 1]
    assert {Path(path).name for path in groups[0]} == {
        near_wrap_a.name,
        near_wrap_b.name,
    }


def test_production_common_wcs_keeps_ra_wrap_compact(tmp_path):
    from dsa110_continuum.mosaic.production import build_common_wcs

    tiles = [
        _write_tile(tmp_path / "a-image-pb.fits", ra_deg=355.0),
        _write_tile(tmp_path / "b-image-pb.fits", ra_deg=5.0),
    ]

    out_wcs, _ny, nx = build_common_wcs([str(path) for path in tiles])

    assert nx < 10000
    assert min(abs(out_wcs.wcs.crval[0]), abs(out_wcs.wcs.crval[0] - 360.0)) < 5.0


def test_batch_epoch_coadd_helper_calls_package_entry(monkeypatch):
    import batch_pipeline as bp
    import dsa110_continuum.mosaic.production as production
    import mosaic_day

    called: dict[str, list[str]] = {}

    def fake_build_epoch_coadd(tile_paths: list[str]):
        called["tile_paths"] = tile_paths
        return np.zeros((2, 2), dtype=np.float32), WCS(naxis=2)

    def fail_legacy_coadd(*_args, **_kwargs):
        raise AssertionError("batch helper should not call mosaic_day.coadd_tiles")

    monkeypatch.setattr(production, "build_epoch_coadd", fake_build_epoch_coadd)
    monkeypatch.setattr(mosaic_day, "coadd_tiles", fail_legacy_coadd)

    mosaic, out_wcs = bp._build_epoch_coadd(["tile-a.fits", "tile-b.fits"])

    assert called["tile_paths"] == ["tile-a.fits", "tile-b.fits"]
    assert mosaic.shape == (2, 2)
    assert isinstance(out_wcs, WCS)


def test_batch_epoch_product_helper_calls_product_entry(monkeypatch):
    import batch_pipeline as bp
    import dsa110_continuum.mosaic.production as production

    expected = production.ProductionCoaddResult(
        mosaic=np.zeros((2, 2)),
        weight=np.ones((2, 2)),
        wcs=WCS(naxis=2),
    )
    called: dict[str, list[str]] = {}

    def fake_build_epoch_coadd_products(tile_paths: list[str]):
        called["tile_paths"] = tile_paths
        return expected

    monkeypatch.setattr(
        production,
        "build_epoch_coadd_products",
        fake_build_epoch_coadd_products,
    )

    result = bp._build_epoch_coadd_products(["tile-a.fits", "tile-b.fits"])

    assert called["tile_paths"] == ["tile-a.fits", "tile-b.fits"]
    assert result is expected


def test_archive_epoch_products_overwrites_mosaic_and_weight_as_a_pair(tmp_path):
    import batch_pipeline as bp
    from dsa110_continuum.mosaic.production import write_weight_map

    source_mosaic = _write_tile(tmp_path / "stage_mosaic.fits", ra_deg=10.0, value=2.0)
    source_weight = write_weight_map(
        np.full((16, 16), 4.0),
        WCS(fits.getheader(source_mosaic)).celestial,
        source_mosaic,
    )
    destination_mosaic = _write_tile(
        tmp_path / "product_mosaic.fits",
        ra_deg=10.0,
        value=1.0,
    )
    destination_weight = tmp_path / "product_mosaic.weights.fits"
    fits.PrimaryHDU(data=np.ones((16, 16))).writeto(destination_weight)

    bp._archive_epoch_products(
        source_mosaic,
        source_weight,
        destination_mosaic,
        destination_weight,
    )

    assert np.all(fits.getdata(destination_mosaic) == 2.0)
    assert np.all(fits.getdata(destination_weight) == 4.0)
