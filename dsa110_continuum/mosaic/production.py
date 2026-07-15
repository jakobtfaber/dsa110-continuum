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
- Per-tile reprojection uses an overlap-only output WCS cutout (11-sample
  edge bounds), then pastes into the full mosaic; full-grid mode remains
  available via ``use_overlap_cutouts=False`` for equivalence tests.
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


def _default_coadd_workers() -> int:
    """Parallel reproject worker count (env ``DSA110_COADD_WORKERS``, default ≤8)."""
    raw = os.environ.get("DSA110_COADD_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            log.warning("Ignoring invalid DSA110_COADD_WORKERS=%r", raw)
    ncpu = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else (os.cpu_count() or 4)
    )
    return max(1, min(8, ncpu))


def _sample_array_edges(shape: tuple[int, ...], *, n_samples: int = 11) -> np.ndarray:
    """Sample pixel coordinates along each edge of an N-D array (incl. corners).

    Mirrors ``reproject.array_utils.sample_array_edges`` so overlap bounds stay
    conservative under SIN/TAN edge bow without depending on that private helper.
    Returns an array of shape ``(ndim, n_points)`` in array-axis order.
    """
    all_positions: list[np.ndarray] = []
    ndim = len(shape)
    shape_arr = np.asarray(shape, dtype=int)
    for idim in range(ndim):
        for vertex in range(2**ndim):
            positions = -0.5 + shape_arr * ((vertex & (2 ** np.arange(ndim))) > 0).astype(int)
            positions = np.broadcast_to(positions, (n_samples, ndim)).copy()
            positions[:, idim] = np.linspace(-0.5, shape_arr[idim] - 0.5, n_samples)
            all_positions.append(positions)
    return np.unique(np.vstack(all_positions), axis=0).T


def _overlap_output_bounds(
    in_wcs: WCS,
    out_wcs: WCS,
    shape_in: tuple[int, int],
    shape_out: tuple[int, int],
    *,
    n_samples: int = 11,
) -> tuple[int, int, int, int] | None:
    """Return ``(ymin, ymax, xmin, xmax)`` covering the input on the output grid.

    Uses an 11-sample edge map (same geometry as
    ``reproject.mosaicking.reproject_and_coadd``). Returns ``None`` when the
    predicted overlap is empty. If any edge sample lacks valid coordinates,
    falls back to the full output grid (safe for all-sky / blank corners).
    """
    ny_out, nx_out = shape_out
    # sample_array_edges returns (y, x) for shape (ny, nx); flip to (x, y)
    # for WCS pixel transforms, then flip the result back to (y, x).
    edges_yx = _sample_array_edges(shape_in, n_samples=n_samples)
    edges_xy = edges_yx[::-1]
    try:
        from astropy.wcs.utils import pixel_to_pixel

        edges_out_xy = pixel_to_pixel(in_wcs, out_wcs, *edges_xy)
        edges_out_yx = edges_out_xy[::-1]
    except Exception:  # noqa: BLE001 — fall back to world round-trip
        world = in_wcs.pixel_to_world(*edges_xy)
        x_out, y_out = out_wcs.world_to_pixel(world)
        edges_out_yx = (y_out, x_out)

    if np.any(~np.isfinite(edges_out_yx[0]) | ~np.isfinite(edges_out_yx[1])):
        return 0, ny_out, 0, nx_out

    ymin = max(0, int(np.floor(float(np.min(edges_out_yx[0])) + 0.5)))
    ymax = min(ny_out, int(np.ceil(float(np.max(edges_out_yx[0])) + 0.5)))
    xmin = max(0, int(np.floor(float(np.min(edges_out_yx[1])) + 0.5)))
    xmax = min(nx_out, int(np.ceil(float(np.max(edges_out_yx[1])) + 0.5)))
    if ymax <= ymin or xmax <= xmin:
        return None
    return ymin, ymax, xmin, xmax


