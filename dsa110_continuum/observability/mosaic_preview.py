"""Mosaic thumbnail URLs and same-origin Dagster static sync (read-only)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dsa110_continuum.observability.hour_state import HourStateConfig

# Durable copy under the observability output tree; start_dagster.sh also rsyncs here.
DEFAULT_STATIC_ROOT = Path(
    os.environ.get(
        "DSA110_DAGSTER_STATIC",
        "/data/dsa110-continuum/outputs/observability-dashboard-2026-07-14/dagster-static",
    )
)


def epoch_label(hour: int) -> str:
    """Return qa_server epoch token for an observing hour (e.g. 11 → T1100)."""
    return f"T{hour:02d}00"


def qa_base_url() -> str:
    """Public base URL for qa_server thumbnails (localhost until Cloudflare QA is live)."""
    return os.environ.get("DSA110_QA_BASE_URL", "http://127.0.0.1:8767").rstrip("/")


def dagster_public_url() -> str:
    """Browser-facing Dagster origin for same-origin preview links."""
    host = os.environ.get("DSA110_DAGSTER_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = os.environ.get("DSA110_DAGSTER_PORT", "3212")
    return os.environ.get("DSA110_DAGSTER_PUBLIC_URL", f"http://{host}:{port}").rstrip("/")


def qa_thumb_url(date: str, hour: int, base: str | None = None) -> str:
    """Return the qa_server PNG thumbnail URL for one hourly-epoch mosaic."""
    root = (base or qa_base_url()).rstrip("/")
    return f"{root}/artifacts/mosaic/{date}/{epoch_label(hour)}/thumb.png"


def dagster_static_thumb_path(date: str, hour: int) -> str:
    """Relative web path served from the Dagster webapp build."""
    return f"/dsa110/{date}_{epoch_label(hour)}_mosaic_thumb.png"


def dagster_static_page_path(date: str, hour: int) -> str:
    """Relative web path for the lightweight mosaic preview HTML page."""
    return f"/dsa110/{date}_{epoch_label(hour)}_mosaic.html"


def resolve_thumb_source(config: HourStateConfig) -> Path | None:
    """Pick an on-disk PNG for the selected hour without calling science code."""
    epoch = epoch_label(config.hour)
    mosaic_stem = f"{config.date}T{config.hour:02d}00_mosaic"
    candidates = [
        config.campaign_outputs / "previews" / f"{mosaic_stem}_qa_thumb.png",
        Path("/data/dsa110-continuum/outputs/mosaic-thumb-2026-07-13") / f"{epoch}_thumb.png",
        config.stage / f"images/mosaic_{config.date}" / f"{mosaic_stem}_qa_diag.png",
    ]
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    thumb_dir = Path(os.environ.get("DSA110_QA_THUMBS", "/tmp/qa_thumbs"))
    if thumb_dir.is_dir():
        matches = sorted(
            thumb_dir.glob(f"{config.date}_{epoch}_*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for match in matches:
            if match.is_file() and match.stat().st_size > 0:
                return match
    return None


def webapp_build_dir() -> Path | None:
    """Locate the active dagster_webserver webapp/build directory."""
    try:
        import dagster_webserver
    except ImportError:
        return None
    build = Path(dagster_webserver.__file__).resolve().parent / "webapp" / "build"
    return build if build.is_dir() else None


def _write_preview_html(
    dest: Path,
    *,
    date: str,
    hour: int,
    thumb_href: str,
    fits_path: str | None,
    qa_url: str,
) -> None:
    epoch = epoch_label(hour)
    fits_line = (
        f"<p>FITS: <code>{fits_path}</code></p>" if fits_path else "<p>FITS: not visible</p>"
    )
    dest.write_text(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{date} {epoch} mosaic — Dagster preview</title>
  <style>
    body {{ margin: 0; background: #111; color: #eee; font-family: system-ui, sans-serif; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #333; }}
    h1 {{ font-size: 16px; font-weight: 600; margin: 0 0 6px; }}
    a {{ color: #79b8ff; }}
    img {{ max-width: 100%; height: auto; display: block; margin: 16px auto; }}
    main {{ padding: 0 16px 24px; }}
    code {{ font-size: 12px; word-break: break-all; }}
  </style>
</head>
<body>
  <header>
    <h1>Hourly-epoch mosaic preview — {date} {epoch}</h1>
    <p>
      <a href="/">Dagster home</a> ·
      <a href="/asset-groups/hour_11_observability">hour_11_observability</a> ·
      <a href="/assets/measurement_sets_tiles_mosaic">measurement_sets_tiles_mosaic</a> ·
      <a href="./hour11_mosaic_cutout.html">Cutout benchmark</a> ·
      <a href="{qa_url}">QA thumb (qa_server)</a>
    </p>
  </header>
  <main>
    {fits_line}
    <img src="{thumb_href}" alt="{date} {epoch} mosaic thumbnail"/>
    <p>Thumbnail source remains qa_server / on-disk PNG caches; this page is a Dagster-static mirror.</p>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def sync_mosaic_thumb(
    config: HourStateConfig,
    *,
    fits_path: str | None = None,
    static_root: Path | None = None,
) -> dict:
    """Copy the hour mosaic PNG into Dagster-static paths and return URL metadata."""
    date = config.date
    hour = config.hour
    epoch = epoch_label(hour)
    qa_url = qa_thumb_url(date, hour)
    rel_thumb = dagster_static_thumb_path(date, hour)
    rel_page = dagster_static_page_path(date, hour)
    public = dagster_public_url()
    result = {
        "epoch": epoch,
        "qa_thumb_url": qa_url,
        "dagster_thumb_url": f"{public}{rel_thumb}",
        "dagster_page_url": f"{public}{rel_page}",
        "dagster_thumb_path": rel_thumb,
        "dagster_page_path": rel_page,
        "source_path": None,
        "synced": False,
    }
    source = resolve_thumb_source(config)
    if source is None:
        return result
    result["source_path"] = str(source)

    roots: list[Path] = []
    durable = static_root or DEFAULT_STATIC_ROOT
    roots.append(durable / "dsa110")
    build = webapp_build_dir()
    if build is not None:
        roots.append(build / "dsa110")

    thumb_name = Path(rel_thumb).name
    page_name = Path(rel_page).name
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        dest_thumb = root / thumb_name
        shutil.copy2(source, dest_thumb)
        _write_preview_html(
            root / page_name,
            date=date,
            hour=hour,
            thumb_href=f"./{thumb_name}",
            fits_path=fits_path,
            qa_url=qa_url,
        )
        # Stable aliases for the default hour-11 campaign.
        if date == "2026-07-13" and hour == 11:
            shutil.copy2(dest_thumb, root / "hour11_mosaic_thumb.png")
            _write_preview_html(
                root / "hour11_mosaic.html",
                date=date,
                hour=hour,
                thumb_href="./hour11_mosaic_thumb.png",
                fits_path=fits_path,
                qa_url=qa_url,
            )
    result["synced"] = True
    return result


def mosaic_preview_markdown(preview: dict, *, mosaic_path: str | None) -> str:
    """Markdown block shown in Dagster asset metadata (inline thumb when synced)."""
    lines = [
        f"### Hourly-epoch mosaic — `{preview['epoch']}`",
        "",
    ]
    if mosaic_path:
        lines.append(f"- FITS: `{mosaic_path}`")
    lines.append(f"- [QA thumbnail (qa_server)]({preview['qa_thumb_url']})")
    lines.append(f"- [Dagster preview page]({preview['dagster_page_url']})")
    if preview.get("synced"):
        lines.extend(
            [
                "",
                f"![mosaic thumbnail]({preview['dagster_thumb_url']})",
                "",
                "_Same-origin Dagster-static PNG. Remote browsers: use qa_server / Tailscale; "
                "`dsa110-continuum.jakobtfaber.com` is not assumed live._",
            ]
        )
    elif preview.get("qa_thumb_url"):
        lines.extend(
            [
                "",
                f"![mosaic thumbnail]({preview['qa_thumb_url']})",
                "",
                "_Inline image from qa_server (`DSA110_QA_BASE_URL`). Requires :8767 reachable "
                "from the browser viewing Dagster._",
            ]
        )
    else:
        lines.append("")
        lines.append("_No mosaic thumbnail source found on disk._")
    return "\n".join(lines)
