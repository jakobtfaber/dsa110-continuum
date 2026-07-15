"""Per-artifact QA detail pages: caltable (#56), tile (#55), MS (#54)."""

from __future__ import annotations

import html
import json
from pathlib import Path

from dsa110_continuum.observability import artifacts, caltable_qa, job_state, ms_qa, tile_qa
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

_STYLE = """<style>
body{background:#0d1014;color:#e8edf2;font-family:Inter,-apple-system,sans-serif;margin:0}
.shell{max-width:1250px;margin:auto;padding:22px} a{color:#4eb8ff}
h2{font-size:1rem;text-transform:uppercase;letter-spacing:.1em;color:#bcc6d0}
table{border-collapse:collapse;font-size:.83rem;margin:12px 0}
td,th{padding:8px 12px;border-bottom:1px solid #2a3038;text-align:left}
th{background:#171b20;color:#9da8b4}
.badge{display:inline-block;background:var(--badge);color:#081014;padding:3px 8px;
border-radius:999px;font-size:.68rem;font-weight:800}
.plot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}
.plot-grid figure{margin:0;background:#171b20;border:1px solid #2a3038;border-radius:8px;
padding:10px}
.plot-grid img{width:100%;background:#090b0e;border-radius:4px;min-height:80px}
.plot-grid figcaption{font-size:.75rem;color:#87919d;margin-top:6px}
.muted{color:#87919d}
pre{background:#090b0e;color:#b9c6d0;border-radius:6px;padding:12px;max-height:260px;
overflow:auto;font-size:.72rem;line-height:1.45;white-space:pre-wrap}
.process-list{list-style:none;padding:0;margin:0}
.process-list li{display:flex;gap:10px;padding:6px 0;border-top:1px solid #2a3038;
font-size:.76rem} .process-list li:first-child{border:0}
.process-list span{word-break:break-all;color:#bdc6cf}</style>"""


def _badge(state: str, label: str) -> str:
    colors = {"pass": "#41c97a", "warn": "#d99b35", "fail": "#ff6470", "info": "#4eb8ff"}
    return (
        f'<span class="badge" style="--badge:{colors.get(state, "#69717d")}">'
        f"{html.escape(label.upper())}</span>"
    )


