"""Plotting style utilities for DSA-110 continuum imaging pipeline.

This module provides styling functions for matplotlib plots, ensuring
consistent appearance across the project. Supports multiple output
contexts (quicklook, publication, presentation).

Note: SciencePlots integration was planned but not fully implemented.
These functions provide minimal fallback functionality.
"""

from typing import Literal

import matplotlib.pyplot as plt


# Type aliases
StyleContext = Literal["quicklook", "publication", "presentation", "interactive"]
FigureLayout = Literal["single", "double", "wide", "square"]


def apply_science_style(context: StyleContext = "publication") -> None:
    """Apply science-style formatting to matplotlib.

    Attempts to use SciencePlots if available, otherwise applies
    sensible defaults for scientific plotting.

    Args:
        context: The output context ("quicklook", "publication",
                 "presentation", "interactive")
    """
    try:
        # Try to use SciencePlots if available
        plt.style.use(["science", "notebook"])
    except Exception:
        # Fallback to sensible defaults
        font_sizes = get_font_sizes(10, context)
        plt.rcParams.update({
            "figure.figsize": get_figure_size("single", context),
            "figure.dpi": get_dpi(context),
            "font.size": font_sizes["label"],
            "axes.labelsize": font_sizes["label"],
            "axes.titlesize": font_sizes["title"],
            "legend.fontsize": font_sizes["legend"],
            "xtick.labelsize": font_sizes["tick"],
            "ytick.labelsize": font_sizes["tick"],
            "axes.linewidth": 1.0,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "axes.facecolor": "white",
            "figure.facecolor": "white",
            "savefig.dpi": get_dpi(context),
            "savefig.bbox": "tight",
        })


def reset_to_defaults() -> None:
    """Reset matplotlib to default settings."""
    plt.rcdefaults()


def get_figure_size(
    layout: FigureLayout | StyleContext = "single",
    context: StyleContext = "publication",
) -> tuple[float, float]:
    """Get recommended figure size for the given layout and context.

    Args:
        layout: The figure layout ("single", "double", "wide", "square")
                or context for backward compatibility
        context: The output context ("quicklook", "publication",
                 "presentation", "interactive")

    Returns
    -------
        Tuple of (width, height) in inches
    """
    # Backward compatibility: if only one arg and it's a context, use it
    if layout in ("quicklook", "publication", "presentation", "interactive"):
        context = layout
        layout = "single"

    # Base sizes for single column at different contexts
    base_sizes = {
        "quicklook": {"width": 6, "height": 5},
        "publication": {"width": 8, "height": 6},
        "presentation": {"width": 12, "height": 9},
        "interactive": {"width": 10, "height": 8},
    }

    base = base_sizes.get(context, base_sizes["publication"])

    # Adjust for layout
    layout_multipliers = {
        "single": (1.0, 1.0),
        "double": (2.0, 1.0),
        "wide": (1.5, 0.75),
        "square": (1.0, 1.0),
    }

    mult = layout_multipliers.get(layout, (1.0, 1.0))
    width = base["width"] * mult[0]
    height = base["height"] * mult[1]

    if layout == "square":
        size = min(width, height)
        return (size, size)

    return (width, height)


def get_dpi(context: StyleContext = "publication") -> int:
    """Get recommended DPI for the given context.

    Args:
        context: The output context

    Returns
    -------
        DPI value
    """
    dpis = {
        "quicklook": 140,
        "publication": 300,
        "presentation": 150,
        "interactive": 100,
    }
    return dpis.get(context, 140)


def get_font_sizes(
    base_size: int = 10,
    context: StyleContext = "publication",
) -> dict[str, int]:
    """Get recommended font sizes for the given context.

    Args:
        base_size: Base font size to scale from
        context: The output context

    Returns
    -------
        Dictionary with keys: "title", "label", "tick", "legend", "annotation"
    """
    # Scaling factors for each context
    scales = {
        "quicklook": 1.0,
        "publication": 1.0,
        "presentation": 1.4,
        "interactive": 1.1,
    }

    scale = scales.get(context, 1.0)

    return {
        "title": int(base_size * 1.2 * scale),
        "label": int(base_size * scale),
        "tick": int(base_size * 0.9 * scale),
        "legend": int(base_size * 0.9 * scale),
        "annotation": int(base_size * 0.8 * scale),
    }


def get_output_format(context: StyleContext = "publication") -> str:
    """Get recommended output format for the given context.

    Args:
        context: The output context

    Returns
    -------
        Format string ("png", "pdf", "svg")
    """
    formats = {
        "quicklook": "png",
        "publication": "pdf",
        "presentation": "png",
        "interactive": "png",
    }
    return formats.get(context, "png")
