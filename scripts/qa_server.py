"""DSA-110 continuum QA and operations dashboard."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from astropy.io import fits
from dsa110_continuum.observability import control as pipeline_control
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from matplotlib.colors import PowerNorm
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardConfig:
    """Filesystem and campaign settings for one dashboard instance."""

    stage: Path = Path(os.environ.get("DSA110_STAGE", "/stage/dsa110-contimg"))
    products: Path = Path(
        os.environ.get("DSA110_PRODUCTS_BASE", "/data/dsa110-proc/products/mosaics")
    )
    incoming: Path = Path(os.environ.get("DSA110_INCOMING", "/data/incoming"))
    thumb_dir: Path = Path(os.environ.get("DSA110_QA_THUMBS", "/tmp/qa_thumbs"))
    campaign_outputs: Path = Path(
        os.environ.get(
            "DSA110_CAMPAIGN_OUTPUTS",
            "/data/dsa110-continuum/outputs/slowvis-mosaic-campaign-2026-07-14",
        )
    )
    campaign_date: str = os.environ.get("DSA110_CAMPAIGN_DATE", "2026-07-13")
    campaign_hour: int = int(os.environ.get("DSA110_CAMPAIGN_HOUR", "11"))


EPOCHS = [
    ("2026-01-25", "T0200"),
    ("2026-02-12", "T0000"),
    ("2026-02-15", "T0000"),
    ("2026-02-23", "T0000"),
    ("2026-02-25", "T0000"),
    ("2026-02-26", "T0000"),
]

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
EPOCH_RE = re.compile(r"T\d{4}")
PROCESS_NAMES = ("batch_pipeline.py", "wsclean", "aoflagger")


def _config(request: Request) -> DashboardConfig:
    return request.app.state.dashboard_config


def _validate_date_epoch(date: str, epoch: str | None = None) -> None:
    if not DATE_RE.fullmatch(date) or (epoch is not None and not EPOCH_RE.fullmatch(epoch)):
        raise HTTPException(status_code=400, detail="Invalid date or epoch")


def find_mosaic(config: DashboardConfig, date: str, epoch: str) -> Path | None:
    """Return the canonical hourly-epoch mosaic path when it exists."""
    path = config.stage / f"images/mosaic_{date}" / f"{date}{epoch}_mosaic.fits"
    return path if path.is_file() else None


def find_csv(config: DashboardConfig, date: str, epoch: str) -> Path | None:
    """Return the newest matching forced-photometry table when present."""
    directory = config.products / date
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(f"*{epoch}*phot.csv"))
    return matches[-1] if matches else None


def cal_tables(config: DashboardConfig) -> set[str]:
    """Return the observation prefixes with bandpass tables."""
    directory = config.stage / "ms"
    if not directory.is_dir():
        return set()
    return {path.stem.split("_")[0] for path in directory.glob("*.b")}


def get_metrics(config: DashboardConfig, date: str, epoch: str) -> dict:
    """Measure the legacy QA fields for one hourly-epoch mosaic."""
    fits_path = find_mosaic(config, date, epoch)
    csv_path = find_csv(config, date, epoch)
    metrics = {
        "date": date,
        "epoch": epoch,
        "fits": fits_path is not None,
        "csv": csv_path is not None,
        "mosaic_path": str(fits_path) if fits_path else None,
        "photometry_path": str(csv_path) if csv_path else None,
        "thumbnail_url": f"/artifacts/mosaic/{date}/{epoch}/thumb.png",
        "peak": None,
        "rms": None,
        "dr": None,
        "ratio": None,
        "n_bright": 0,
        "status": "missing",
    }
    if not fits_path:
        return metrics
    try:
        with fits.open(fits_path, memmap=True) as hdus:
            data = hdus[0].data.squeeze().astype(np.float32)
        finite = data[np.isfinite(data)]
        metrics["peak"] = float(np.nanmax(finite)) if finite.size else None
        background = data[np.isfinite(data) & (np.abs(data) < 0.05)]
        metrics["rms"] = float(np.nanstd(background)) * 1000 if background.size else None
        if metrics["peak"] is not None and metrics["rms"]:
            metrics["dr"] = metrics["peak"] / (metrics["rms"] / 1000)
    except Exception as exc:
        logger.warning("FITS error %s %s: %s", date, epoch, exc)
    if csv_path:
        try:
            frame = pd.read_csv(csv_path)
            flux = frame.get("nvss_flux_jy", pd.Series(index=frame.index, dtype=float))
            bright = frame[flux > 0.06]
            metrics["n_bright"] = len(bright)
            if len(bright) and "dsa_nvss_ratio" in bright:
                metrics["ratio"] = float(bright["dsa_nvss_ratio"].median())
        except Exception as exc:
            logger.warning("CSV error %s %s: %s", date, epoch, exc)
    ratio = metrics["ratio"]
    if ratio is not None and not np.isfinite(ratio):
        metrics["ratio"] = None
        ratio = None
    if ratio is not None:
        metrics["status"] = "pass" if 0.8 <= ratio <= 1.2 else "fail"
    else:
        metrics["status"] = "no_phot"
    return metrics


def make_thumbnail(config: DashboardConfig, date: str, epoch: str) -> Path | None:
    """Build or reuse a cached PNG for one hourly-epoch mosaic."""
    fits_path = find_mosaic(config, date, epoch)
    if not fits_path:
        return None
    key = hashlib.md5(f"{fits_path}{fits_path.stat().st_mtime}".encode()).hexdigest()[:8]
    output = config.thumb_dir / f"{date}_{epoch}_{key}.png"
    if output.exists():
        return output
    for old in config.thumb_dir.glob(f"{date}_{epoch}_*.png"):
        old.unlink(missing_ok=True)
    try:
        with fits.open(fits_path, memmap=True) as hdus:
            data = hdus[0].data.squeeze().astype(np.float32)
        finite = data[np.isfinite(data)]
        if not finite.size:
            return None
        vmax = float(np.nanpercentile(finite, 99.5))
        background = data[np.isfinite(data) & (np.abs(data) < 0.05)]
        rms = float(np.nanstd(background)) if background.size else 0.0
        peak = float(np.nanmax(finite))
        figure, axis = plt.subplots(figsize=(12, 4), dpi=90)
        axis.imshow(
            data,
            origin="lower",
            cmap="inferno",
            norm=PowerNorm(gamma=0.35, vmin=0, vmax=max(vmax, np.finfo(float).eps)),
            aspect="auto",
        )
        dynamic_range = f"{peak / rms:.0f}" if rms else "--"
        axis.set_title(
            f"{date}  |  Peak {peak:.2f} Jy  RMS {rms * 1000:.1f} mJy/beam  DR {dynamic_range}",
            color="white",
            fontsize=10,
            pad=4,
        )
        axis.axis("off")
        figure.patch.set_facecolor("#111")
        plt.tight_layout(pad=0.2)
        plt.savefig(output, dpi=90, bbox_inches="tight", facecolor="#111")
        plt.close(figure)
        return output
    except Exception as exc:
        logger.error("Thumbnail error %s %s: %s", date, epoch, exc)
        return None


def _file_record(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _latest(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.is_file()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _tail(path: Path | None, lines: int = 24) -> list[str]:
    if path is None:
        return []
    try:
        with path.open(errors="replace") as stream:
            return stream.readlines()[-lines:]
    except OSError as exc:
        return [f"Unable to read {path}: {exc}"]


def disk_status() -> dict:
    """Report read-only capacity information for the data volumes."""
    result = {}
    for path in (Path("/stage"), Path("/data")):
        try:
            usage = shutil.disk_usage(path)
            result[str(path)] = {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "pct_used": round(usage.used / usage.total * 100, 1),
            }
        except OSError as exc:
            result[str(path)] = {"error": str(exc)}
    return result


def process_status() -> list[dict]:
    """List only continuum pipeline processes relevant to operators."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,etimes=,stat=,args="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [{"error": str(exc)}]
    processes = []
    for line in result.stdout.splitlines():
        if not any(name in line.lower() for name in PROCESS_NAMES):
            continue
        parts = line.strip().split(maxsplit=3)
        if len(parts) == 4:
            processes.append(
                {
                    "pid": int(parts[0]),
                    "elapsed_seconds": int(parts[1]),
                    "state": parts[2],
                    "command": parts[3],
                }
            )
    return processes