def _config(request: Request):
    return request.app.state.dashboard_config


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>{_STYLE}</head>
<body><main class="shell"><p><a href="/">← Dashboard</a> ·
<a href="/artifacts/caltable/">caltables</a> ·
<a href="/artifacts/tile/">tiles</a> ·
<a href="/artifacts/ms/">measurement sets</a></p>{body}</main></body></html>"""
    )


def _plot_grid(base_url: str, kinds: tuple[str, ...]) -> str:
    figures = "".join(
        f'<figure><img src="{base_url}/plot/{kind}.png" loading="lazy" '
        f'alt="{html.escape(kind)}"><figcaption>{html.escape(kind)}</figcaption></figure>'
        for kind in kinds
    )
    return f'<div class="plot-grid">{figures}</div>'


def _related_row(related: dict) -> str:
    links = []
    for name in filter(None, (related.get("ms"), related.get("ms_meridian"))):
        links.append(f'<a href="/artifacts/ms/{html.escape(name)}">{html.escape(name)}</a>')
    for name in related.get("caltables", []):
        links.append(f'<a href="/artifacts/caltable/{html.escape(name)}">{html.escape(name)}</a>')
    if related.get("tile"):
        links.append(f'<a href="/artifacts/tile/{related["tile"]}">tile {related["tile"]}</a>')
    if related.get("mosaic_exists"):
        links.append(
            f'<a href="/artifacts/mosaic/{related["date"]}/{related["epoch_token"]}/status">'
            f"hourly-epoch mosaic {related['date']}{related['epoch_token']}</a>"
        )
    links.append(f'<a href="/runs/{related["date"]}">run {related["date"]}</a>')
    return " · ".join(links)


def _job_card(job: dict) -> str:
    """Render the in-flight job section for one artifact (#57)."""
    if not job.get("active"):
        return '<h2>In-flight job</h2><p class="muted">No active job touching this artifact</p>'
    rows = []
    for proc in job.get("processes", []):
        rows.append(
            f"<li><code>PID {proc['pid']}</code> <span>{html.escape(proc['command'])}</span></li>"
        )
    for proc in job.get("batch", []):
        rows.append(
            f"<li><code>PID {proc['pid']}</code> "
            f"<span>date-level batch: {html.escape(proc['command'])}</span></li>"
        )
    process_html = f'<ul class="process-list">{"".join(rows)}</ul>' if rows else ""
    runs_html = ""
    for run in job.get("runs", []):
        run_id = html.escape(str(run.get("run_id")))
        tail = html.escape("\n".join(run.get("log_lines") or []) or "no matching log lines yet")
        runs_html += (
            f'<p class="muted">run <a href="/control/runs/{run_id}">{run_id}</a>'
            f" · PID {run.get('pid')} · {html.escape(str(run.get('log_path')))}</p>"
            f"<pre>{tail}</pre>"
        )
    return f"<h2>In-flight job</h2>{process_html}{runs_html}"


def _cached_summary(config, category: str, name: str, source: Path, builder) -> dict:
    cached = artifacts.cached_artifact_file(
        Path(config.thumb_dir),
        category,
        name,
        "summary",
        source.stat().st_mtime,
        ".json",
        lambda tmp: tmp.write_text(json.dumps(builder(), default=str)),
    )
    return json.loads(cached.read_text())


# ---------------------------------------------------------------- caltable (#56)

caltable_router = APIRouter(prefix="/artifacts/caltable", tags=["caltable artifacts"])


def _resolve_caltable_or_404(config, name: str) -> Path:
    try:
        return artifacts.resolve_caltable(Path(config.stage) / "ms", name)
    except artifacts.ArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@caltable_router.get("/", response_class=HTMLResponse)
def caltable_index(request: Request):
    """List the newest calibration tables on stage."""
    config = _config(request)
    records = artifacts.list_caltables(Path(config.stage) / "ms")
    rows = (
        "".join(
            f'<tr><td><a href="/artifacts/caltable/{html.escape(record["name"])}">'
            f"{html.escape(record['name'])}</a></td>"
            f"<td>{caltable_qa.caltable_type(record['name'])}</td>"
            f"<td>{html.escape(record['modified'][:19])}</td></tr>"
            for record in records
        )
        or '<tr><td colspan="3" class="muted">No calibration tables on stage</td></tr>'
    )
    return _page(
        "Calibration tables",
        f"""<h1>Calibration tables</h1>
<table><thead><tr><th>Table</th><th>Type</th><th>Modified (UTC)</th></tr></thead>
<tbody>{rows}</tbody></table>""",
    )


@caltable_router.get("/{name}/status")
def caltable_status(name: str, request: Request):
    """Machine-readable summary for one calibration table."""
    config = _config(request)
    path = _resolve_caltable_or_404(config, name)
    try:
        summary = _cached_summary(config, "caltable", name, path, lambda: caltable_qa.summary(path))
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from None
    return {
        "file": artifacts.file_record(path),
        "summary": summary,
        "related": artifacts.related_artifacts(Path(config.stage), name[:19]),
        "plot_kinds": list(caltable_qa.plot_kinds(name)),
        "job": job_state.jobs_for_timestamp(name[:19]),
    }


@caltable_router.get("/{name}/plot/{kind}.png")
def caltable_plot(name: str, kind: str, request: Request):
    """Lazily render (and cache) one diagnostic plot for a calibration table."""
    config = _config(request)
    path = _resolve_caltable_or_404(config, name)
    if kind not in caltable_qa.plot_kinds(name):
        raise HTTPException(status_code=404, detail=f"unknown plot kind {kind!r}")
    try:
        png = artifacts.cached_artifact_file(
            Path(config.thumb_dir),
            "caltable",
            name,
            kind,
            path.stat().st_mtime,
            ".png",
            lambda tmp: caltable_qa.render_plot(path, kind, tmp),
        )
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from None
    return Response(
        content=png.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "max-age=300"},
    )