def _sault_reproject_one_tile(
    path: str,
    out_header_bytes: bytes,
    ny: int,
    nx: int,
    use_overlap_cutouts: bool = True,
) -> dict:
    """Reproject one tile's Sault numerator/weight planes onto the mosaic grid.

    Module-level so :class:`concurrent.futures.ProcessPoolExecutor` can pickle
    it. Returns a dict with ``ok``, cutout arrays + destination offsets (when
    successful), or ``skipped`` when the tile has no predicted output overlap.
    """
    from astropy.io import fits as _fits

    name = Path(path).name
    out_header = _fits.Header.fromstring(out_header_bytes)
    try:
        from reproject import reproject_interp
    except ModuleNotFoundError:
        reproject_interp = None

    with _fits.open(path) as hdul:
        data = hdul[0].data.squeeze().astype(np.float64)
        hdr = hdul[0].header

    in_wcs = WCS(hdr).celestial
    out_wcs_local = WCS(out_header)

    if use_overlap_cutouts:
        bounds = _overlap_output_bounds(in_wcs, out_wcs_local, data.shape, (ny, nx))
        if bounds is None:
            return {"ok": True, "skipped": True, "name": name}
        ymin, ymax, xmin, xmax = bounds
    else:
        ymin, ymax, xmin, xmax = 0, ny, 0, nx

    cut_ny, cut_nx = ymax - ymin, xmax - xmin
    out_wcs_cut = out_wcs_local[ymin:ymax, xmin:xmax]
    out_header_cut = out_wcs_cut.to_header()
    out_header_cut["NAXIS1"] = cut_nx
    out_header_cut["NAXIS2"] = cut_ny

    pb, pb_source = _pb_map_for_tile(path, data)
    if pb is None:
        pb = np.ones_like(data)
        pb_source = "uniform (no PB source)"

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

    wmap = w_tile * pb**2
    invalid = ~np.isfinite(data) | ~np.isfinite(pb) | (pb < PB_CUTOFF)
    wmap[invalid] = 0.0
    num_plane = np.where(invalid, 0.0, wmap * data)
    support_plane = (~invalid).astype(np.float64)
    n_floor = int(((pb < PB_CUTOFF) & np.isfinite(pb)).sum())

    try:
        if reproject_interp is None:
            num_reproj, footprint = _nearest_reproject(
                num_plane, in_wcs, out_wcs_cut, (cut_ny, cut_nx)
            )
            w_reproj, _ = _nearest_reproject(wmap, in_wcs, out_wcs_cut, (cut_ny, cut_nx))
            support_reproj, _ = _nearest_reproject(
                support_plane,
                in_wcs,
                out_wcs_cut,
                (cut_ny, cut_nx),
            )
        else:
            num_reproj, footprint = reproject_interp(
                (num_plane, in_wcs),
                out_header_cut,
                shape_out=(cut_ny, cut_nx),
            )
            w_reproj, _ = reproject_interp(
                (wmap, in_wcs),
                out_header_cut,
                shape_out=(cut_ny, cut_nx),
            )
            # Interpolating the zeroed weight plane alone can leak weight
            # back into PB-rejected pixels from valid neighbours. Reproject
            # the boolean support with nearest-neighbour sampling and use it
            # as the authoritative cutoff mask on the output grid.
            support_reproj, _ = reproject_interp(
                (support_plane, in_wcs),
                out_header_cut,
                shape_out=(cut_ny, cut_nx),
                order="nearest-neighbor",
            )
    except Exception as exc:  # noqa: BLE001 — worker must not kill the pool
        return {"ok": False, "name": name, "error": str(exc)}

    valid = (
        footprint.astype(bool)
        & np.isfinite(num_reproj)
        & np.isfinite(w_reproj)
        & np.isfinite(support_reproj)
        & (support_reproj >= 0.5)
    )
    return {
        "ok": True,
        "skipped": False,
        "name": name,
        "num": np.asarray(num_reproj, dtype=np.float64),
        "weight": np.asarray(w_reproj, dtype=np.float64),
        "valid": valid,
        "y0": int(ymin),
        "x0": int(xmin),
        "noise": noise,
        "n_floor": n_floor,
        "pb_source": pb_source,
    }


