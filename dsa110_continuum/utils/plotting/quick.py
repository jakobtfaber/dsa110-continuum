#!/usr/bin/env python
"""Ultra-fast minimal FITS plotter for testing.

This module provides a quick way to visualize FITS images by cropping
around the peak and using RMS-based scaling.
"""

import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits


def quick_plot(fits_file, output_file, crop_size=1000):
    """Make a quick low-res plot by cropping around the peak.

    Args:
        fits_file: Path to FITS file
        output_file: Path to output PNG
        crop_size: Size of region around peak to plot (default: 1000 pixels)
    """
    print(f"Loading {fits_file}...")

    with fits.open(fits_file) as hdul:
        data = hdul[0].data

        # Handle 4D/3D/2D data
        if data.ndim == 4:
            img = data[0, 0, :, :]
        elif data.ndim == 3:
            img = data[0, :, :]
        else:
            img = data

    print(f"Original image shape: {img.shape}")

    peak = np.nanmax(img)
    rms = np.nanstd(img)
    print(f"Peak: {peak:.6f} Jy, RMS: {rms:.6f} Jy, SNR: {peak / rms:.1f}")

    # Find peak location
    peak_idx = np.unravel_index(np.nanargmax(img), img.shape)
    print(f"Peak location: {peak_idx}")

    # Crop around the peak to preserve source brightness
    half_size = crop_size // 2
    y_center, x_center = peak_idx
    y_min = max(0, y_center - half_size)
    y_max = min(img.shape[0], y_center + half_size)
    x_min = max(0, x_center - half_size)
    x_max = min(img.shape[1], x_center + half_size)

    img_crop = img[y_min:y_max, x_min:x_max]
    peak_idx_crop = (y_center - y_min, x_center - x_min)

    print(f"Cropped to: {img_crop.shape} centered on peak")
    print(f"Peak in crop: {np.nanmax(img_crop):.6f} Jy (preserved!)")

    # Quick plot with RMS-based scaling
    fig, ax = plt.subplots(1, 1, figsize=(10, 9))

    # Use RMS-based vmin/vmax to show noise and source
    # Key insight: scale to RMS, not peak, so we see the background!
    vmin = -3 * rms  # 3-sigma below zero
    vmax = 20 * rms  # 20-sigma (source will saturate, but we see structure)

    print(f"Colorscale: vmin={vmin:.6f}, vmax={vmax:.6f}")
    print(f"  (Peak is {peak / rms:.1f} sigma, will saturate - that's OK!)")
    print("Plotting...")

    im = ax.imshow(
        img_crop,
        origin="lower",
        cmap="inferno",
        vmin=vmin,
        vmax=vmax,
        interpolation="bilinear",
    )

    plt.colorbar(im, ax=ax, label="Flux (Jy/beam)", fraction=0.046)
    ax.set_title(
        f"Self-calibrated Image (Iteration 1) - {crop_size}×{crop_size} pix region\n"
        f"Peak: {peak * 1e3:.2f} mJy, RMS: {rms * 1e6:.1f} µJy, SNR: {peak / rms:.1f}"
    )
    ax.set_xlabel("X offset (pixels)")
    ax.set_ylabel("Y offset (pixels)")

    # Mark the peak
    ax.plot(peak_idx_crop[1], peak_idx_crop[0], "w+", markersize=20, markeredgewidth=2)
    ax.plot(
        peak_idx_crop[1],
        peak_idx_crop[0],
        "wo",
        markersize=30,
        markeredgewidth=2,
        fillstyle="none",
    )

    print("Saving...")
    plt.tight_layout()
    plt.savefig(output_file, dpi=100, bbox_inches="tight")
    print(f"✓ Saved to {output_file}")
    plt.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: python -m dsa110_continuum.utils.plotting.quick <fits_file> <output_png> [crop_size_pixels]"
        )
        print("  crop_size: size of region around peak to plot (default: 1000)")
        sys.exit(1)

    crop_size = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
    quick_plot(sys.argv[1], sys.argv[2], crop_size)