@caltable_router.get("/{name}", response_class=HTMLResponse)
def caltable_page(name: str, request: Request):
    """Human-readable per-caltable QA page."""
    config = _config(request)
    path = _resolve_caltable_or_404(config, name)
    record = artifacts.file_record(path)
    try:
        summary = _cached_summary(config, "caltable", name, path, lambda: caltable_qa.summary(path))
        summary_note = ""
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        summary = {"quality": {}, "per_spw": [], "snr_summary": None, "provenance": None}
        summary_note = f'<p class="muted">metrics unavailable: {html.escape(str(exc))}</p>'
    provenance = summary.get("provenance")
    if provenance:
        prov_rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in provenance.items()
        )
        prov_html = f"<table><tbody>{prov_rows}</tbody></table>"
    else:
        prov_html = (
            '<p class="muted">No provenance sidecar '
            "(pre-provenance table or borrowed original missing)</p>"
        )
    quality = summary.get("quality") or {}
    quality_rows = (
        "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in quality.items()
        )
        or '<tr><td colspan="2" class="muted">—</td></tr>'
    )
    spw_rows = (
        "".join(
            f"<tr><td>{stat['spw_id']}</td><td>{stat['fraction_flagged']:.3f}</td>"
            f"<td>{_badge('fail' if stat['is_problematic'] else 'pass', 'problem' if stat['is_problematic'] else 'ok')}</td></tr>"
            for stat in summary.get("per_spw", [])
        )
        or '<tr><td colspan="3" class="muted">—</td></tr>'
    )
    related = artifacts.related_artifacts(Path(config.stage), name[:19])
    job = job_state.jobs_for_timestamp(name[:19])
    cal_type = caltable_qa.caltable_type(name)
    return _page(
        f"Caltable {name}",
        f"""
<h1>Calibration table · {html.escape(name)} {_badge("info", cal_type)}</h1>
<p class="muted">{html.escape(record["path"])} · {record["size_bytes"]:,} bytes ·
modified {html.escape(record["modified"][:19])} ·
<a href="/artifacts/caltable/{html.escape(name)}/status">JSON</a></p>
<p>{_related_row(related)}</p>{summary_note}
{_job_card(job)}
<h2>Provenance</h2>{prov_html}
<h2>Quality metrics</h2><table><tbody>{quality_rows}</tbody></table>
<h2>Per-SPW flagging</h2>
<table><thead><tr><th>SPW</th><th>Flagged</th><th></th></tr></thead>
<tbody>{spw_rows}</tbody></table>
<h2>Diagnostics</h2>{_plot_grid(f"/artifacts/caltable/{html.escape(name)}", caltable_qa.plot_kinds(name))}""",
    )


# ---------------------------------------------------------------- tile (#55)

tile_router = APIRouter(prefix="/artifacts/tile", tags=["tile artifacts"])


def _resolve_tile_or_404(config, ts: str) -> dict:
    try:
        return artifacts.tile_products(Path(config.stage) / "images", ts)
    except artifacts.ArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


def _tile_ms_path(config, ts: str) -> Path | None:
    related = artifacts.related_artifacts(Path(config.stage), ts)
    name = related.get("ms_meridian") or related.get("ms")
    return (Path(config.stage) / "ms" / name) if name else None


@tile_router.get("/", response_class=HTMLResponse)
def tile_index(request: Request):
    """List the newest single-tile FITS products on stage."""
    config = _config(request)
    records = artifacts.list_tiles(Path(config.stage) / "images")
    rows = (
        "".join(
            f'<tr><td><a href="/artifacts/tile/{record["name"]}">{record["name"]}</a></td>'
            f"<td>{html.escape(record['modified'][:19])}</td></tr>"
            for record in records
        )
        or '<tr><td colspan="2" class="muted">No tiles on stage</td></tr>'
    )
    return _page(
        "Tiles",
        f"""<h1>Single-tile FITS</h1>
<table><thead><tr><th>Tile</th><th>Modified (UTC)</th></tr></thead>
<tbody>{rows}</tbody></table>""",
    )


