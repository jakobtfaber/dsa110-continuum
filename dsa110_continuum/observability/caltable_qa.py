"""Per-calibration-table QA glue for the dashboard (CASA imports stay function-scoped)."""

from __future__ import annotations

import shutil
from pathlib import Path

from dsa110_continuum.observability.artifacts import CALTABLE_NAME_RE, ArtifactRenderError

BASE_KINDS = ("gain_amp", "gain_phase", "flagging", "snr", "dterm", "stability")
BP_KINDS = ("bandpass_amp", "bandpass_phase")
K_KINDS = ("delay", "delay_hist")
_TYPES = {"b": "BP", "g": "G", "k": "K"}


def caltable_type(name: str) -> str:
    """Return the calibration table type badge (BP/G/K) from the file extension."""
    return _TYPES[name.rsplit(".", 1)[1]]


def plot_kinds(name: str) -> tuple[str, ...]:
    """Return the plot kinds available for a table name, keyed on its extension."""
    ext = name.rsplit(".", 1)[1]
    if ext == "b":
        return BASE_KINDS + BP_KINDS
    if ext == "k":
        return BASE_KINDS + K_KINDS
    return BASE_KINDS


def provenance(table_path: Path) -> dict | None:
    """Acquisition provenance from the sidecar written next to the sibling .b table."""
    from dsa110_continuum.calibration.ensure import load_provenance_sidecar

    return load_provenance_sidecar(str(table_path.with_suffix(".b")))


def summary(table_path: Path) -> dict:
    """Quality metrics + per-SPW flagging + SNR summary + provenance for one table."""
    from dsa110_continuum.qa.calibration_quality import (
        analyze_per_spw_flagging,
        extract_gain_snr,
        validate_caltable_quality,
    )

    quality = validate_caltable_quality(str(table_path)).to_dict()
    per_spw = [
        {
            "spw_id": stat.spw_id,
            "fraction_flagged": stat.fraction_flagged,
            "is_problematic": stat.is_problematic,
        }
        for stat in analyze_per_spw_flagging(str(table_path))
    ]
    try:
        snr_summary = extract_gain_snr(str(table_path)).get("summary")
    except Exception as exc:  # SNR/WEIGHT columns are optional
        snr_summary = {"error": str(exc)}
    return {
        "name": table_path.name,
        "cal_type": caltable_type(table_path.name),
        "quality": quality,
        "per_spw": per_spw,
        "snr_summary": snr_summary,
        "provenance": provenance(table_path),
    }


def stability_report(table_path: Path, limit: int = 8) -> dict:
    """In-memory trend report over the newest same-type tables (never touches the DB)."""
    from dsa110_continuum.qa.calibration_stability_tracker import CalibrationStabilityTracker

    suffix = "." + table_path.name.rsplit(".", 1)[1]
    siblings = sorted(
        (
            path
            for path in table_path.parent.iterdir()
            if CALTABLE_NAME_RE.fullmatch(path.name) and path.name.endswith(suffix)
        ),
        key=lambda path: path.stat().st_mtime,
    )[-limit:]
    tracker = CalibrationStabilityTracker(persist=False)
    for sibling in siblings:
        tracker.update_from_caltable(str(sibling))
    report = tracker.generate_report().to_dict()
    report["n_tables"] = len(siblings)
    return report


def render_plot(table_path: Path, kind: str, target: Path) -> None:
    """Render one plot kind to ``target``; raise ArtifactRenderError with a reason."""
    workdir = target.parent / f"{target.name}.work"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    try:
        produced = _render_into(table_path, kind, workdir)
        shutil.move(str(produced), str(target))
    except ArtifactRenderError:
        raise
    except (ImportError, RuntimeError, OSError, ValueError, KeyError) as exc:
        raise ArtifactRenderError(f"{kind}: {exc}") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _first(paths) -> Path:
    paths = [Path(p) for p in paths]
    if not paths:
        raise ArtifactRenderError("plot function produced no figure")
    return paths[0]


def _render_into(table_path: Path, kind: str, workdir: Path) -> Path:
    table = str(table_path)
    if kind in ("gain_amp", "gain_phase"):
        from dsa110_continuum.visualization.calibration_plots import plot_gains

        return _first(
            plot_gains(
                table,
                output=workdir,
                plot_amplitude=kind == "gain_amp",
                plot_phase=kind == "gain_phase",
            )
        )
    if kind == "flagging":
        from dsa110_continuum.visualization.calibration_plots import plot_flagging_diagnostics

        return _first(plot_flagging_diagnostics(table, output=workdir))
    if kind == "snr":
        from dsa110_continuum.visualization.calibration_plots import plot_gain_snr

        return _first(plot_gain_snr(table, output=workdir))
    if kind == "dterm":
        from dsa110_continuum.visualization.calibration_plots import plot_dterm_scatter

        return _first(plot_dterm_scatter(table, output=workdir))
    if kind in ("bandpass_amp", "bandpass_phase"):
        from dsa110_continuum.visualization.calibration_plots import plot_bandpass

        return _first(
            plot_bandpass(
                table,
                output=workdir,
                plot_amplitude=kind == "bandpass_amp",
                plot_phase=kind == "bandpass_phase",
            )
        )
    if kind in ("delay", "delay_hist"):
        from dsa110_continuum.visualization.kcal_delay_plots import plot_kcal_delays

        produced = [Path(p) for p in plot_kcal_delays(table, output=workdir)]
        wanted = [p for p in produced if ("_delay_hist" in p.name) == (kind == "delay_hist")]
        return _first(wanted)
    if kind == "stability":
        return _render_stability(table_path, workdir)
    raise ArtifactRenderError(f"unknown plot kind {kind!r}")


def _render_stability(table_path: Path, workdir: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report = stability_report(table_path)
    details = report.get("antenna_details") or {}
    if not details:
        raise ArtifactRenderError("no stability history available on stage")
    antennas = sorted(details, key=int)
    amp = [details[antenna]["amp_trend_per_obs"] for antenna in antennas]
    phase = [details[antenna]["phase_trend_deg_per_obs"] for antenna in antennas]
    flagged = [
        details[antenna]["is_drifting_amplitude"]
        or details[antenna]["is_drifting_phase"]
        or details[antenna]["is_outlier"]
        for antenna in antennas
    ]
    colors = ["#ff6470" if bad else "#4eb8ff" for bad in flagged]
    figure, (ax_amp, ax_phase) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    ax_amp.bar(range(len(antennas)), amp, color=colors)
    ax_amp.set_ylabel("amp trend / obs")
    ax_phase.bar(range(len(antennas)), phase, color=colors)
    ax_phase.set_ylabel("phase trend (deg/obs)")
    ax_phase.set_xlabel(f"antenna (over {report['n_tables']} tables; red = drifting/outlier)")
    figure.suptitle(f"Gain stability · {table_path.name}")
    out = workdir / "stability.png"
    figure.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(figure)
    return out
