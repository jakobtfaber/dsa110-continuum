# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# as part of the contimg-import-retirement migration (docs/rse/specs/plan-contimg-import-retirement.md).
"""Plotting utilities for DSA-110 continuum imaging pipeline.

This package consolidates all plotting functionality:
- core: Standard FITS image plotting with nice visualization
- fast: Optimized FITS plotting for speed
- utils: Coverage and timeline plots (temporal, pointing)
- quick: Ultra-fast minimal FITS plotter for testing
- style: Matplotlib styling utilities for consistent appearance

Usage:
    from dsa110_continuum.utils.plotting import (
        # Core FITS plotting
        plot_fits_image,
        # Fast FITS plotting
        plot_fits_fast,
        # Quick testing plots
        quick_plot,
        # Coverage plots
        plot_pointing_coverage,
        plot_temporal_coverage,
        # Styling
        apply_science_style,
        reset_to_defaults,
        get_figure_size,
        get_dpi,
        get_font_sizes,
        get_output_format,
    )
"""

from dsa110_continuum.utils.plotting.core import plot_fits_image
from dsa110_continuum.utils.plotting.fast import plot_fits_fast
from dsa110_continuum.utils.plotting.quick import quick_plot
from dsa110_continuum.utils.plotting.style import (
    apply_science_style,
    get_dpi,
    get_figure_size,
    get_font_sizes,
    get_output_format,
    reset_to_defaults,
)
from dsa110_continuum.utils.plotting.utils import (
    plot_pointing_coverage,
    plot_temporal_coverage,
)

__all__ = [
    # Core
    "plot_fits_image",
    # Fast
    "plot_fits_fast",
    # Quick
    "quick_plot",
    # Utils
    "plot_pointing_coverage",
    "plot_temporal_coverage",
    # Style
    "apply_science_style",
    "reset_to_defaults",
    "get_figure_size",
    "get_dpi",
    "get_font_sizes",
    "get_output_format",
]
