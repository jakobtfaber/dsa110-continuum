"""Coverage and timeline plotting utilities.

This module provides functions for visualizing observation coverage,
both spatially (pointing coverage) and temporally (timeline).
"""

import base64
import io
from datetime import datetime

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np


def _fig_to_base64(fig) -> str:
    """Convert matplotlib figure to base64 encoding string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    buf.seek(0)
    img_str = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return img_str


def plot_pointing_coverage(ras: list[float], decs: list[float]) -> str | None:
    """Generate RA/Dec sky map (Aitoff projection).

    Args:
        ras: List of RA values in degrees.
        decs: List of Dec values in degrees.

    Returns
    -------
        Base64 encoded SVG string.
    """
    if not ras or not decs:
        return None

    fig = plt.figure(figsize=(10, 6))
    ax = fig.add_subplot(111, projection="aitoff")

    # Convert to radians
    ra_rad = np.radians(ras)
    dec_rad = np.radians(decs)

    # Remap RA to [-pi, pi] for Aitoff
    # Standard convention: 0 at center, increasing to left?
    # Matplotlib Aitoff: Longitude increases to the right.
    # Astronomy: RA increases to the East (Left).
    # We map RA [0, 360] -> [-pi, pi]
    # To get standard view with 0 center:
    # shift: ra_rad - pi
    ra_rad = -(ra_rad - np.pi)  # Negate to reverse direction (East-Left)
    # Wrap to [-pi, pi]
    ra_rad = np.remainder(ra_rad + np.pi, 2 * np.pi) - np.pi

    ax.scatter(ra_rad, dec_rad, alpha=0.5, s=10, c="black")
    ax.set_xlabel("RA")
    ax.set_ylabel("Dec")
    ax.set_title("Spatial Coverage (Aitoff)", y=1.1)
    ax.grid(True, color="black", alpha=0.5)

    return _fig_to_base64(fig)


def plot_temporal_coverage(times: list[datetime], duration_minutes: float = 15.0) -> str | None:
    """Generate temporal coverage timeline.

    Args:
        times: List of datetime objects (start times).
        duration_minutes: Assumed duration of each observation.

    Returns
    -------
        Base64 encoded SVG string.
    """
    if not times:
        return None

    fig, ax = plt.subplots(figsize=(10, 3))

    # Convert times to matplotlib date numbers
    # Each bar starts at time t and has width duration
    start_nums = mdates.date2num(times)
    width = duration_minutes / (24 * 60)  # Width in days

    # Create list of (start, width) tuples
    segments = [(t, width) for t in start_nums]

    # Plot broken horizontal bar
    # y-range (0, 1)
    ax.broken_barh(segments, (0, 1), facecolors="black", alpha=0.5)

    # Formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d\n%H:%M"))
    ax.set_xlabel("Time (UTC)")
    ax.set_yticks([])  # Hide y-axis ticks
    ax.set_title("Temporal Coverage Timeline")

    # Set x-limits to slightly pad the data range
    if start_nums.any():
        min_t = min(start_nums)
        max_t = max(start_nums) + width
        pad = (max_t - min_t) * 0.05 if max_t > min_t else width
        ax.set_xlim(min_t - pad, max_t + pad)

    fig.autofmt_xdate()
    ax.grid(True, axis="x", alpha=0.3)

    return _fig_to_base64(fig)