def _pid_hints(config: DashboardConfig) -> list[dict]:
    hints = []
    if not config.campaign_outputs.is_dir():
        return hints
    for path in sorted(config.campaign_outputs.glob("*.pid")):
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        for label, raw_pid in re.findall(r"([A-Z_]*PID)=(\d+)", content):
            pid = int(raw_pid)
            hints.append(
                {
                    "label": label,
                    "pid": pid,
                    "visible": Path(f"/proc/{pid}").exists(),
                    "source": str(path),
                }
            )
    return hints


def campaign_status(config: DashboardConfig, date: str, hour: int) -> dict:
    """Collect current filesystem, log, disk, and process state for an epoch."""
    _validate_date_epoch(date)
    if not 0 <= hour <= 23:
        raise HTTPException(status_code=400, detail="Hour must be between 0 and 23")
    prefix = f"{date}T{hour:02d}"
    ms_dir = config.stage / "ms"
    image_dir = config.stage / f"images/mosaic_{date}"
    product_dir = config.products / date
    ms_files = sorted(ms_dir.glob(f"{prefix}:*.ms")) if ms_dir.is_dir() else []
    bandpass = sorted(ms_dir.glob(f"{prefix}:*.b")) if ms_dir.is_dir() else []
    gains = sorted(ms_dir.glob(f"{prefix}:*.g")) if ms_dir.is_dir() else []
    tile_images = sorted(image_dir.glob(f"{prefix}:*-image.fits")) if image_dir.is_dir() else []
    mosaic = image_dir / f"{date}T{hour:02d}00_mosaic.fits"
    incoming_files = (
        sorted(config.incoming.glob(f"{prefix}:*_sb*.hdf5")) if config.incoming.is_dir() else []
    )
    logs = list(product_dir.glob("run_*.log")) if product_dir.is_dir() else []
    if config.campaign_outputs.is_dir():
        logs.extend(config.campaign_outputs.glob(f"batch_run_h{hour:02d}*.log"))
        logs.extend(config.campaign_outputs.glob(f"batch_run_h{hour}*.log"))
    latest_log = _latest(list(dict.fromkeys(logs)))
    manifest = product_dir / f"{date}_manifest.json"
    summary = product_dir / f"{date}_run_summary.json"
    report = product_dir / "run_report.md"
    processes = process_status()
    stages = [
        {
            "name": "Measurement Sets",
            "state": "ready" if ms_files else "not_yet",
            "count": len(ms_files),
        },
        {
            "name": "Calibration tables",
            "state": "ready" if bandpass and gains else "not_yet",
            "count": len(bandpass) + len(gains),
        },
        {
            "name": "Tile images",
            "state": "ready" if tile_images else "not_yet",
            "count": len(tile_images),
        },
        {
            "name": "Hourly-epoch mosaic",
            "state": "ready" if mosaic.is_file() else "not_yet",
            "count": int(mosaic.is_file()),
        },
    ]
    return {
        "date": date,
        "hour": hour,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stages": stages,
        "measurement_sets": {"count": len(ms_files), "paths": [str(path) for path in ms_files]},
        "calibration": {
            "bandpass": [_file_record(path) for path in bandpass],
            "gains": [_file_record(path) for path in gains],
        },
        "tiles": {"count": len(tile_images), "latest": _file_record(_latest(tile_images))},
        "mosaic": _file_record(mosaic),
        "run_products": {
            "manifest": _file_record(manifest),
            "summary": _file_record(summary),
            "report": _file_record(report),
        },
        "log": {"file": _file_record(latest_log), "tail": _tail(latest_log)},
        "incoming": {
            "directory": str(config.incoming),
            "hdf5_count": len(incoming_files),
            "latest": _file_record(_latest(incoming_files)),
        },
        "disks": disk_status(),
        "processes": processes,
        "pid_hints": _pid_hints(config),
    }


