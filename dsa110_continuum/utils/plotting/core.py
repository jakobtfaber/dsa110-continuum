# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
#!/usr/bin/env python
"""Core FITS image plotter with nice visualization.

This module provides publication-quality FITS image plotting with WCS support,
beam visualization, and proper scaling for radio astronomy images.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from astropy.visualization import (
    AsinhStretch,
    ImageNormalize,
)
from astropy.wcs import WCS


def plot_fits_image(fits_path, output_path=None, percentile=99.5):
    """Create a nice plot of a FITS image.

    Args:
        fits_path: Path to FITS file
        output_path: Output PNG path (default: same name as FITS with .png)
        percentile: Percentile for contrast stretch (default: 99.5)

    """
    fits_path = Path(fits_path)
    if not fits_path.exists():
        print(f"Error: File not found: {fits_path}")
        sys.exit(1)

    if output_path is None:
        output_path = fits_path.with_suffix(".png")

    # Read FITS
    with fits.open(fits_path) as hdul:
        data = hdul[0].data
        header = hdul[0].header

        # Squeeze dimensions
        data = np.asarray(data).squeeze()

        # Handle 3D/4D arrays (take first channel/stokes)
        if data.ndim > 2:
            data = data[0] if data.ndim == 3 else data[0, 0]

        if data.ndim != 2:
            print(f"Error: Expected 2D data, got {data.ndim}D")
            sys.exit(1)

        # Get WCS
        try:
            wcs = WCS(header).celestial
        except Exception:
            wcs = None
            print("Warning: Could not parse WCS, plotting without coordinates")

        # Get beam info
        bmaj = header.get("BMAJ", None)
        bmin = header.get("BMIN", None)
        bpa = header.get("BPA", None)
        bunit = header.get("BUNIT", "Jy/beam")

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

    # Normalize data for display
    # Use RMS for scaling - set floor at -3*RMS and use asinh stretch for full dynamic range
    rms_val = np.sqrt(np.nanmean(data**2))
    vmin = -3 * rms_val  # Show negative features
    vmax = np.nanmax(data)  # Full peak

    # Use asinh stretch to compress bright source while showing faint structure
    stretch = AsinhStretch(a=0.1)  # a=0.1 gives good compression
    norm = ImageNormalize(data, vmin=vmin, vmax=vmax, stretch=stretch)

    # Plot image
    im = ax.imshow(data, origin="lower", cmap="viridis", norm=norm, aspect="auto")

    # Add colorbar
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(bunit, fontsize=12)

    # Add title with statistics
    mean = np.nanmean(data)
    std = np.nanstd(data)
    peak = np.nanmax(data)
    rms = np.sqrt(np.nanmean(data**2))

    title = f"{fits_path.name}\n"
    title += (
        f"Peak: {peak:.3e} {bunit}, RMS: {rms:.3e} {bunit}, Mean: {mean:.3e} {bunit}"
    )
    if bmaj and bmin:
        title += f'\nBeam: {bmaj * 3600:.2f}" × {bmin * 3600:.2f}"'
        if bpa:
            title += f" @ {bpa:.1f}°"

    ax.set_title(title, fontsize=11, pad=10)

    # Draw beam ellipse if available
    if bmaj and bmin and wcs is not None:
        from matplotlib.patches import Ellipse

        # Put beam in bottom left corner
        beam_x = 0.1
        beam_y = 0.1
        beam = Ellipse(
            xy=(beam_x, beam_y),
            width=bmin * 3600,  # Convert to arcsec
            height=bmaj * 3600,
            angle=bpa if bpa else 0,
            transform=ax.transAxes,
            facecolor="white",
            edgecolor="black",
            linewidth=1.5,
        )
        ax.add_patch(beam)

    plt.tight_layout()

    # Save
    plt.savefig(output_path, dpi=50, bbox_inches="tight")  # Low-res for quick testing
    print(f"Saved plot to: {output_path}")

    # Also print statistics
    print("\nImage statistics:")
    print(f"  Shape: {data.shape}")
    print(f"  Peak: {peak:.6e} {bunit}")
    print(f"  RMS: {rms:.6e} {bunit}")
    print(f"  Mean: {mean:.6e} {bunit}")
    print(f"  Std: {std:.6e} {bunit}")
    if bmaj and bmin:
        print(f'  Beam: {bmaj * 3600:.2f}" × {bmin * 3600:.2f}"')

    plt.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m dsa110_continuum.utils.plotting.core <fits_file> [output_png] [percentile]")
        print("  percentile: contrast stretch percentile (default: 99.5)")
        sys.exit(1)

    fits_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    percentile = float(sys.argv[3]) if len(sys.argv) > 3 else 99.5

    plot_fits_image(fits_file, output_file, percentile)
