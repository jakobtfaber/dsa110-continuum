"""Per-Measurement-Set QA glue for the dashboard (bounded, lazy, cached by the caller)."""

from __future__ import annotations

from pathlib import Path

from dsa110_continuum.observability.artifacts import ArtifactRenderError

UVW_SAMPLE = 2000
# Contiguous rows (stride 1) keep complete baseline sets per integration —
# strided sampling leaves most antenna triangles without all three baselines.
CLOSURE_LIMITS = {"max_rows": 20000, "row_stride": 1, "max_channels": 4}

BASE_KINDS = (
    "uv_coverage",
    "elevation",
    "parallactic",
    "rfi_waterfall",
    "autocorr_heatmap",
    "closure_hist",
)


def _bp_table(ms_path: Path) -> Path | None:
    stem = ms_path.name.rsplit(".", 1)[0].replace("_meridian", "")
    candidate = ms_path.parent / f"{stem}_0~23.b"
    return candidate if candidate.is_dir() else None


def plot_kinds(ms_path: Path) -> tuple[str, ...]:
    """Return the plot kinds available for this MS."""
    kinds = list(BASE_KINDS)
    if _bp_table(ms_path) is not None:
        kinds.append("bandpass_diag")
    return tuple(kinds)


def summary(ms_path: Path) -> dict:
    """Conversion + UVW + RFI occupancy summary; every section degrades independently."""
    from dsa110_continuum.qa.pipeline_quality import check_ms_after_conversion

    passed, conversion = check_ms_after_conversion(str(ms_path))
    result: dict = {"conversion": conversion, "conversion_passed": bool(passed)}
    try:
        from dsa110_continuum.qa.uvw_validation import validate_uvw_geometry

        uvw = validate_uvw_geometry(str(ms_path), sample_size=UVW_SAMPLE)
        result["uvw"] = {
            "is_valid": uvw.is_valid,
            "n_violations": uvw.n_violations,
            "violation_fraction": uvw.violation_fraction,
            "max_uvw_distance_m": uvw.max_uvw_distance_m,
        }
    except Exception as exc:
        result["uvw"] = {"error": str(exc)}
    try:
        from dsa110_continuum.qa.rfi_metrics import calculate_rfi_occupancy

        occupancy = calculate_rfi_occupancy(ms_path)
        result["rfi"] = {
            "total_occupancy": float(occupancy["total_occupancy"]),
            "n_channels": int(occupancy["n_channels"]),
            "n_rows": int(occupancy["n_rows"]),
        }
    except Exception as exc:
        result["rfi"] = {"error": str(exc)}
    return result


def _uv_lambda(ms_path: Path):
    import numpy as np
    from dsa110_continuum.adapters.casa_tables import table

    with table(str(ms_path)) as ms:
        uvw = ms.getcol("UVW")
    with table(str(ms_path) + "/SPECTRAL_WINDOW") as spw:
        freq_hz = float(np.mean(spw.getcol("CHAN_FREQ")))
    wavelength_m = 299792458.0 / freq_hz
    return uvw[:, 0] / wavelength_m, uvw[:, 1] / wavelength_m


def render_plot(ms_path: Path, kind: str, target: Path) -> None:
    """Render one MS plot kind to ``target``; ArtifactRenderError carries the reason."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    try:
        if kind == "uv_coverage":
            from dsa110_continuum.visualization.uv_plots import plot_uv_coverage

            u_lambda, v_lambda = _uv_lambda(ms_path)
            figure = plot_uv_coverage(
                u_lambda, v_lambda, output=str(target), title=f"UV coverage · {ms_path.name}"
            )
            plt.close(figure)
        elif kind in ("elevation", "parallactic"):
            from dsa110_continuum.visualization.elevation_plots import (
                extract_geometry_from_ms,
                plot_elevation_vs_time,
                plot_parallactic_angle_vs_time,
            )

            geometry = extract_geometry_from_ms(str(ms_path))
            if kind == "elevation":
                figure = plot_elevation_vs_time(
                    geometry["times"], geometry["elevation_deg"], output=str(target)
                )
            else:
                figure = plot_parallactic_angle_vs_time(
                    geometry["times"], geometry["parallactic_angle_deg"], output=str(target)
                )
            plt.close(figure)
        elif kind == "rfi_waterfall":
            import numpy as np
            from dsa110_continuum.qa.rfi_metrics import get_rfi_waterfall_data

            waterfall, times, freqs = get_rfi_waterfall_data(ms_path)
            figure, axis = plt.subplots(figsize=(10, 4))
            mesh = axis.imshow(
                waterfall,
                origin="lower",
                aspect="auto",
                cmap="inferno",
                extent=[freqs.min() / 1e6, freqs.max() / 1e6, 0, len(np.atleast_1d(times))],
            )
            figure.colorbar(mesh, label="flag occupancy")
            axis.set_xlabel("frequency (MHz)")
            axis.set_ylabel("time bin")
            axis.set_title(f"RFI flag waterfall · {ms_path.name}")
            figure.savefig(target, dpi=110, bbox_inches="tight")
            plt.close(figure)
        elif kind == "autocorr_heatmap":
            from dsa110_continuum.visualization.tsys_plots import (
                extract_tsys_from_ms,
                plot_tsys_heatmap,
            )

            data = extract_tsys_from_ms(str(ms_path))
            figure = plot_tsys_heatmap(
                data["times"],
                data["tsys"],
                output=str(target),
                antenna_names=data.get("antenna_names"),
                title="Autocorrelation amplitude (uncalibrated Tsys proxy)",
            )
            plt.close(figure)
        elif kind == "closure_hist":
            from dsa110_continuum.visualization.closure_phase_plots import (
                compute_closure_phases,
                extract_closure_phases_from_ms,
                plot_closure_phase_histogram,
            )

            raw = extract_closure_phases_from_ms(str(ms_path), **CLOSURE_LIMITS)
            closure = compute_closure_phases(raw["visibility"], raw["antenna1"], raw["antenna2"])
            figure = plot_closure_phase_histogram(closure, output=str(target))
            plt.close(figure)
        elif kind == "bandpass_diag":
            _render_bandpass_diag(ms_path, target)
        else:
            raise ArtifactRenderError(f"unknown plot kind {kind!r}")
    except ArtifactRenderError:
        raise
    except Exception as exc:
        raise ArtifactRenderError(f"{kind}: {type(exc).__name__}: {exc}") from exc


def _render_bandpass_diag(ms_path: Path, target: Path) -> None:
    """Figure 1 of the bandpass diagnostic set (per-antenna amplitude overview)."""
    import shutil
    import tempfile

    from dsa110_continuum.visualization.bandpass_diagnostics import load_data, plot_figure1

    bp_table = _bp_table(ms_path)
    if bp_table is None:
        raise ArtifactRenderError("no same-timestamp bandpass table on stage")
    workdir = Path(tempfile.mkdtemp(prefix="bpdiag_", dir=str(target.parent)))
    try:
        data = load_data(str(ms_path), str(bp_table))
        plot_figure1(data, workdir, ms_path.name)
        pngs = sorted(workdir.glob("*.png"))
        if not pngs:
            raise ArtifactRenderError("bandpass diagnostics produced no figure")
        shutil.move(str(pngs[0]), str(target))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