def _format_number(value, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    return f"{value:.{decimals}f}"


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    number = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if number < 1024 or unit == "PB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return "—"


def _badge(state: str, label: str | None = None) -> str:
    colors = {
        "pass": "#41c97a",
        "ready": "#41c97a",
        "active": "#4eb8ff",
        "fail": "#ff6470",
        "missing": "#69717d",
        "not_yet": "#d99b35",
        "no_phot": "#d99b35",
    }
    text = label or state.replace("_", " ")
    return f'<span class="badge" style="--badge:{colors.get(state, "#69717d")}">{html.escape(text.upper())}</span>'


def _ratio_cell(ratio) -> str:
    if ratio is None or (isinstance(ratio, float) and np.isnan(ratio)):
        return "—"
    color = "#41c97a" if 0.8 <= ratio <= 1.2 else "#ff6470"
    return f'<span style="color:{color};font-weight:700">{ratio:.3f}</span>'


def render_dashboard(config: DashboardConfig) -> str:
    """Render the consolidated QA and operations dashboard."""
    all_metrics = [get_metrics(config, date, epoch) for date, epoch in EPOCHS]
    cals = cal_tables(config)
    campaign = campaign_status(config, config.campaign_date, config.campaign_hour)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n_pass = sum(metric["status"] == "pass" for metric in all_metrics)
    n_fail = sum(metric["status"] == "fail" for metric in all_metrics)
    n_missing = sum(metric["status"] in ("missing", "no_phot") for metric in all_metrics)

    rows = []
    cards = []
    for metric in all_metrics:
        has_cal = any(metric["date"] in name for name in cals)
        rows.append(
            f"<tr><td><b>{metric['date']}</b><small>{metric['epoch']}</small></td>"
            f"<td>{_badge(metric['status'])}</td>"
            f"<td>{_badge('ready' if has_cal else 'missing', 'yes' if has_cal else 'no')}</td>"
            f"<td>{_format_number(metric['peak'])}</td>"
            f"<td>{_format_number(metric['rms'], 1)}</td>"
            f"<td>{_format_number(metric['dr'], 0)}</td>"
            f"<td>{_ratio_cell(metric['ratio'])}</td><td>{metric['n_bright']}</td></tr>"
        )
        if metric["fits"]:
            image = (
                f'<img src="{metric["thumbnail_url"]}" alt="{metric["date"]} mosaic" '
                'loading="lazy">'
            )
        else:
            image = '<div class="empty">No mosaic</div>'
        cards.append(
            f'<article class="mosaic-card status-{metric["status"]}"><header><b>{metric["date"]}</b>'
            f"{_badge(metric['status'])}</header>{image}<footer>Peak {_format_number(metric['peak'])} Jy"
            f"<span>RMS {_format_number(metric['rms'], 1)} mJy</span>"
            f"<span>Ratio {_ratio_cell(metric['ratio'])}</span></footer></article>"
        )

    stage_cards = []
    for stage in campaign["stages"]:
        stage_cards.append(
            f'<div class="stage"><span>{html.escape(stage["name"])}</span>'
            f"{_badge(stage['state'])}<strong>{stage['count']}</strong></div>"
        )
    disk_cards = []
    for path, disk in campaign["disks"].items():
        if "error" in disk:
            detail = html.escape(disk["error"])
            percent = "unknown"
        else:
            detail = (
                f"{_human_bytes(disk['free_bytes'])} free of {_human_bytes(disk['total_bytes'])}"
            )
            percent = f"{disk['pct_used']}% used"
        disk_cards.append(
            f'<div class="info-card"><span>{html.escape(path)}</span><strong>{percent}</strong><small>{detail}</small></div>'
        )
    processes = campaign["processes"]
    if processes:
        process_rows = "".join(
            f"<li><code>PID {process.get('pid', '—')}</code><span>{html.escape(process.get('command', process.get('error', '')))}</span></li>"
            for process in processes
        )
    elif campaign["pid_hints"]:
        process_rows = "".join(
            f"<li><code>PID {hint['pid']}</code><span>{html.escape(hint['label'])} recorded in "
            f"{html.escape(hint['source'])}; "
            f"{'visible' if hint['visible'] else 'not visible in the server process namespace'}</span></li>"
            for hint in campaign["pid_hints"]
        )
    else:
        process_rows = (
            '<li class="empty-row">No batch_pipeline, WSClean, or AOFlagger process visible</li>'
        )
    log_record = campaign["log"]["file"]
    log_title = html.escape(log_record["path"]) if log_record else "No campaign log found"
    log_tail = html.escape("".join(campaign["log"]["tail"]) or "No log lines available")
    mosaic_path = campaign["mosaic"]["path"] if campaign["mosaic"] else "Not yet produced"
    run_summary = campaign["run_products"]["summary"]
    run_summary_path = run_summary["path"] if run_summary else "Not yet produced"
    incoming = campaign["incoming"]

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DSA-110 Continuum Observatory</title>
<style>
:root{{--ink:#e8edf2;--muted:#87919d;--panel:#171b20;--line:#2a3038;--blue:#4eb8ff;--green:#41c97a}}
*{{box-sizing:border-box}} body{{background:#0d1014;color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0}}
body:before{{content:"";display:block;height:3px;background:linear-gradient(90deg,#4eb8ff,#41c97a 45%,#d99b35)}}
.shell{{max-width:1500px;margin:auto;padding:22px}} h1{{font-size:1.5rem;margin:0;letter-spacing:.01em}} h2{{font-size:1rem;margin:0 0 14px;text-transform:uppercase;letter-spacing:.12em;color:#bcc6d0}}
.subtitle{{color:var(--muted);font-size:.82rem;margin:5px 0 24px}} section{{margin-bottom:34px}} .summary,.stage-grid,.info-grid{{display:grid;gap:10px}}
.summary{{grid-template-columns:repeat(4,minmax(110px,1fr));max-width:620px}} .stat,.stage,.info-card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:13px 16px}}
.stat strong{{display:block;font-size:1.7rem}} .stat span,.stage span,.info-card span{{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.08em}}
.campaign{{border:1px solid #2f4553;background:linear-gradient(135deg,#131a20,#14181d);border-radius:12px;padding:18px}} .campaign-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:16px}}
.campaign-head h2{{margin:0;color:#dce9f2}} .campaign-head p{{margin:4px 0 0;color:var(--muted);font-size:.82rem}} .stage-grid{{grid-template-columns:repeat(4,1fr)}}
.stage{{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center}} .stage strong{{grid-column:1/-1;font-size:1.8rem}} .info-grid{{grid-template-columns:repeat(3,1fr);margin-top:10px}}
.info-card strong,.info-card small{{display:block;margin-top:7px}} .info-card small{{color:var(--muted)}} .path{{font-family:ui-monospace,SFMono-Regular,monospace;word-break:break-all;color:#a9c8dd}}
.badge{{display:inline-block;background:var(--badge);color:#081014;padding:3px 8px;border-radius:999px;font-size:.68rem;font-weight:800;letter-spacing:.05em;white-space:nowrap}}
.ops-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}} .panel{{background:#101419;border:1px solid var(--line);border-radius:8px;padding:14px;min-width:0}}
.panel h3{{font-size:.76rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:0 0 10px}} .process-list{{list-style:none;padding:0;margin:0;max-height:180px;overflow:auto}} .process-list li{{display:flex;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:.76rem}} .process-list li:first-child{{border:0}} .process-list span{{word-break:break-all;color:#bdc6cf}}
pre{{background:#090b0e;color:#b9c6d0;border-radius:6px;padding:12px;margin:0;max-height:250px;overflow:auto;font-size:.72rem;line-height:1.45;white-space:pre-wrap}} .log-path{{color:var(--muted);font-size:.68rem;word-break:break-all;margin-bottom:8px}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:9px}} table{{width:100%;border-collapse:collapse;font-size:.82rem}} th,td{{padding:10px 12px;text-align:center;border-bottom:1px solid var(--line);white-space:nowrap}} th{{background:#171b20;color:#9da8b4;font-weight:600}} td:first-child{{text-align:left}} td small{{display:block;color:var(--muted);margin-top:2px}} tr:last-child td{{border:0}} tr:hover{{background:#14191f}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}} .mosaic-card{{background:var(--panel);border:1px solid var(--line);border-radius:9px;overflow:hidden}} .mosaic-card header,.mosaic-card footer{{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 12px}} .mosaic-card img{{display:block;width:100%;min-height:140px;object-fit:cover;background:#090b0e}} .mosaic-card footer{{font-size:.75rem;color:#aeb7c0;flex-wrap:wrap}} .empty{{display:grid;place-items:center;min-height:140px;background:#101318;color:#5d6670}} .empty-row{{color:var(--muted)}}
@media(max-width:850px){{.stage-grid,.info-grid{{grid-template-columns:repeat(2,1fr)}}.ops-grid{{grid-template-columns:1fr}}}} @media(max-width:560px){{.shell{{padding:16px}}.summary,.stage-grid,.info-grid{{grid-template-columns:1fr 1fr}}.grid{{grid-template-columns:1fr}}}}
</style><script>setTimeout(()=>location.reload(),30000)</script></head>
<body><main class="shell"><h1>DSA-110 Continuum Observatory</h1><div class="subtitle">Updated {now} · auto-refresh 30s · read-only</div>
<section class="campaign"><div class="campaign-head"><div><h2>Active campaign · {campaign["date"]} hour {campaign["hour"]:02d}</h2><p>Hourly-epoch mosaic progress from incoming HDF5 through science products</p></div>{_badge("active" if processes else "not_yet", "process visible" if processes else ("PID recorded" if campaign["pid_hints"] else "process not visible"))}</div>
<div class="stage-grid">{"".join(stage_cards)}</div><div class="info-grid">{"".join(disk_cards)}
<div class="info-card"><span>Incoming slow-vis</span><strong>{incoming["hdf5_count"]} HDF5</strong><small class="path">{html.escape(incoming["directory"])}</small></div></div>
<div class="info-grid"><div class="info-card"><span>MS root</span><strong>{campaign["measurement_sets"]["count"]} hour-11 MS</strong><small class="path">{html.escape(str(config.stage / "ms"))}</small></div>
<div class="info-card"><span>Tile root</span><strong>{campaign["tiles"]["count"]} image FITS</strong><small class="path">{html.escape(str(config.stage / f"images/mosaic_{campaign['date']}"))}</small></div>
<div class="info-card"><span>Hourly-epoch mosaic</span><strong>{"available" if campaign["mosaic"] else "not yet"}</strong><small class="path">{html.escape(mosaic_path)}</small></div>
<div class="info-card"><span>Run summary</span><strong>{"available" if run_summary else "not yet"}</strong><small class="path">{html.escape(run_summary_path)}</small></div></div>
<div class="ops-grid"><div class="panel"><h3>Pipeline heartbeat</h3><ul class="process-list">{process_rows}</ul></div>
<div class="panel"><h3>Latest batch log</h3><div class="log-path">{log_title}</div><pre>{log_tail}</pre></div></div></section>
<section><h2>Historical hourly-epoch QA</h2><div class="summary"><div class="stat"><strong style="color:#41c97a">{n_pass}</strong><span>Pass</span></div><div class="stat"><strong style="color:#ff6470">{n_fail}</strong><span>Fail</span></div><div class="stat"><strong style="color:#d99b35">{n_missing}</strong><span>Missing / pending photometry</span></div><div class="stat"><strong>{len(EPOCHS)}</strong><span>Total</span></div></div>
<div class="table-wrap"><table><thead><tr><th>Date</th><th>Status</th><th>Cal tables</th><th>Peak (Jy)</th><th>RMS (mJy)</th><th>Dyn range</th><th>DSA/NVSS</th><th>Bright sources</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
<section><h2>Mosaic thumbnails</h2><div class="grid">{"".join(cards)}</div></section></main></body></html>"""


mosaic_router = APIRouter(prefix="/artifacts/mosaic", tags=["mosaic artifacts"])
ops_router = APIRouter(prefix="/api", tags=["read-only operations"])
legacy_router = APIRouter(include_in_schema=False)
control_router = APIRouter(prefix="/api/runs", tags=["pipeline control"])

CONTROL_TOKEN_ENV = "DSA110_CONTROL_TOKEN"


class RunRequestBody(BaseModel):
    """JSON body for launching or previewing a batch_pipeline run."""

    date: str
    cal_date: str | None = None
    start_hour: int | None = None
    end_hour: int | None = None
    rfi_mode: str | None = None
    tile_timeout: int | None = None
    quarantine_after_failures: int | None = None
    photometry_workers: int | None = None
    retry_failed: bool = False
    force_recal: bool = False
    skip_photometry: bool = False
    lenient_qa: bool = False
    clear_quarantine: bool = False
    dry_run: bool = False


def _require_control_token(request: Request) -> None:
    expected = os.environ.get(CONTROL_TOKEN_ENV, "")
    provided = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="control token missing or invalid")


def _audit(
    config: pipeline_control.ControlConfig, action: str, request: Request, payload: dict
) -> None:
    config.control_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "remote": request.client.host if request.client else None,
        "payload": payload,
    }
    with (config.control_dir / "audit.jsonl").open("a") as stream:
        stream.write(json.dumps(entry) + "\n")


@control_router.get("")
def control_list_runs():
    """List the newest launcher-owned pipeline runs (read-only)."""
    return {"runs": pipeline_control.list_runs(pipeline_control.ControlConfig())}


@control_router.get("/{run_id}")
def control_get_run(run_id: str, tail: int = 40):
    """Return one run's registry record plus a bounded log tail (read-only)."""
    config = pipeline_control.ControlConfig()
    try:
        record = pipeline_control.get_run(run_id, config)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown run") from None
    record["log_tail"] = _tail(Path(record["log_path"]), max(1, min(tail, 500)))
    return record


@control_router.post("")
def control_launch(body: RunRequestBody, request: Request):
    """Launch batch_pipeline.py (or preview with dry_run) — token required."""
    _require_control_token(request)
    config = pipeline_control.ControlConfig()
    try:
        run_request = pipeline_control.RunRequest(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _audit(config, "dry_run" if body.dry_run else "launch", request, body.model_dump())
    if body.dry_run:
        try:
            plan = pipeline_control.run_dry_run(run_request, config)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="dry-run timed out") from None
        return {"plan": plan}
    try:
        return pipeline_control.launch_run(run_request, config)
    except pipeline_control.RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@control_router.post("/{run_id}/terminate")
def control_terminate(run_id: str, request: Request):
    """Terminate a running pipeline run's whole process group — token required."""
    _require_control_token(request)
    config = pipeline_control.ControlConfig()
    _audit(config, "terminate", request, {"run_id": run_id})
    try:
        return pipeline_control.terminate_run(run_id, config)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown run") from None
    except pipeline_control.RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@mosaic_router.get("/{date}/{epoch}/status")
def mosaic_status(date: str, epoch: str, request: Request):
    """Return the data used by the mosaic dashboard card."""
    _validate_date_epoch(date, epoch)
    return get_metrics(_config(request), date, epoch)


@mosaic_router.get("/{date}/{epoch}/thumb.png")
def mosaic_thumbnail(date: str, epoch: str, request: Request):
    """Return a cached or newly rendered mosaic PNG."""
    _validate_date_epoch(date, epoch)
    path = make_thumbnail(_config(request), date, epoch)
    if not path:
        return Response(status_code=404)
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "max-age=60"},
    )


@legacy_router.get("/thumb/{date}/{epoch}.png")
def legacy_thumbnail(date: str, epoch: str, request: Request):
    """Preserve the original thumbnail URL."""
    return mosaic_thumbnail(date, epoch, request)


@ops_router.get("/status")
def operations_status(request: Request, date: str | None = None, hour: int | None = None):
    """Return the read-only campaign and telescope operations snapshot."""
    config = _config(request)
    return campaign_status(
        config,
        date or config.campaign_date,
        config.campaign_hour if hour is None else hour,
    )


def create_app(config: DashboardConfig | None = None) -> FastAPI:
    """Create a routed dashboard application."""
    dashboard_config = config or DashboardConfig()
    dashboard_config.thumb_dir.mkdir(parents=True, exist_ok=True)
    application = FastAPI(title="DSA-110 Continuum Observatory", version="2.0")
    application.state.dashboard_config = dashboard_config
    application.include_router(mosaic_router)
    application.include_router(ops_router)
    application.include_router(control_router)
    application.include_router(legacy_router)

    @application.get("/", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(render_dashboard(dashboard_config))

    @application.get("/health")
    def health():
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8767, log_level="warning")
