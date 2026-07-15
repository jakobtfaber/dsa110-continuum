"""Per-tile (single-tile FITS) QA glue for the dashboard."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from dsa110_continuum.observability.artifacts import ArtifactRenderError

SCATTER_PATCH = 256
SCATTER_GRID = 3  # central 3x3 patches only — full-grid scattering is offline QA


def _best_image(products: dict[str, Path | None]) -> Path:
    image = products.get("image-pb") or products.get("image")
    if image is None:
        raise ArtifactRenderError("no image product for tile")
    return image


def _load_plane(path: Path):
    import numpy as np
    from astropy.io import fits

    with fits.open(path, memmap=True) as hdus:
        return np.squeeze(hdus[0].data).astype("float32")


def summary(products: dict[str, Path | None], ms_path: Path | None) -> dict:
    """Gate result + residual stats + PSF correlation for one tile."""
    from dsa110_continuum.qa.image_gate import check_image_quality_for_source_finding
    from dsa110_continuum.qa.image_metrics import (
        calculate_psf_correlation,
        calculate_residual_stats,
    )

    image = _best_image(products)
    gate_kwargs = {}
    if ms_path is not None:
        try:
            from dsa110_continuum.qa.noise_model import _extract_integration_time

            gate_kwargs["integration_time_s"] = _extract_integration_time(str(ms_path))
        except Exception:  # default 12.88 s stands
            pass
    gate = asdict(check_image_quality_for_source_finding(str(image), **gate_kwargs))

    residual_path = products.get("residual-pb") or products.get("residual")
    residual = None
    if residual_path is not None:
        try:
            residual = calculate_residual_stats(str(residual_path))
        except Exception as exc:
            residual = {"error": str(exc)}

    psf_correlation = None
    if products.get("dirty") is not None and products.get("psf") is not None:
        try:
            psf_correlation = float(
                calculate_psf_correlation(str(products["dirty"]), str(products["psf"]))
            )
        except Exception:
            psf_correlation = None

    return {
        "gate": gate,
        "residual": residual,
        "psf_correlation": psf_correlation,
        "noise": {"integration_time_s": gate_kwargs.get("integration_time_s")},
    }


def plot_kinds(products: dict[str, Path | None], ms_available: bool) -> tuple[str, ...]:
    """Return the plot kinds available for this tile's on-disk products."""
    kinds = ["image"]
    if products.get("residual-pb") or products.get("residual"):
        kinds.append("residual")
    if products.get("psf") is not None:
        kinds += ["psf_2d", "psf_radial", "sidelobe"]
    kinds.append("scattering")
    if ms_available:
        kinds.append("residual_hist")
    return tuple(kinds)


def render_plot(
    products: dict[str, Path | None], kind: str, target: Path, ms_path: Path | None = None
) -> None:
    """Render one tile plot kind to ``target``; ArtifactRenderError carries the reason."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    try:
        if kind == "image":
            from dsa110_continuum.visualization.fits_plots import plot_fits_image

            figure = plot_fits_image(str(_best_image(products)), output=str(target))
            plt.close(figure)
        elif kind == "residual":
            from dsa110_continuum.visualization.fits_plots import plot_fits_image

            residual = products.get("residual-pb") or products.get("residual")
            if residual is None:
                raise ArtifactRenderError("no residual product")
            figure = plot_fits_image(
                str(residual), output=str(target), title=f"Residual {residual.name}"
            )
            plt.close(figure)
        elif kind in ("psf_2d", "psf_radial", "sidelobe"):
            from dsa110_continuum.visualization import beam_plots

            if products.get("psf") is None:
                raise ArtifactRenderError("no PSF product")
            psf = _load_plane(products["psf"])
            renderer = {
                "psf_2d": beam_plots.plot_psf_2d,
                "psf_radial": beam_plots.plot_psf_radial_profile,
                "sidelobe": beam_plots.plot_sidelobe_analysis,
            }[kind]
            figure = renderer(psf, output=str(target))
            plt.close(figure)
        elif kind == "scattering":
            _render_scattering(_best_image(products), target)
        elif kind == "residual_hist":
            _render_residual_hist(ms_path, target)
        else:
            raise ArtifactRenderError(f"unknown plot kind {kind!r}")
    except ArtifactRenderError:
        raise
    except (ImportError, RuntimeError, OSError, ValueError, KeyError) as exc:
        raise ArtifactRenderError(f"{kind}: {exc}") from exc


def _render_scattering(image_path: Path, target: Path) -> None:
    """Bounded scattering card: score the central 3x3 patch grid only."""
    try:
        import scattering  # noqa: F401
        import torch  # noqa: F401
    except ImportError as exc:
        raise ArtifactRenderError(
            f"scattering library unavailable in this environment: {exc}"
        ) from exc
    import matplotlib.pyplot as plt
    import numpy as np
    from dsa110_continuum.qa.scattering_qa import _get_scattering_calculator, score_patch

    data = _load_plane(image_path)
    ny, nx = data.shape
    if ny < SCATTER_PATCH or nx < SCATTER_PATCH:
        raise ArtifactRenderError(f"image smaller than one {SCATTER_PATCH}px patch")
    half = SCATTER_GRID // 2
    cy, cx = ny // 2, nx // 2
    stc = _get_scattering_calculator(SCATTER_PATCH, 7, 4)  # check_tile_scattering defaults
    scores = np.full((SCATTER_GRID, SCATTER_GRID), np.nan)
    for row in range(SCATTER_GRID):
        for col in range(SCATTER_GRID):
            y0 = cy + (row - half) * SCATTER_PATCH - SCATTER_PATCH // 2
            x0 = cx + (col - half) * SCATTER_PATCH - SCATTER_PATCH // 2
            if y0 < 0 or x0 < 0 or y0 + SCATTER_PATCH > ny or x0 + SCATTER_PATCH > nx:
                continue
            patch = data[y0 : y0 + SCATTER_PATCH, x0 : x0 + SCATTER_PATCH]
            scores[row, col] = score_patch(patch, stc)[0]
    figure, axis = plt.subplots(figsize=(6, 5))
    image = axis.imshow(scores, cmap="RdYlGn", vmin=0.5, vmax=1.0)
    figure.colorbar(image, label="scattering score (central patches)")
    axis.set_title("Scattering QA — central 3×3 patches (bounded; full grid is offline QA)")
    figure.savefig(target, dpi=110, bbox_inches="tight")
    plt.close(figure)


def _render_residual_hist(ms_path: Path | None, target: Path) -> None:
    import matplotlib.pyplot as plt

    if ms_path is None:
        raise ArtifactRenderError("parent MS not on stage")
    from dsa110_continuum.visualization.residual_diagnostics import (
        extract_residuals_from_ms,
        plot_residual_histogram,
    )

    data = extract_residuals_from_ms(str(ms_path), average_channels=True)
    figure = plot_residual_histogram(data, output=str(target))
    plt.close(figure)
