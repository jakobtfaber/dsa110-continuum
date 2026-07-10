"""Production hourly-epoch image-domain coadd helpers.

The package namespace is the canonical owner of the production coadd (#77).
Tiles are combined with a per-pixel inverse-variance (Sault) weighted mean:

- Input tiles are PB-corrected (``*-image-pb.fits``), so each pixel's variance
  is sigma_flat^2 / PB^2 and the optimal weight is w_tile * PB^2 with
  w_tile = 1 / sigma_flat^2. A tile's contribution therefore rolls off
  smoothly with its beam instead of entering at full weight up to a blanking
  line.
- The primary-beam map per tile is recovered exactly as the ratio
  ``-image.fits`` / ``-image-pb.fits`` (the beam WSClean actually applied;
  verified pixel-identical to ``-beam-0.fits`` on H17 production tiles,
  2026-07-04). Fallback: the band-average of all ``*-beam-N.fits`` maps,
  so the weight stays correct even for products where per-subband beam
  files do differ.
- ``PB_CUTOFF`` is a numerical weight floor (weight = 0 below it), not a
  science decision: with PB^2 weighting the excluded pixels would have
  carried <~4% weight anyway.
- The accumulated weight plane (sum of per-tile inverse-variance weights,
  units 1/(Jy/beam)^2) is available as a companion product: the effective
  local noise is 1/sqrt(weight), which lets photometry/source-finding
  threshold out single-coverage boundary pixels that per-pixel weighting
  cannot repair (num/den is identically the tile value where only one tile
  covers).
- RA wrap uses a circular mean so 0/360-degree tile sets stay compact.
- Day-batch callers can still split disjoint tile sets with a 10-degree RA gap.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

CELL_ARCSEC = 6.0
PB_CUTOFF = 0.2

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductionCoaddResult:
    """Science mosaic planes on their shared output grid."""

    mosaic: np.ndarray
    weight: np.ndarray
    wcs: WCS


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


def _tile_base(fits_path: str) -> str:
    """Strip the WSClean product suffix from a tile image path."""
    for suffix in ("-image-pb.fits", "-image.fits"):
        if fits_path.endswith(suffix):
            return fits_path[: -len(suffix)]
    return fits_path.removesuffix(".fits")


def _pb_map_for_tile(fits_path: str, data_pb: np.ndarray) -> tuple[np.ndarray | None, str]:
    """Recover the normalized primary-beam response on the tile's pixel grid.

    Preferred source is the ratio ``-image.fits`` / ``-image-pb.fits``: it is
    exactly the beam WSClean divided out, with no model assumptions. The ratio
    is only undefined where the PB-corrected pixel is 0 or non-finite (isolated
    noise zero-crossings and blanked borders) — those pixels get NaN and drop
    out of the coadd. Fallback is the band-average of every ``*-beam-N.fits``
    present; a single subband beam is never used alone.
    """
    base = _tile_base(fits_path)

    flat_path = base + "-image.fits"
    if fits_path.endswith("-image-pb.fits") and os.path.exists(flat_path):
        with fits.open(flat_path) as hdul:
            flat = hdul[0].data.squeeze().astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            pb = np.where(
                np.isfinite(flat) & np.isfinite(data_pb) & (data_pb != 0),
                flat / data_pb,
                np.nan,
            )
        finite_frac = float(np.isfinite(pb).mean())
        if finite_frac > 0.3:
            peak = np.nanmax(pb)
            if peak > 0:
                pb = pb / peak
            return pb, "image/image-pb ratio"
        log.warning(
            "  PB ratio for %s only %.0f%% finite; falling back to beam maps",
            Path(fits_path).name,
            finite_frac * 100,
        )

    beam_paths = sorted(Path(base).parent.glob(Path(base).name + "-beam-*.fits"))
    if beam_paths:
        pb_sum: np.ndarray | None = None
        n_used = 0
        for beam_path in beam_paths:
            with fits.open(beam_path) as hdul:
                beam = hdul[0].data.squeeze().astype(np.float64)
            if pb_sum is None:
                pb_sum = np.zeros_like(beam)
            pb_sum += beam
            n_used += 1
        assert pb_sum is not None
        pb = pb_sum / n_used
        peak = np.nanmax(pb)
        if peak > 0:
            pb = pb / peak
        return pb, f"band-average of {n_used} beam map(s)"

    return None, "none"


def coadd_tiles_with_weights(
    fits_paths: list[str],
    out_wcs: WCS,
    ny: int,
    nx: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the Sault-weighted mosaic and accumulated inverse-variance plane.

    Inputs are PB-corrected tiles, so each pixel is an unbiased sky estimate
    with variance sigma_flat^2 / PB^2; the inverse-variance weight is
    (1 / sigma_flat^2) * PB^2 (Sault weighting). The weighted-numerator and
    weight planes are reprojected identically and accumulated, and the mosaic
    is their ratio.
    """
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

        pb, pb_source = _pb_map_for_tile(path, data)
        if pb is None:
            log.warning(
                "  No PB source for %s (no -image sibling, no beam maps); "
                "using uniform weight over the tile footprint",
                Path(path).name,
            )
            pb = np.ones_like(data)
        else:
            log.info("  PB source: %s", pb_source)

        # Per-tile scalar weight from flat-noise rms: PB-corrected central box
        # has PB ~ 1 there, so measure on data * pb (the flat-noise plane).
        flat_plane = data * pb
        cy, cx = data.shape[0] // 2, data.shape[1] // 2
        margin = 200
        inner = flat_plane[
            max(0, cy - margin) : cy + margin,
            max(0, cx - margin) : cx + margin,
        ]
        inner_finite = inner[np.isfinite(inner)]
        noise = float(np.std(inner_finite)) if inner_finite.size else 0.0
        if noise <= 0 or not np.isfinite(noise):
            noise = 1.0
        w_tile = 1.0 / (noise**2)

        # Per-pixel weight plane; PB_CUTOFF is a numerical floor, not blanking
        # science: below it PB^2 weight is negligible and the ratio-derived PB
        # gets noisy.
        wmap = w_tile * pb**2
        invalid = ~np.isfinite(data) | ~np.isfinite(pb) | (pb < PB_CUTOFF)
        wmap[invalid] = 0.0
        num_plane = np.where(invalid, 0.0, wmap * data)
        support_plane = (~invalid).astype(np.float64)
        n_floor = int(((pb < PB_CUTOFF) & np.isfinite(pb)).sum())
        log.info(
            "  Sault weights: sigma_flat=%.4g, floor(PB<%.0f%%) zeroed %d px",
            noise,
            PB_CUTOFF * 100,
            n_floor,
        )

        in_wcs = WCS(hdr).celestial
        if reproject_interp is None:
            num_reproj, footprint = _nearest_reproject(num_plane, in_wcs, out_wcs, (ny, nx))
            w_reproj, _ = _nearest_reproject(wmap, in_wcs, out_wcs, (ny, nx))
            support_reproj, _ = _nearest_reproject(
                support_plane,
                in_wcs,
                out_wcs,
                (ny, nx),
            )
        else:
            try:
                num_reproj, footprint = reproject_interp(
                    (num_plane, in_wcs),
                    out_header,
                    shape_out=(ny, nx),
                )
                w_reproj, _ = reproject_interp(
                    (wmap, in_wcs),
                    out_header,
                    shape_out=(ny, nx),
                )
                # Interpolating the zeroed weight plane alone can leak weight
                # back into PB-rejected pixels from valid neighbours. Reproject
                # the boolean support with nearest-neighbour sampling and use it
                # as the authoritative cutoff mask on the output grid.
                support_reproj, _ = reproject_interp(
                    (support_plane, in_wcs),
                    out_header,
                    shape_out=(ny, nx),
                    order="nearest-neighbor",
                )
            except Exception as exc:
                log.warning("Reproject failed for %s: %s; skipping", Path(path).name, exc)
                continue

        valid = (
            footprint.astype(bool)
            & np.isfinite(num_reproj)
            & np.isfinite(w_reproj)
            & np.isfinite(support_reproj)
            & (support_reproj >= 0.5)
        )
        sum_image[valid] += num_reproj[valid]
        sum_weight[valid] += w_reproj[valid]

    with np.errstate(invalid="ignore", divide="ignore"):
        mosaic = np.where(sum_weight > 0, sum_image / sum_weight, np.nan)
    return mosaic, sum_weight


