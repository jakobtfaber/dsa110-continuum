"""Per-artifact QA detail pages: caltable (#56), tile (#55), MS (#54)."""

from __future__ import annotations

import html
import json
from pathlib import Path

from dsa110_continuum.observability import artifacts, caltable_qa
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
.muted{color:#87919d}</style>"""


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
<a href="/artifacts/caltable/">caltables</a></p>{body}</main></body></html>"""
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
    cal_type = caltable_qa.caltable_type(name)
    return _page(
        f"Caltable {name}",
        f"""
<h1>Calibration table · {html.escape(name)} {_badge("info", cal_type)}</h1>
<p class="muted">{html.escape(record["path"])} · {record["size_bytes"]:,} bytes ·
modified {html.escape(record["modified"][:19])} ·
<a href="/artifacts/caltable/{html.escape(name)}/status">JSON</a></p>
<p>{_related_row(related)}</p>{summary_note}
<h2>Provenance</h2>{prov_html}
<h2>Quality metrics</h2><table><tbody>{quality_rows}</tbody></table>
<h2>Per-SPW flagging</h2>
<table><thead><tr><th>SPW</th><th>Flagged</th><th></th></tr></thead>
<tbody>{spw_rows}</tbody></table>
<h2>Diagnostics</h2>{_plot_grid(f"/artifacts/caltable/{html.escape(name)}", caltable_qa.plot_kinds(name))}""",
    )