@tile_router.get("/{name}/status")
def tile_status(name: str, request: Request):
    """Machine-readable summary for one tile."""
    config = _config(request)
    products = _resolve_tile_or_404(config, name)
    source = next(path for path in products.values() if path is not None)
    ms_path = _tile_ms_path(config, name)
    try:
        summary = _cached_summary(
            config, "tile", name, source, lambda: tile_qa.summary(products, ms_path)
        )
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from None
    return {
        "products": {key: artifacts.file_record(path) for key, path in products.items()},
        "summary": summary,
        "related": artifacts.related_artifacts(Path(config.stage), name),
        "plot_kinds": list(tile_qa.plot_kinds(products, ms_path is not None)),
        "job": job_state.jobs_for_timestamp(name),
    }


@tile_router.get("/{name}/plot/{kind}.png")
def tile_plot(name: str, kind: str, request: Request):
    """Lazily render (and cache) one tile diagnostic plot."""
    config = _config(request)
    products = _resolve_tile_or_404(config, name)
    ms_path = _tile_ms_path(config, name)
    if kind not in tile_qa.plot_kinds(products, ms_path is not None):
        raise HTTPException(status_code=404, detail=f"unknown plot kind {kind!r}")
    source = next(path for path in products.values() if path is not None)
    try:
        png = artifacts.cached_artifact_file(
            Path(config.thumb_dir),
            "tile",
            name,
            kind,
            source.stat().st_mtime,
            ".png",
            lambda tmp: tile_qa.render_plot(products, kind, tmp, ms_path=ms_path),
        )
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from None
    return Response(
        content=png.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "max-age=300"},
    )


@tile_router.get("/{name}", response_class=HTMLResponse)
def tile_page(name: str, request: Request):
    """Human-readable per-tile QA page."""
    config = _config(request)
    products = _resolve_tile_or_404(config, name)
    source = next(path for path in products.values() if path is not None)
    ms_path = _tile_ms_path(config, name)
    try:
        summary = _cached_summary(
            config, "tile", name, source, lambda: tile_qa.summary(products, ms_path)
        )
        note = ""
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        summary = {"gate": None, "residual": None, "psf_correlation": None}
        note = f'<p class="muted">metrics unavailable: {html.escape(str(exc))}</p>'
    gate = summary.get("gate") or {}
    overall = str(gate.get("overall", "—"))
    gate_badge = _badge({"PASS": "pass", "WARN": "warn"}.get(overall, "fail"), overall)
    gate_rows = (
        "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in gate.items()
        )
        or '<tr><td colspan="2" class="muted">—</td></tr>'
    )
    product_rows = "".join(
        f"<tr><th>{key}</th><td>{html.escape(path.name) if path else '—'}</td></tr>"
        for key, path in products.items()
    )
    related = artifacts.related_artifacts(Path(config.stage), name)
    job = job_state.jobs_for_timestamp(name)
    return _page(
        f"Tile {name}",
        f"""
<h1>Single-tile FITS · {name} {gate_badge}</h1>
<p class="muted"><a href="/artifacts/tile/{name}/status">JSON</a></p>
<p>{_related_row(related)}</p>{note}
{_job_card(job)}
<h2>QA gate</h2><table><tbody>{gate_rows}</tbody></table>
<h2>Products</h2><table><tbody>{product_rows}</tbody></table>
<h2>Diagnostics</h2>{_plot_grid(f"/artifacts/tile/{name}", tile_qa.plot_kinds(products, ms_path is not None))}""",
    )


# ---------------------------------------------------------------- ms (#54)

ms_router = APIRouter(prefix="/artifacts/ms", tags=["ms artifacts"])


def _resolve_ms_or_404(config, name: str) -> Path:
    try:
        return artifacts.resolve_ms(Path(config.stage) / "ms", name)
    except artifacts.ArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@ms_router.get("/", response_class=HTMLResponse)