def coadd_tiles(fits_paths: list[str], out_wcs: WCS, ny: int, nx: int) -> np.ndarray:
    """Compatibility wrapper returning only the Sault-weighted mosaic."""
    mosaic, _weight = coadd_tiles_with_weights(fits_paths, out_wcs, ny, nx)
    return mosaic


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
    result = build_epoch_coadd_products(fits_paths)
    return result.mosaic, result.wcs


def build_epoch_coadd_products(fits_paths: list[str]) -> ProductionCoaddResult:
    """Build the mosaic, accumulated weight plane, and WCS for one epoch."""
    out_wcs, ny, nx = build_common_wcs(fits_paths)
    mosaic, weight = coadd_tiles_with_weights(fits_paths, out_wcs, ny, nx)
    return ProductionCoaddResult(mosaic=mosaic, weight=weight, wcs=out_wcs)


def weight_path_for_mosaic(mosaic_path: str | Path) -> Path:
    """Return the stable companion path ``<mosaic>.weights.fits``."""
    return Path(mosaic_path).with_suffix(".weights.fits")


def weight_map_is_valid(
    weight_path: str | Path,
    mosaic_path: str | Path,
) -> bool:
    """Return whether a companion weight FITS is readable and grid-aligned."""
    try:
        with fits.open(weight_path, memmap=True) as weight_hdul:
            weight_hdu = weight_hdul[0]
            if weight_hdu.data is None or weight_hdu.header.get("BUNIT") != "1/Jy^2":
                return False
            weight_shape = weight_hdu.data.squeeze().shape
            weight_wcs = WCS(weight_hdu.header).celestial

        with fits.open(mosaic_path, memmap=True) as mosaic_hdul:
            mosaic_hdu = mosaic_hdul[0]
            if mosaic_hdu.data is None:
                return False
            mosaic_shape = mosaic_hdu.data.squeeze().shape
            mosaic_wcs = WCS(mosaic_hdu.header).celestial

        return weight_shape == mosaic_shape and all(
            np.allclose(weight_values, mosaic_values, rtol=0.0, atol=1e-10)
            for weight_values, mosaic_values in (
                (weight_wcs.wcs.crpix, mosaic_wcs.wcs.crpix),
                (weight_wcs.wcs.crval, mosaic_wcs.wcs.crval),
                (weight_wcs.wcs.cdelt, mosaic_wcs.wcs.cdelt),
            )
        )
    except (OSError, ValueError, IndexError, TypeError):
        return False


def write_weight_map(
    weight: np.ndarray,
    out_wcs: WCS,
    mosaic_path: str | Path,
) -> Path:
    """Write an accumulated inverse-variance plane beside its mosaic."""
    weight_path = weight_path_for_mosaic(mosaic_path)
    weight_path.parent.mkdir(parents=True, exist_ok=True)
    header = out_wcs.to_header()
    header["BUNIT"] = ("1/Jy^2", "Inverse variance of PB-corrected flux")
    header["EXTNAME"] = ("WEIGHT", "Accumulated inverse-variance plane")
    header["MOSAIC"] = (Path(mosaic_path).name, "Associated mosaic FITS")
    header["HISTORY"] = "Effective local noise is 1/sqrt(weight) Jy/beam"
    fits.PrimaryHDU(data=weight.astype(np.float32), header=header).writeto(
        weight_path,
        overwrite=True,
    )
    log.info("Epoch weight map written: %s", weight_path)
    return weight_path
