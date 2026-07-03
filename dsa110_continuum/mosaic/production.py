"""Production hourly-epoch image-domain coadd helpers.

These helpers preserve the current batch production semantics while making the
package namespace the canonical owner for #77:

- WSClean per-tile ``*-beam-0.fits`` maps are the production PB source.
- Pixels below 20% peak beam response are blanked before coadd.
- RA wrap uses a circular mean so 0/360-degree tile sets stay compact.
- Day-batch callers can still split disjoint tile sets with a 10-degree RA gap.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

CELL_ARCSEC = 6.0
PB_CUTOFF = 0.2

log = logging.getLogger(__name__)


def _get_tile_center_ra(fits_path: str) -> float:
    """Return the centre RA (deg) of a tile FITS image."""
    with fits.open(fits_path) as hdul:
        hdr = hdul[0].header
        wcs = WCS(hdr).celestial
        ny, nx = hdul[0].data.squeeze().shape
        center = wcs.pixel_to_world(nx / 2.0, ny / 2.0)
        return float(center.ra.deg)


def group_tiles_by_ra(fits_paths: list[str], gap_deg: float = 10.0) -> list[list[str]]:
    """Split tiles into contiguous RA strips using a gap threshold."""
    if not fits_paths:
        return []

    centers = [(path, _get_tile_center_ra(path)) for path in fits_paths]
    centers.sort(key=lambda item: item[1])

    groups: list[list[str]] = [[centers[0][0]]]
    for idx in range(1, len(centers)):
        delta = centers[idx][1] - centers[idx - 1][1]
        if delta > gap_deg:
            groups.append([])
        groups[-1].append(centers[idx][0])

    if len(groups) > 1:
        wrap_gap = (centers[0][1] + 360.0) - centers[-1][1]
        if wrap_gap < gap_deg:
            groups[0] = groups[-1] + groups[0]
            groups.pop()

    log.info("Grouped %d tiles into %d RA strip(s)", len(fits_paths), len(groups))
    return groups


def build_common_wcs(fits_paths: list[str], margin_deg: float = 0.5) -> tuple[WCS, int, int]:
    """Compute a common RA/Dec WCS that covers all input FITS images."""
    all_ras: list[float] = []
    dec_min, dec_max = 90.0, -90.0

    for path in fits_paths:
        with fits.open(path) as hdul:
            hdr = hdul[0].header
            wcs = WCS(hdr).celestial
            ny, nx = hdul[0].data.squeeze().shape
            corners = wcs.pixel_to_world([0, nx - 1, 0, nx - 1], [0, 0, ny - 1, ny - 1])
            ras = [float(c.ra.deg) for c in corners]
            decs = [float(c.dec.deg) for c in corners]
            all_ras.extend(ras)
            dec_min = min(dec_min, min(decs))
            dec_max = max(dec_max, max(decs))

    ra_rad = np.deg2rad(all_ras)
    mean_ra = (
        float(np.rad2deg(np.arctan2(np.mean(np.sin(ra_rad)), np.mean(np.cos(ra_rad))))) % 360.0
    )
    shifted = np.array([(ra - mean_ra + 180.0) % 360.0 - 180.0 for ra in all_ras])
    ra_min = mean_ra + float(shifted.min()) - margin_deg
    ra_max = mean_ra + float(shifted.max()) + margin_deg
    dec_min -= margin_deg
    dec_max += margin_deg

    ra_center = ((ra_min + ra_max) / 2.0) % 360.0
    dec_center = (dec_min + dec_max) / 2.0

    pixel_scale_deg = CELL_ARCSEC / 3600.0
    nx = int(np.ceil((ra_max - ra_min) / pixel_scale_deg))
    ny = int(np.ceil((dec_max - dec_min) / pixel_scale_deg))
    nx = nx + (nx % 2)
    ny = ny + (ny % 2)

    out_wcs = WCS(naxis=2)
    out_wcs.wcs.crpix = [nx / 2 + 0.5, ny / 2 + 0.5]
    out_wcs.wcs.cdelt = [-pixel_scale_deg, pixel_scale_deg]
    out_wcs.wcs.crval = [ra_center, dec_center]
    out_wcs.wcs.ctype = ["RA---SIN", "DEC--SIN"]

    log.info(
        "Common WCS: RA %.2f-%.2f deg, Dec %.2f-%.2f deg, %dx%d px (center RA=%.2f)",
        ra_min,
        ra_max,
        dec_min,
        dec_max,
        nx,
        ny,
        ra_center,
    )
    return out_wcs, ny, nx


def _beam_path_for_tile(fits_path: str) -> str:
    """Return the WSClean beam-map companion path for a tile image."""
    path = fits_path.replace("-image-pb.fits", "-beam-0.fits")
    if not os.path.exists(path):
        path = fits_path.replace("-image.fits", "-beam-0.fits")
    return path


def coadd_tiles(fits_paths: list[str], out_wcs: WCS, ny: int, nx: int) -> np.ndarray:
    """Reproject each tile onto the common WCS and do noise-weighted coaddition."""
    try:
        from reproject import reproject_interp
    except ModuleNotFoundError:
        reproject_interp = None

    sum_image = np.zeros((ny, nx), dtype=np.float64)
    sum_weight = np.zeros((ny, nx), dtype=np.float64)
    out_header = out_wcs.to_header()
    out_header["NAXIS1"] = nx
    out_header["NAXIS2"] = ny

    for path in fits_paths:
        log.info("Reprojecting %s ...", Path(path).name)
        with fits.open(path) as hdul:
            data = hdul[0].data.squeeze().astype(np.float64)
            hdr = hdul[0].header

        pb_path = _beam_path_for_tile(path)
        if PB_CUTOFF > 0 and os.path.exists(pb_path):
            with fits.open(pb_path) as pb_hdul:
                pb_data = pb_hdul[0].data.squeeze().astype(np.float64)
            pb_peak = np.nanmax(pb_data)
            if pb_peak > 0:
                pb_data /= pb_peak
            low_beam = (pb_data < PB_CUTOFF) | ~np.isfinite(pb_data)
            data[low_beam] = np.nan
            log.info("  PB cutoff (%.0f%%): blanked %d pixels", PB_CUTOFF * 100, low_beam.sum())
        elif PB_CUTOFF > 0:
            log.warning("  No WSClean beam map found for %s; skipping beam cutoff", Path(path).name)

        cy, cx = data.shape[0] // 2, data.shape[1] // 2
        margin = 200
        inner = data[
            max(0, cy - margin) : cy + margin,
            max(0, cx - margin) : cx + margin,
        ]
        noise = np.nanstd(inner[np.isfinite(inner)])
        if noise <= 0 or not np.isfinite(noise):
            noise = 1.0
        weight = 1.0 / (noise**2)

        in_wcs = WCS(hdr).celestial
        if reproject_interp is None:
            reprojected, footprint = _nearest_reproject(data, in_wcs, out_wcs, (ny, nx))
        else:
            try:
                reprojected, footprint = reproject_interp(
                    (data, in_wcs),
                    out_header,
                    shape_out=(ny, nx),
                )
            except Exception as exc:
                log.warning("Reproject failed for %s: %s; skipping", Path(path).name, exc)
                continue

        valid = footprint.astype(bool) & np.isfinite(reprojected)
        sum_image[valid] += weight * reprojected[valid]
        sum_weight[valid] += weight

    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sum_weight > 0, sum_image / sum_weight, np.nan)


def _nearest_reproject(
    data: np.ndarray,
    in_wcs: WCS,
    out_wcs: WCS,
    shape_out: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbor WCS reprojection fallback for test/cloud environments."""
    ny, nx = shape_out
    out_y, out_x = np.indices((ny, nx))
    world = out_wcs.pixel_to_world(out_x.ravel(), out_y.ravel())
    in_x, in_y = in_wcs.world_to_pixel(world)
    in_xi = np.rint(in_x).astype(int)
    in_yi = np.rint(in_y).astype(int)

    valid = (
        np.isfinite(in_x)
        & np.isfinite(in_y)
        & (in_xi >= 0)
        & (in_xi < data.shape[1])
        & (in_yi >= 0)
        & (in_yi < data.shape[0])
    )
    reprojected = np.full((ny * nx,), np.nan, dtype=np.float64)
    footprint = np.zeros((ny * nx,), dtype=np.float64)
    reprojected[valid] = data[in_yi[valid], in_xi[valid]]
    footprint[valid] = 1.0
    return reprojected.reshape((ny, nx)), footprint.reshape((ny, nx))


def build_epoch_coadd(fits_paths: list[str]) -> tuple[np.ndarray, WCS]:
    """Build the production coadd array and WCS for one hourly-epoch tile set."""
    out_wcs, ny, nx = build_common_wcs(fits_paths)
    return coadd_tiles(fits_paths, out_wcs, ny, nx), out_wcs