def coadd_tiles_with_weights(
    fits_paths: list[str],
    out_wcs: WCS,
    ny: int,
    nx: int,
    *,
    max_workers: int | None = None,
    use_overlap_cutouts: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the Sault-weighted mosaic and accumulated inverse-variance plane.

    Inputs are PB-corrected tiles, so each pixel is an unbiased sky estimate
    with variance sigma_flat^2 / PB^2; the inverse-variance weight is
    (1 / sigma_flat^2) * PB^2 (Sault weighting). The weighted-numerator and
    weight planes are reprojected identically and accumulated, and the mosaic
    is their ratio.

    By default each tile is reprojected only onto its predicted output-WCS
    overlap cutout (then pasted into the full mosaic arrays). Set
    ``use_overlap_cutouts=False`` to force full-grid reprojection (tests /
    equivalence checks).

    Parameters
    ----------
    max_workers
        Parallel tile reprojections via :class:`~concurrent.futures.ProcessPoolExecutor`.
        ``None`` uses :func:`_default_coadd_workers` (env ``DSA110_COADD_WORKERS``,
        capped at 8). ``1`` forces the serial path (tests / debugging).
    use_overlap_cutouts
        If ``True`` (default), reproject each tile onto its overlap bounding
        box only. If ``False``, reproject onto the full ``(ny, nx)`` grid.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    sum_image = np.zeros((ny, nx), dtype=np.float64)
    sum_weight = np.zeros((ny, nx), dtype=np.float64)
    out_header = out_wcs.to_header()
    out_header["NAXIS1"] = nx
    out_header["NAXIS2"] = ny
    # Binary round-trip preserves WCS float precision across process boundaries
    # (Header.items() stringifies values and can perturb CRVAL/CDELT).
    out_header_bytes = out_header.tostring()

    workers = _default_coadd_workers() if max_workers is None else max(1, int(max_workers))
    workers = min(workers, max(1, len(fits_paths)))

    def _accumulate(result: dict, *, announce: bool = True) -> None:
        if not result.get("ok"):
            log.warning(
                "Reproject failed for %s: %s; skipping",
                result.get("name", "?"),
                result.get("error", "unknown"),
            )
            return
        if result.get("skipped"):
            log.info("Skipping %s (no overlap with mosaic grid)", result["name"])
            return
        if announce:
            log.info("Finished reprojecting %s", result["name"])
        if result["pb_source"].startswith("uniform"):
            log.warning(
                "  No PB source for %s (no -image sibling, no beam maps); "
                "using uniform weight over the tile footprint",
                result["name"],
            )
        else:
            log.info("  PB source: %s", result["pb_source"])
        log.info(
            "  Sault weights: sigma_flat=%.4g, floor(PB<%.0f%%) zeroed %d px",
            result["noise"],
            PB_CUTOFF * 100,
            result["n_floor"],
        )
        valid = result["valid"]
        y0 = int(result["y0"])
        x0 = int(result["x0"])
        cut_ny, cut_nx = valid.shape
        dest_num = sum_image[y0 : y0 + cut_ny, x0 : x0 + cut_nx]
        dest_weight = sum_weight[y0 : y0 + cut_ny, x0 : x0 + cut_nx]
        dest_num[valid] += result["num"][valid]
        dest_weight[valid] += result["weight"][valid]

    if workers <= 1 or len(fits_paths) <= 1:
        for path in fits_paths:
            log.info("Reprojecting %s ...", Path(path).name)
            _accumulate(
                _sault_reproject_one_tile(
                    path,
                    out_header_bytes,
                    ny,
                    nx,
                    use_overlap_cutouts,
                ),
                announce=False,
            )
    else:
        log.info(
            "Parallel Sault coadd: %d tiles, max_workers=%d, cutouts=%s "
            "(set DSA110_COADD_WORKERS to override)",
            len(fits_paths),
            workers,
            use_overlap_cutouts,
        )
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _sault_reproject_one_tile,
                    path,
                    out_header_bytes,
                    ny,
                    nx,
                    use_overlap_cutouts,
                )
                for path in fits_paths
            ]
            for fut in as_completed(futures):
                _accumulate(fut.result())

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
            weight_data = weight_hdu.data.squeeze()
            weight_shape = weight_data.shape
            weight_wcs = WCS(weight_hdu.header).celestial

        with fits.open(mosaic_path, memmap=True) as mosaic_hdul:
            mosaic_hdu = mosaic_hdul[0]
            if mosaic_hdu.data is None:
                return False
            mosaic_data = mosaic_hdu.data.squeeze()
            mosaic_shape = mosaic_data.shape
            mosaic_wcs = WCS(mosaic_hdu.header).celestial

        if weight_shape != mosaic_shape or weight_data.ndim != 2:
            return False
        has_science_pixel = False
        for weight_row, mosaic_row in zip(weight_data, mosaic_data, strict=True):
            if not np.all(np.isfinite(weight_row)) or np.any(weight_row < 0):
                return False
            science = np.isfinite(mosaic_row)
            if np.any(weight_row[~science] != 0):
                return False
            if np.any(science):
                has_science_pixel = True
                if np.any(weight_row[science] <= 0):
                    return False

        return has_science_pixel and bool(weight_wcs.wcs.compare(mosaic_wcs.wcs))
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
