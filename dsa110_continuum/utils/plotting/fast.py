# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
#!/usr/bin/env python
"""Fast FITS image plotter optimized for radio astronomy.

Uses manual scaling instead of percentile calculation for speed.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def plot_fits_fast(fits_path, output_path=None, vmin_factor=3, vmax_factor=0.95):
    """Fast plot with manual scaling.

    Args:
        fits_path: Path to FITS file
        output_path: Output PNG path
        vmin_factor: Min displayed value = -vmin_factor * RMS
        vmax_factor: Max displayed value = vmax_factor * peak

    """
    fits_path = Path(fits_path)
    if output_path is None:
        output_path = fits_path.with_suffix(".png")

    # Read FITS
    with fits.open(fits_path) as hdul:
        data = hdul[0].data
        header = hdul[0].header

        data = np.asarray(data).squeeze()
        if data.ndim > 2:
            data = data[0] if data.ndim == 3 else data[0, 0]

        try:
            wcs = WCS(header).celestial
        except Exception:
            wcs = None

        bmaj = header.get("BMAJ", None)
        bmin = header.get("BMIN", None)
        bpa = header.get("BPA", None)
        bunit = header.get("BUNIT", "Jy/beam")

    # Calculate statistics
    peak = np.nanmax(data)
    rms = np.sqrt(np.nanmean(data**2))

    # Manual scaling for speed
    vmin = -vmin_factor * rms
    vmax = vmax_factor * peak

    # Create figure
    fig = plt.figure(figsize=(12, 10))

    if wcs is not None:
        ax = fig.add_subplot(111, projection=wcs)
        ax.set_xlabel("Right Ascension (J2000)", fontsize=12)
        ax.set_ylabel("Declination (J2000)", fontsize=12)
    else:
        ax = fig.add_subplot(111)
        ax.set_xlabel("X pixel", fontsize=12)
        ax.set_ylabel("Y pixel", fontsize=12)

    # Plot with sqrt scaling for better visualization
    im = ax.imshow(
        data, origin="lower", cmap="viridis", vmin=vmin, vmax=vmax, aspect="auto"
    )

    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(bunit, fontsize=12)

    # Title
    title = f"{fits_path.name}\n"
    title += f"Peak: {peak:.3e} {bunit}, RMS: {rms:.3e} {bunit}, "
    title += f"SNR: {peak / rms:.1f}"
    if bmaj and bmin:
        title += f'\nBeam: {bmaj * 3600:.2f}" × {bmin * 3600:.2f}"'
        if bpa:
            title += f" @ {bpa:.1f}°"

    ax.set_title(title, fontsize=11, pad=10)

    # Draw beam
    if bmaj and bmin and wcs is not None:
        from matplotlib.patches import Ellipse

        beam = Ellipse(
            xy=(0.1, 0.1),
            width=bmin * 3600,
            height=bmaj * 3600,
            angle=bpa if bpa else 0,
            transform=ax.transAxes,
            facecolor="white",
            edgecolor="black",
            linewidth=1.5,
        )
        ax.add_patch(beam)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {output_path}")
    print(f"  Peak: {peak:.6e}, RMS: {rms:.6e}, SNR: {peak / rms:.1f}")
    plt.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m dsa110_continuum.utils.plotting.fast <fits_file> [output_png]")
        sys.exit(1)

    plot_fits_fast(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
