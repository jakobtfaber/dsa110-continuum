#!/usr/bin/env python3
# ruff: noqa: D103
"""Validate an hourly-epoch mosaic against separate radio catalogs."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
from pathlib import Path

import astropy.units as u
import matplotlib
import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dsa110_continuum.catalog.query import cone_search

LOG = logging.getLogger("validate_mosaic_astrometry")
CATALOG_PATHS = {
    "nvss": Path("/data/dsa110-contimg/state/catalogs/nvss_full.sqlite3"),
    "first": Path("/data/dsa110-contimg/state/catalogs/first_full.sqlite3"),
    "rax": Path("/data/dsa110-contimg/state/catalogs/rax_full.sqlite3"),
}
SEEDED_COLUMNS = [
    "catalog_ra_deg",
    "catalog_dec_deg",
    "catalog_flux_mjy",
    "dsa_ra_deg",
    "dsa_dec_deg",
    "peak_flux_jy_beam",
    "local_rms_jy_beam",
    "snr",
    "dra_cosdec_arcsec",
    "ddec_arcsec",
    "separation_arcsec",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mosaic", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--min-flux-mjy", type=float, default=50.0)
    parser.add_argument("--surveys", default="nvss,first,rax")
    parser.add_argument("--blind-timeout-seconds", type=float, default=600.0)
    parser.add_argument(
        "--no-blind",
        action="store_true",
        help="Skip Aegean/BANE blind path (seeded gate still runs).",
    )
    return parser.parse_args()


def load_rax_parquet(
    ra_center: float,
    dec_center: float,
    radius_deg: float,
    min_flux_mjy: float,
) -> pd.DataFrame:
    """Load RACS/RAX from parquet when sqlite flux_mjy is unpopulated."""
    parquet = Path("/data/dsa110-contimg/state/catalogs/raw_rows/rax_full.parquet")
    if not parquet.is_file():
        return pd.DataFrame(columns=["ra_deg", "dec_deg", "flux_mjy"])
    df = pd.read_parquet(parquet, columns=["RAJ2000", "DEJ2000", "Ftot"])
    # Coarse Dec cut before angular filter (Ftot is mJy in RACS-DR1 export).
    keep = (
        (df["DEJ2000"] >= dec_center - radius_deg)
        & (df["DEJ2000"] <= dec_center + radius_deg)
        & (df["Ftot"] >= min_flux_mjy)
    )
    df = df.loc[keep, ["RAJ2000", "DEJ2000", "Ftot"]].rename(
        columns={"RAJ2000": "ra_deg", "DEJ2000": "dec_deg", "Ftot": "flux_mjy"}
    )
    if df.empty:
        return df.reset_index(drop=True)
    sky = SkyCoord(df.ra_deg.to_numpy() * u.deg, df.dec_deg.to_numpy() * u.deg)
    center = SkyCoord(ra_center * u.deg, dec_center * u.deg)
    return df.loc[sky.separation(center).deg <= radius_deg].reset_index(drop=True)


def query_catalog(
    survey: str,
    ra_center: float,
    dec_center: float,
    radius_deg: float,
    min_flux_mjy: float,
) -> pd.DataFrame:
    catalog = cone_search(
        survey,
        ra_center,
        dec_center,
        radius_deg,
        min_flux_mjy=min_flux_mjy,
    )
    if survey == "rax" and (catalog.empty or catalog["flux_mjy"].isna().all()):
        LOG.warning(
            "RAX sqlite fluxes are null; falling back to rax_full.parquet Ftot >= %.1f mJy",
            min_flux_mjy,
        )
        catalog = load_rax_parquet(ra_center, dec_center, radius_deg, min_flux_mjy)
    return catalog


def image_plane(data: np.ndarray) -> np.ndarray:
    squeezed = np.squeeze(data)
    if squeezed.ndim != 2:
        raise ValueError(f"Expected a two-dimensional FITS image, got {data.shape}")
    return np.asarray(squeezed)


def footprint_geometry(data: np.ndarray, wcs: WCS, pad_deg: float) -> dict:
    valid = np.isfinite(data)
    if not np.any(valid):
        raise ValueError("Mosaic contains no finite pixels")
    valid_rows = np.flatnonzero(np.any(valid, axis=1))
    valid_cols = np.flatnonzero(np.any(valid, axis=0))
    x0, x1 = int(valid_cols[0]), int(valid_cols[-1])
    y0, y1 = int(valid_rows[0]), int(valid_rows[-1])
    corners = SkyCoord.from_pixel(
        np.array([x0, x0, x1, x1]), np.array([y0, y1, y0, y1]), wcs, origin=0
    )
    center = SkyCoord.from_pixel((x0 + x1) / 2, (y0 + y1) / 2, wcs, origin=0)
    radius = float(np.max(center.separation(corners).deg) + pad_deg)
    return {"bbox_pixels": [x0, x1, y0, y1], "center": center, "radius_deg": radius}


def in_padded_bbox(catalog: pd.DataFrame, wcs: WCS, bbox: list[int], pad_px: float) -> pd.DataFrame:
    if catalog.empty:
        return catalog
    sky = SkyCoord(catalog.ra_deg.to_numpy() * u.deg, catalog.dec_deg.to_numpy() * u.deg)
    x, y = sky.to_pixel(wcs, origin=0)
    x0, x1, y0, y1 = bbox
    keep = (x >= x0 - pad_px) & (x <= x1 + pad_px) & (y >= y0 - pad_px) & (y <= y1 + pad_px)
    return catalog.loc[keep].reset_index(drop=True)


def parabolic_shift(left: float, center: float, right: float) -> float:
    denom = left - 2.0 * center + right
    if not np.isfinite(denom) or denom == 0:
        return 0.0
    shift = 0.5 * (left - right) / denom
    return float(np.clip(shift, -0.5, 0.5))


def seeded_measurements(
    catalog: pd.DataFrame, data: np.ndarray, wcs: WCS, half_width_px: int
) -> pd.DataFrame:
    rows: list[dict] = []
    height, width = data.shape
    for source in catalog.itertuples(index=False):
        catalog_coord = SkyCoord(source.ra_deg * u.deg, source.dec_deg * u.deg)
        x_float, y_float = catalog_coord.to_pixel(wcs, origin=0)
        xc, yc = int(round(float(x_float))), int(round(float(y_float)))
        x0, x1 = max(0, xc - half_width_px), min(width, xc + half_width_px + 1)
        y0, y1 = max(0, yc - half_width_px), min(height, yc + half_width_px + 1)
        cutout = data[y0:y1, x0:x1]
        if cutout.shape != (2 * half_width_px + 1, 2 * half_width_px + 1):
            continue
        edge = np.concatenate((cutout[0], cutout[-1], cutout[1:-1, 0], cutout[1:-1, -1]))
        edge = edge[np.isfinite(edge)]
        if edge.size < 8:
            continue
        background = float(np.median(edge))
        rms = float(1.4826 * np.median(np.abs(edge - background)))
        if not np.isfinite(rms) or rms <= 0 or not np.any(np.isfinite(cutout)):
            continue
        iy, ix = np.unravel_index(np.nanargmax(cutout), cutout.shape)
        if ix in (0, cutout.shape[1] - 1) or iy in (0, cutout.shape[0] - 1):
            continue
        peak = float(cutout[iy, ix])
        snr = (peak - background) / rms
        if snr < 5.0:
            continue
        dx = parabolic_shift(cutout[iy, ix - 1], peak, cutout[iy, ix + 1])
        dy = parabolic_shift(cutout[iy - 1, ix], peak, cutout[iy + 1, ix])
        dsa_coord = SkyCoord.from_pixel(x0 + ix + dx, y0 + iy + dy, wcs, origin=0)
        dra, ddec = catalog_coord.spherical_offsets_to(dsa_coord)
        dra_arcsec, ddec_arcsec = float(dra.arcsec), float(ddec.arcsec)
        flux = getattr(source, "flux_mjy", np.nan)
        rows.append(
            {
                "catalog_ra_deg": float(source.ra_deg),
                "catalog_dec_deg": float(source.dec_deg),
                "catalog_flux_mjy": float(flux) if pd.notna(flux) else float("nan"),
                "dsa_ra_deg": float(dsa_coord.ra.deg),
                "dsa_dec_deg": float(dsa_coord.dec.deg),
                "peak_flux_jy_beam": peak,
                "local_rms_jy_beam": rms,
                "snr": float(snr),
                "dra_cosdec_arcsec": dra_arcsec,
                "ddec_arcsec": ddec_arcsec,
                "separation_arcsec": float(np.hypot(dra_arcsec, ddec_arcsec)),
            }
        )
    return pd.DataFrame(rows, columns=SEEDED_COLUMNS)


def score(
    matches: pd.DataFrame,
    threshold_arcsec: float,
    association_radius_arcsec: float,
) -> dict:
    """Score only associations within association_radius (reject false peaks)."""
    result = {
        "n_seeded_peaks": int(len(matches)),
        "association_radius_arcsec": association_radius_arcsec,
        "threshold_arcsec": threshold_arcsec,
    }
    if matches.empty:
        return result | {
            "n_seeded_matches": 0,
            "verdict": "SKIP",
            "reason": "fewer than 5 seeded matches",
        }
    associated = matches.loc[matches.separation_arcsec <= association_radius_arcsec]
    result["n_seeded_matches"] = int(len(associated))
    result["n_rejected_false_associations"] = int(len(matches) - len(associated))
    if len(associated) < 5:
        return result | {"verdict": "SKIP", "reason": "fewer than 5 seeded matches"}
    dra = associated.dra_cosdec_arcsec.to_numpy()
    ddec = associated.ddec_arcsec.to_numpy()
    sep = associated.separation_arcsec.to_numpy()
    rms = float(np.sqrt(np.mean(sep**2)))
    mean_dra, mean_ddec = float(np.mean(dra)), float(np.mean(ddec))
    passed = rms <= threshold_arcsec
    warning = bool(abs(mean_dra) > 2.0 or abs(mean_ddec) > 2.0)
    return result | {
        "mean_dra_cosdec_arcsec": mean_dra,
        "mean_ddec_arcsec": mean_ddec,
        "mean_offset_magnitude_arcsec": float(np.hypot(mean_dra, mean_ddec)),
        "median_separation_arcsec": float(np.median(sep)),
        "rms_separation_arcsec": rms,
        "pass": passed,
        "warning": warning,
        "verdict": "PASS" if passed else "FAIL",
        "warning_reason": "mean RA or Dec offset exceeds 2 arcsec" if warning else None,
    }


def _aegean_worker(mosaic: str, bkg: str, rms: str, queue: mp.Queue) -> None:
    try:
        from dsa110_continuum.source_finding import run_aegean

        queue.put(("ok", [vars(item) for item in run_aegean(mosaic, bkg, rms, sigma=7.0)]))
    except BaseException as exc:
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def blind_catalog(
    mosaic: Path, out_dir: Path, timeout_seconds: float
) -> tuple[pd.DataFrame | None, str]:
    try:
        import AegeanTools  # noqa: F401
        from dsa110_continuum.source_finding import run_bane
    except ImportError as exc:
        return None, f"Aegean/BANE unavailable: {exc}"
    link = out_dir / mosaic.name
    if not link.exists():
        link.symlink_to(mosaic)
    try:
        bkg, rms = run_bane(link, cores=1)
    except Exception as exc:
        return None, f"Aegean/BANE failed: {exc}"
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_aegean_worker, args=(str(link), bkg, rms, queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join()
        queue.close()
        queue.join_thread()
        return None, f"Aegean timed out after {timeout_seconds:g} seconds; blind path skipped"
    if queue.empty():
        queue.close()
        queue.join_thread()
        return (
            None,
            f"Aegean exited with code {process.exitcode} without a result; blind path skipped",
        )
    status, payload = queue.get()
    queue.close()
    queue.join_thread()
    if status != "ok":
        return None, f"Aegean failed: {payload}; blind path skipped"
    detections = pd.DataFrame(payload)
    return detections, f"Aegean found {len(detections)} sources"


def blind_matches(
    detections: pd.DataFrame, catalog: pd.DataFrame, radius_arcsec: float
) -> pd.DataFrame:
    columns = [
        "catalog_ra_deg",
        "catalog_dec_deg",
        "catalog_flux_mjy",
        "dsa_ra_deg",
        "dsa_dec_deg",
        "dra_cosdec_arcsec",
        "ddec_arcsec",
        "separation_arcsec",
    ]
    if detections.empty or catalog.empty:
        return pd.DataFrame(columns=columns)
    dsa = SkyCoord(detections.ra_deg.to_numpy() * u.deg, detections.dec_deg.to_numpy() * u.deg)
    ref = SkyCoord(catalog.ra_deg.to_numpy() * u.deg, catalog.dec_deg.to_numpy() * u.deg)
    idx, sep, _ = ref.match_to_catalog_sky(dsa)
    rows = []
    for catalog_idx, (dsa_idx, distance) in enumerate(zip(idx, sep)):
        if distance.arcsec > radius_arcsec:
            continue
        dra, ddec = ref[catalog_idx].spherical_offsets_to(dsa[dsa_idx])
        rows.append(
            {
                "catalog_ra_deg": float(ref[catalog_idx].ra.deg),
                "catalog_dec_deg": float(ref[catalog_idx].dec.deg),
                "catalog_flux_mjy": float(catalog.iloc[catalog_idx].flux_mjy),
                "dsa_ra_deg": float(dsa[dsa_idx].ra.deg),
                "dsa_dec_deg": float(dsa[dsa_idx].dec.deg),
                "dra_cosdec_arcsec": float(dra.arcsec),
                "ddec_arcsec": float(ddec.arcsec),
                "separation_arcsec": float(distance.arcsec),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def write_plots(results: dict[str, pd.DataFrame], threshold: float, out_dir: Path) -> None:
    surveys = list(results)
    colors = {"nvss": "tab:blue", "first": "tab:orange", "rax": "tab:green"}
    fig, axes = plt.subplots(1, len(surveys), figsize=(5 * len(surveys), 4), squeeze=False)
    for ax, survey in zip(axes[0], surveys):
        df = results[survey]
        ax.scatter(df.dra_cosdec_arcsec, df.ddec_arcsec, s=18, alpha=0.7, color=colors.get(survey))
        ax.axhline(0, color="0.5", lw=0.8)
        ax.axvline(0, color="0.5", lw=0.8)
        ax.set(
            title=survey.upper(),
            xlabel=r"$\Delta$RA cos(Dec) [arcsec]",
            ylabel=r"$\Delta$Dec [arcsec]",
        )
        ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    fig.savefig(out_dir / "offset_scatter.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.8))
    # Quiver U/V must be in the same units as x/y (degrees). Passing arcsec
    # with scale_units='xy' wildly exaggerates arrows and can fake an
    # "edge looks worse" impression.
    ref_arcsec = 10.0
    ref_plot_deg = 0.12
    scale = (ref_arcsec / 3600.0) / ref_plot_deg
    plotted_any = False
    for survey, df in results.items():
        if df.empty:
            continue
        # Prefer true associations when the column exists; else all rows.
        use = df
        if "separation_arcsec" in df.columns and len(df):
            # beam/2 ≈ threshold*2.5 when threshold = beam/5; use 22" default-ish
            # from typical DSA mosaic — caller should pass consistent cut.
            assoc = df.loc[df.separation_arcsec <= max(threshold * 2.5, threshold)]
            if len(assoc):
                use = assoc
        bright = use.assign(_flux=use.catalog_flux_mjy.fillna(0.0)).nlargest(
            min(80, len(use)), "_flux"
        )
        ax.quiver(
            bright.catalog_ra_deg,
            bright.catalog_dec_deg,
            bright.dra_cosdec_arcsec / 3600.0,
            bright.ddec_arcsec / 3600.0,
            angles="xy",
            scale_units="xy",
            scale=scale,
            color=colors.get(survey),
            alpha=0.65,
            width=0.0025,
            label=survey.upper(),
        )
        plotted_any = True
    if plotted_any:
        # Fixed 10" reference arrow
        ra_cols = [d.catalog_ra_deg for d in results.values() if not d.empty]
        dec_cols = [d.catalog_dec_deg for d in results.values() if not d.empty]
        x_ref = float(np.nanmax(pd.concat(ra_cols)))
        y_ref = float(np.nanmin(pd.concat(dec_cols)))
        ax.quiver(
            [x_ref - 0.3],
            [y_ref + 0.1],
            [ref_arcsec / 3600.0],
            [0.0],
            angles="xy",
            scale_units="xy",
            scale=scale,
            color="black",
            width=0.004,
        )
        ax.text(x_ref - 0.3, y_ref + 0.18, f'{ref_arcsec:g}"', fontsize=9)
    ax.invert_xaxis()
    ax.set(
        xlabel="RA [deg]",
        ylabel="Dec [deg]",
        title=f'Astrometric offsets (associated) · {ref_arcsec:g}" reference',
    )
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "quiver_sky.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    for survey, df in results.items():
        if not df.empty:
            ax.hist(
                df.separation_arcsec,
                bins="auto",
                histtype="step",
                lw=2,
                label=survey.upper(),
            )
    ax.axvline(threshold, color="black", ls="--", label=f"gate = {threshold:.2f} arcsec")
    ax.set(xlabel="Separation [arcsec]", ylabel="Count")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "hist_separation.png", dpi=160)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.mosaic.is_file():
        raise FileNotFoundError(f"Mosaic not found: {args.mosaic}")
    surveys = [item.strip().lower() for item in args.surveys.split(",") if item.strip()]
    invalid = set(surveys) - set(CATALOG_PATHS)
    if invalid:
        raise ValueError(f"Unsupported surveys: {sorted(invalid)}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / "run.log"
    handler = logging.FileHandler(log_path, mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    LOG.info("Master catalog is VLASS-only and is not used as an astrometric reference in Phase 1")

    with fits.open(args.mosaic, memmap=True) as hdul:
        data = image_plane(hdul[0].data)
        header = hdul[0].header.copy()
    wcs = WCS(header).celestial
    if not wcs.has_celestial:
        raise ValueError("Mosaic has no readable celestial WCS")
    bmaj_deg, bmin_deg = float(header["BMAJ"]), float(header["BMIN"])
    beam_arcsec = np.sqrt(bmaj_deg * bmin_deg) * 3600.0
    threshold = float(beam_arcsec / 5.0)
    pixel_scale_deg = float(
        np.mean([abs(scale.to_value(u.deg)) for scale in wcs.proj_plane_pixel_scales()])
    )
    half_width_px = max(2, int(round(bmaj_deg / pixel_scale_deg)))
    geometry = footprint_geometry(data, wcs, bmaj_deg)
    pad_px = bmaj_deg / pixel_scale_deg
    LOG.info(
        "Beam %.2f x %.2f arcsec; astrometry threshold %.2f arcsec",
        bmaj_deg * 3600,
        bmin_deg * 3600,
        threshold,
    )

    catalogs: dict[str, pd.DataFrame] = {}
    seeded: dict[str, pd.DataFrame] = {}
    survey_summary: dict[str, dict] = {}
    for survey in surveys:
        path = CATALOG_PATHS[survey]
        if not path.is_file():
            matches = pd.DataFrame(columns=SEEDED_COLUMNS)
            matches.to_csv(args.out_dir / f"seeded_offsets_{survey}.csv", index=False)
            seeded[survey] = matches
            survey_summary[survey] = {
                "verdict": "SKIP",
                "reason": f"catalog missing: {path}",
                "n_seeded_matches": 0,
            }
            continue
        os.environ[f"{survey.upper()}_CATALOG"] = str(path)
        catalog = query_catalog(
            survey,
            geometry["center"].ra.deg,
            geometry["center"].dec.deg,
            geometry["radius_deg"],
            args.min_flux_mjy,
        )
        catalog = in_padded_bbox(catalog, wcs, geometry["bbox_pixels"], pad_px)
        catalogs[survey] = catalog
        LOG.info("%s: %d catalog sources in padded footprint", survey.upper(), len(catalog))
        matches = seeded_measurements(catalog, data, wcs, half_width_px)
        matches.to_csv(args.out_dir / f"seeded_offsets_{survey}.csv", index=False)
        seeded[survey] = matches
        association_radius = beam_arcsec / 2.0
        survey_summary[survey] = score(matches, threshold, association_radius)
        survey_summary[survey]["n_catalog_sources"] = int(len(catalog))
        survey_summary[survey]["catalog_path"] = str(path)
        LOG.info(
            '%s: %d seeded peaks (%d associated <= %.1f"); verdict %s',
            survey.upper(),
            survey_summary[survey].get("n_seeded_peaks", len(matches)),
            survey_summary[survey].get("n_seeded_matches", 0),
            association_radius,
            survey_summary[survey]["verdict"],
        )

    if args.no_blind:
        detections, blind_status = None, "Blind path skipped (--no-blind)"
    else:
        detections, blind_status = blind_catalog(
            args.mosaic, args.out_dir, args.blind_timeout_seconds
        )
    LOG.info("Blind path: %s", blind_status)
    if detections is not None:
        for survey, catalog in catalogs.items():
            matches = blind_matches(detections, catalog, beam_arcsec / 2.0)
            matches.to_csv(args.out_dir / f"blind_matches_{survey}.csv", index=False)
            survey_summary[survey]["n_blind_matches"] = int(len(matches))

    scored = [value for value in survey_summary.values() if value["verdict"] != "SKIP"]
    overall = "PASS" if scored and all(value["verdict"] == "PASS" for value in scored) else "FAIL"
    summary = {
        "mosaic": str(args.mosaic),
        "overall_verdict": overall,
        "gate_definition": (
            "seeded RMS separation <= sqrt(BMAJ*BMIN)/5 using associations within beam/2"
        ),
        "beam": {
            "bmaj_arcsec": bmaj_deg * 3600,
            "bmin_arcsec": bmin_deg * 3600,
            "geometric_mean_arcsec": beam_arcsec,
            "threshold_arcsec": threshold,
        },
        "footprint": {
            "valid_pixel_bbox": geometry["bbox_pixels"],
            "padding_bmaj_deg": bmaj_deg,
            "query_center_ra_deg": geometry["center"].ra.deg,
            "query_center_dec_deg": geometry["center"].dec.deg,
            "query_radius_deg": geometry["radius_deg"],
        },
        "min_flux_mjy": args.min_flux_mjy,
        "surveys": survey_summary,
        "blind_source_finding": blind_status,
        "master_catalog": {
            "used": False,
            "reason": "master_sources.sqlite3 is VLASS-only; rebuild deferred to Phase 2",
        },
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_plots(seeded, threshold, args.out_dir)
    LOG.info("Overall verdict: %s", overall)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