def ms_index(request: Request):
    """List the newest Measurement Sets on stage."""
    config = _config(request)
    records = artifacts.list_ms(Path(config.stage) / "ms")
    rows = (
        "".join(
            f'<tr><td><a href="/artifacts/ms/{html.escape(record["name"])}">'
            f"{html.escape(record['name'])}</a></td>"
            f"<td>{html.escape(record['modified'][:19])}</td></tr>"
            for record in records
        )
        or '<tr><td colspan="2" class="muted">No Measurement Sets on stage</td></tr>'
    )
    return _page(
        "Measurement Sets",
        f"""<h1>Measurement Sets</h1>
<table><thead><tr><th>MS</th><th>Modified (UTC)</th></tr></thead>
<tbody>{rows}</tbody></table>""",
    )


@ms_router.get("/{name}/status")
def ms_status(name: str, request: Request):
    """Machine-readable summary for one Measurement Set."""
    config = _config(request)
    path = _resolve_ms_or_404(config, name)
    try:
        summary = _cached_summary(config, "ms", name, path, lambda: ms_qa.summary(path))
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from None
    return {
        "file": artifacts.file_record(path),
        "summary": summary,
        "related": artifacts.related_artifacts(Path(config.stage), name[:19]),
        "plot_kinds": list(ms_qa.plot_kinds(path)),
        "job": job_state.jobs_for_timestamp(name[:19]),
    }


@ms_router.get("/{name}/plot/{kind}.png")
def ms_plot(name: str, kind: str, request: Request):
    """Lazily render (and cache) one MS diagnostic plot."""
    config = _config(request)
    path = _resolve_ms_or_404(config, name)
    if kind not in ms_qa.plot_kinds(path):
        raise HTTPException(status_code=404, detail=f"unknown plot kind {kind!r}")
    try:
        png = artifacts.cached_artifact_file(
            Path(config.thumb_dir),
            "ms",
            name,
            kind,
            path.stat().st_mtime,
            ".png",
            lambda tmp: ms_qa.render_plot(path, kind, tmp),
        )
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from None
    return Response(
        content=png.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "max-age=300"},
    )


@ms_router.get("/{name}", response_class=HTMLResponse)
def ms_page(name: str, request: Request):
    """Human-readable per-MS QA page with lifecycle state."""
    config = _config(request)
    path = _resolve_ms_or_404(config, name)
    record = artifacts.file_record(path)
    try:
        summary = _cached_summary(config, "ms", name, path, lambda: ms_qa.summary(path))
        note = ""
    except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
        summary = {}
        note = f'<p class="muted">metrics unavailable: {html.escape(str(exc))}</p>'
    related = artifacts.related_artifacts(Path(config.stage), name[:19])
    job = job_state.jobs_for_timestamp(name[:19])
    lifecycle = [
        ("Calibration tables", bool(related["caltables"])),
        ("Tile image", related["tile"] is not None),
        ("Hourly-epoch mosaic", related["mosaic_exists"]),
    ]
    lifecycle_rows = "".join(
        f"<tr><th>{stage_name}</th>"
        f"<td>{_badge('pass' if done else 'warn', 'ready' if done else 'not yet')}</td></tr>"
        for stage_name, done in lifecycle
    )
    summary_rows = (
        "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in summary.items()
        )
        or '<tr><td colspan="2" class="muted">—</td></tr>'
    )
    return _page(
        f"MS {name}",
        f"""
<h1>Measurement Set · {html.escape(name)}</h1>
<p class="muted">{html.escape(record["path"])} ·
modified {html.escape(record["modified"][:19])} ·
<a href="/artifacts/ms/{html.escape(name)}/status">JSON</a></p>
<p>{_related_row(related)}</p>{note}
{_job_card(job)}
<h2>Lifecycle</h2><table><tbody>{lifecycle_rows}</tbody></table>
<h2>Summary</h2><table><tbody>{summary_rows}</tbody></table>
<h2>Diagnostics</h2>{_plot_grid(f"/artifacts/ms/{html.escape(name)}", ms_qa.plot_kinds(path))}""",
    )
