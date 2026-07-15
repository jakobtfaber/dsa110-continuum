"""Shared discovery, validation, and caching for per-artifact dashboard views."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
CALTABLE_NAME_RE = re.compile(rf"({TIMESTAMP})_0~23\.(b|g|k)")
MS_NAME_RE = re.compile(rf"({TIMESTAMP})(_meridian)?\.ms")
TILE_TS_RE = re.compile(TIMESTAMP)

TILE_PRODUCT_SUFFIXES = ("image-pb", "image", "residual-pb", "residual", "psf", "dirty", "model")


class ArtifactNotFound(Exception):
    """Requested artifact name is malformed or absent from stage."""


class ArtifactRenderError(Exception):
    """A summary or plot renderer failed for a stated, user-displayable reason."""


def file_record(path: Path | None) -> dict | None:
    """Return path/size/mtime for an existing path, mirroring the qa_server shape."""
    if path is None or not path.exists():
        return None
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_caltable(ms_dir: Path, name: str) -> Path:
    """Validate a caltable name against the strict allowlist and return its path."""
    if not CALTABLE_NAME_RE.fullmatch(name):
        raise ArtifactNotFound(f"invalid caltable name: {name!r}")
    path = ms_dir / name
    if not _contained(ms_dir, path) or not path.is_dir():
        raise ArtifactNotFound(f"no such caltable: {name!r}")
    return path


def resolve_ms(ms_dir: Path, name: str) -> Path:
    """Validate an MS name against the strict allowlist and return its path."""
    if not MS_NAME_RE.fullmatch(name):
        raise ArtifactNotFound(f"invalid MS name: {name!r}")
    path = ms_dir / name
    if not _contained(ms_dir, path) or not path.is_dir():
        raise ArtifactNotFound(f"no such MS: {name!r}")
    return path


def tile_products(images_dir: Path, ts: str) -> dict[str, Path | None]:
    """Map product suffix -> existing path (or None) for one tile timestamp."""
    if not TILE_TS_RE.fullmatch(ts):
        raise ArtifactNotFound(f"invalid tile timestamp: {ts!r}")
    tile_dir = images_dir / f"mosaic_{ts[:10]}"
    if not _contained(images_dir, tile_dir):
        raise ArtifactNotFound(f"invalid tile timestamp: {ts!r}")
    found = {
        suffix: (path if (path := tile_dir / f"{ts}-{suffix}.fits").is_file() else None)
        for suffix in TILE_PRODUCT_SUFFIXES
    }
    if not any(found.values()):
        raise ArtifactNotFound(f"no tile products for {ts!r}")
    return found


def list_caltables(ms_dir: Path, limit: int = 40) -> list[dict]:
    """List canonical caltables on stage, newest first."""
    if not ms_dir.is_dir():
        return []
    found = [
        (path.stat().st_mtime, path)
        for path in ms_dir.iterdir()
        if CALTABLE_NAME_RE.fullmatch(path.name) and path.is_dir()
    ]
    found.sort(reverse=True)
    return [dict(file_record(path), name=path.name) for _, path in found[:limit]]


def list_ms(ms_dir: Path, limit: int = 48) -> list[dict]:
    """List Measurement Sets on stage, newest first."""
    if not ms_dir.is_dir():
        return []
    found = [
        (path.stat().st_mtime, path)
        for path in ms_dir.iterdir()
        if MS_NAME_RE.fullmatch(path.name) and path.is_dir()
    ]
    found.sort(reverse=True)
    return [dict(file_record(path), name=path.name) for _, path in found[:limit]]


def list_tiles(images_dir: Path, limit: int = 48) -> list[dict]:
    """List single-tile FITS timestamps on stage, newest first (pb/plain deduped)."""
    if not images_dir.is_dir():
        return []
    by_ts: dict[str, Path] = {}
    for path in images_dir.glob("mosaic_*/*-image*.fits"):
        ts = path.name.split("-image")[0]
        if TILE_TS_RE.fullmatch(ts):
            by_ts.setdefault(ts, path)
    records = sorted(by_ts.items(), key=lambda item: item[1].stat().st_mtime, reverse=True)
    return [dict(file_record(path), name=ts) for ts, path in records[:limit]]


def related_artifacts(stage: Path, ts: str) -> dict:
    """Cross-links between the artifacts sharing one observation timestamp."""
    if not TILE_TS_RE.fullmatch(ts):
        raise ArtifactNotFound(f"invalid timestamp: {ts!r}")
    ms_dir = stage / "ms"
    date, hour = ts[:10], ts[11:13]
    tile_dir = stage / "images" / f"mosaic_{date}"

    def _existing(name: str) -> str | None:
        return name if (ms_dir / name).exists() else None

    return {
        "ms": _existing(f"{ts}.ms"),
        "ms_meridian": _existing(f"{ts}_meridian.ms"),
        "caltables": [
            name
            for name in (f"{ts}_0~23.b", f"{ts}_0~23.g", f"{ts}_0~23.k")
            if (ms_dir / name).exists()
        ],
        "tile": ts if any(tile_dir.glob(f"{ts}-image*.fits")) else None,
        "date": date,
        "epoch_token": f"T{hour}00",
        "mosaic_exists": (tile_dir / f"{date}T{hour}00_mosaic.fits").is_file(),
    }


def cached_artifact_file(
    cache_dir: Path,
    category: str,
    name: str,
    kind: str,
    source_mtime: float,
    suffix: str,
    builder: Callable[[Path], None],
) -> Path:
    """Build-once file cache keyed on the source artifact's mtime."""
    key = hashlib.md5(f"{category}{name}{kind}{source_mtime}".encode()).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{category}_{name}_{kind}")
    target = cache_dir / f"{safe}_{key}{suffix}"
    if target.exists():
        return target
    cache_dir.mkdir(parents=True, exist_ok=True)
    for stale in cache_dir.glob(f"{safe}_*{suffix}"):
        stale.unlink(missing_ok=True)
    tmp = target.with_name(f"{target.stem}.tmp{target.suffix}")
    builder(tmp)
    if not tmp.exists():
        raise ArtifactRenderError(f"renderer produced no output for {kind!r}")
    tmp.replace(target)
    return target
