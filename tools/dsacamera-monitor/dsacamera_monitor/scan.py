"""Scan /data/incoming-style tree and emit manifest + static site."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dsacamera_monitor.hdf5_pointing import DEC_ROUND_DIGITS, read_pointing_metadata
from dsacamera_monitor.manifest import (
    BeamAgg,
    DayAgg,
    ScanAccum,
    build_manifest,
    try_parse_filename,
)
from dsacamera_monitor.metadata_cache import MetadataCache


@dataclass(frozen=True)
class MetadataCandidate:
    """One enumerated HDF5 file eligible for a metadata cache lookup."""

    path: Path
    timestamp: datetime
    day: date


def _record_dec(accum: ScanAccum, day: date, dec: float) -> None:
    dr = round(dec, DEC_ROUND_DIGITS)
    accum.global_decs_rounded.add(dr)
    if accum.global_dec_min is None:
        accum.global_dec_min = dec
        accum.global_dec_max = dec
    else:
        accum.global_dec_min = min(accum.global_dec_min, dec)
        accum.global_dec_max = max(accum.global_dec_max, dec)
    dagg = accum.by_day[day]
    dagg.decs_rounded.add(dr)
    if dagg.dec_min is None:
        dagg.dec_min = dec
        dagg.dec_max = dec
    else:
        dagg.dec_min = min(dagg.dec_min, dec)
        dagg.dec_max = max(dagg.dec_max, dec)


def _record_metadata(accum: ScanAccum, day: date, meta: dict[str, Any]) -> None:
    if meta["dec_status"] == "ok":
        dec = meta["dec_deg"]
        assert dec is not None
        accum.files_with_dec += 1
        _record_dec(accum, day, dec)
    elif meta["dec_status"] == "missing":
        accum.files_dec_missing += 1
    else:
        accum.files_dec_read_failed += 1
    if meta["pointing_status"] == "read_failed":
        accum.files_pointing_read_failed += 1


def _parse_cache_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cache_row_failed(row: dict[str, Any]) -> bool:
    return row["dec_status"] == "read_failed" or row["pointing_status"] == "read_failed"


def _scan_incremental_metadata(
    accum: ScanAccum,
    candidates: list[MetadataCandidate],
    *,
    cache_path: Path,
    update_limit: int,
    retry_seconds: int,
    pointing_timeseries: bool,
    pointing_timeseries_max_files: int,
    now: datetime,
) -> None:
    """Update and consume a bounded persistent metadata cache."""
    accum.metadata_cache_enabled = True
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    retry_cutoff = now - timedelta(seconds=max(0, retry_seconds))
    candidates_by_name = {candidate.path.name: candidate for candidate in candidates}

    try:
        with MetadataCache(cache_path) as cache:
            rows = cache.load_rows()
            uncached = sorted(
                (candidate for candidate in candidates if candidate.path.name not in rows),
                key=lambda candidate: (candidate.timestamp, candidate.path.name),
                reverse=True,
            )
            retryable = sorted(
                (
                    candidate
                    for candidate in candidates
                    if candidate.path.name in rows
                    and _cache_row_failed(rows[candidate.path.name])
                    and (
                        (last_attempt := _parse_cache_time(rows[candidate.path.name]["last_attempt_at"]))
                        is None
                        or last_attempt <= retry_cutoff
                    )
                ),
                key=lambda candidate: (candidate.timestamp, candidate.path.name),
                reverse=True,
            )
            selected = (uncached + retryable)[: max(0, update_limit)]
            accum.metadata_retried = sum(
                candidate.path.name in rows for candidate in selected
            )
            updates = [read_pointing_metadata(candidate.path) for candidate in selected]
            cache.write_attempts(updates, now)
            rows = cache.load_rows()
    except Exception as exc:
        accum.metadata_cache_error = f"{type(exc).__name__}: {exc}"
        accum.metadata_pending = len(candidates)
        return

    current_rows = {
        name: rows[name] for name in candidates_by_name if name in rows
    }
    accum.metadata_cached = len(current_rows)
    accum.metadata_pending = len(candidates) - accum.metadata_cached
    accum.metadata_failed = sum(_cache_row_failed(row) for row in current_rows.values())

    for name, row in current_rows.items():
        _record_metadata(accum, candidates_by_name[name].day, row)

    if pointing_timeseries:
        eligible = [
            (candidates_by_name[name], row)
            for name, row in current_rows.items()
            if not _cache_row_failed(row)
            and any(row.get(key) is not None for key in ("t_mid_utc", "ra_deg", "dec_deg"))
        ]
        newest = sorted(
            eligible,
            key=lambda item: (item[0].timestamp, item[0].path.name),
            reverse=True,
        )[:pointing_timeseries_max_files]
        for candidate, row in reversed(newest):
            accum.timeseries_rows.append(
                {
                    "filename": candidate.path.name,
                    "t_mid_utc": row["t_mid_utc"],
                    "ra_deg": row["ra_deg"],
                    "dec_deg": row["dec_deg"],
                }
            )
        accum.timeseries_truncated = len(eligible) > len(newest)
    accum.metadata_emitted = len(accum.timeseries_rows)


def scan_directory(
    root: Path,
    *,
    no_stat: bool,
    hdf5_metadata: bool = True,
    pointing_timeseries: bool = False,
    pointing_timeseries_max_files: int = 5000,
    metadata_cache_path: Path | None = None,
    metadata_update_limit: int = 100,
    metadata_retry_seconds: int = 3600,
    metadata_now: datetime | None = None,
) -> ScanAccum:
    """Single pass over directory entries; only matching *.hdf5 names are counted."""
    accum = ScanAccum()
    metadata_candidates: list[MetadataCandidate] = []
    with os.scandir(root) as it:
        for entry in it:
            if not entry.is_file():
                continue
            name = entry.name
            if not name.endswith(".hdf5"):
                continue
            parsed = try_parse_filename(name)
            if parsed is None:
                continue
            dt_utc, beam = parsed
            day = dt_utc.date()
            full_path = Path(entry.path)

            if day not in accum.by_day:
                accum.by_day[day] = DayAgg()
            day_agg = accum.by_day[day]
            day_agg.count += 1

            if beam not in accum.by_beam:
                accum.by_beam[beam] = BeamAgg()
            beam_agg = accum.by_beam[beam]
            beam_agg.count += 1

            if accum.latest_filename_dt is None or dt_utc > accum.latest_filename_dt:
                accum.latest_filename_dt = dt_utc
            if accum.earliest_filename_dt is None or dt_utc < accum.earliest_filename_dt:
                accum.earliest_filename_dt = dt_utc

            if not no_stat:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                size = st.st_size
                mtime_dt = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                day_agg.bytes += size
                beam_agg.bytes += size
                accum.total_bytes += size
                if accum.latest_mtime is None or mtime_dt > accum.latest_mtime:
                    accum.latest_mtime = mtime_dt
                if accum.earliest_mtime is None or mtime_dt < accum.earliest_mtime:
                    accum.earliest_mtime = mtime_dt

            accum.file_count += 1

            if hdf5_metadata and metadata_cache_path is None:
                meta = read_pointing_metadata(full_path)
                _record_metadata(accum, day, meta)

                if pointing_timeseries and len(accum.timeseries_rows) < pointing_timeseries_max_files:
                    accum.timeseries_rows.append(
                        {
                            "filename": meta["filename"],
                            "t_mid_utc": meta["t_mid_utc"],
                            "ra_deg": meta["ra_deg"],
                            "dec_deg": meta["dec_deg"],
                        }
                    )
            elif hdf5_metadata:
                metadata_candidates.append(
                    MetadataCandidate(path=full_path, timestamp=dt_utc, day=day)
                )

    if hdf5_metadata and metadata_cache_path is not None:
        _scan_incremental_metadata(
            accum,
            metadata_candidates,
            cache_path=metadata_cache_path,
            update_limit=metadata_update_limit,
            retry_seconds=metadata_retry_seconds,
            pointing_timeseries=pointing_timeseries,
            pointing_timeseries_max_files=pointing_timeseries_max_files,
            now=metadata_now or datetime.now(timezone.utc),
        )
    elif pointing_timeseries and hdf5_metadata and accum.file_count > len(accum.timeseries_rows):
        accum.timeseries_truncated = True

    return accum


def build_out(
    *,
    root: Path,
    out_dir: Path,
    no_stat: bool,
    site_dir: Path | None = None,
    hdf5_metadata: bool = True,
    pointing_timeseries: bool = False,
    pointing_timeseries_max_files: int = 5000,
    metadata_cache_path: Path | None = None,
    metadata_update_limit: int = 100,
    metadata_retry_seconds: int = 3600,
) -> tuple[Path, bool]:
    """Scan, write manifest.json, optional pointing_timeseries.json, copy static site into out_dir."""
    accum = scan_directory(
        root,
        no_stat=no_stat,
        hdf5_metadata=hdf5_metadata,
        pointing_timeseries=pointing_timeseries,
        pointing_timeseries_max_files=pointing_timeseries_max_files,
        metadata_cache_path=metadata_cache_path,
        metadata_update_limit=metadata_update_limit,
        metadata_retry_seconds=metadata_retry_seconds,
    )
    manifest = build_manifest(
        source_root=str(root.resolve()),
        accum=accum,
        no_stat=no_stat,
        hdf5_metadata=hdf5_metadata,
        pointing_timeseries=pointing_timeseries,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    wrote_timeseries = False
    if accum.timeseries_rows:
        ts_path = out_dir / "pointing_timeseries.json"
        with open(ts_path, "w", encoding="utf-8") as f:
            json.dump(accum.timeseries_rows, f, indent=2)
            f.write("\n")
        wrote_timeseries = True

    if site_dir is None:
        site_dir = Path(__file__).resolve().parent / "site"
    if site_dir.is_dir():
        for child in site_dir.iterdir():
            dest = out_dir / child.name
            if child.is_file():
                shutil.copy2(child, dest)
            else:
                shutil.copytree(child, dest, dirs_exist_ok=True)

    return manifest_path, wrote_timeseries


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run a scan; used as setuptools console script."""
    parser = argparse.ArgumentParser(
        description="Scan DSA-110 incoming HDF5 files and build static dashboard output."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/data/incoming"),
        help="Directory to scan (default: /data/incoming)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory (manifest.json + copied site assets)",
    )
    parser.add_argument(
        "--no-stat",
        action="store_true",
        help="Do not stat() files; counts only (bytes and mtime freshness omitted)",
    )
    parser.add_argument(
        "--no-hdf5-metadata",
        action="store_true",
        help="Do not open HDF5 files; skip declination and pointing timeseries",
    )
    parser.add_argument(
        "--pointing-timeseries",
        action="store_true",
        help="Emit pointing_timeseries.json (per-file RA/Dec, mid-time); requires HDF5 metadata",
    )
    parser.add_argument(
        "--pointing-timeseries-max-files",
        type=int,
        default=5000,
        metavar="N",
        help="Cap rows in pointing_timeseries.json (default: 5000)",
    )
    parser.add_argument(
        "--metadata-cache",
        type=Path,
        default=None,
        help="Persistent SQLite cache for bounded incremental HDF5 metadata reads",
    )
    parser.add_argument(
        "--metadata-update-limit",
        type=int,
        default=100,
        metavar="N",
        help="Maximum uncached or retryable HDF5 files to open per run (default: 100)",
    )
    parser.add_argument(
        "--metadata-retry-seconds",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="Delay before retrying failed metadata reads (default: 3600)",
    )
    parser.add_argument(
        "--site",
        type=Path,
        default=None,
        help="Override path to static site directory (default: package site/)",
    )
    args = parser.parse_args(argv)

    root = args.root
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1

    hdf5_metadata = not args.no_hdf5_metadata
    pointing_ts = bool(args.pointing_timeseries) and hdf5_metadata
    if args.pointing_timeseries and not hdf5_metadata:
        print(
            "warning: --pointing-timeseries ignored because --no-hdf5-metadata was set",
            file=sys.stderr,
        )

    _, wrote_timeseries = build_out(
        root=root,
        out_dir=args.out,
        no_stat=args.no_stat,
        site_dir=args.site,
        hdf5_metadata=hdf5_metadata,
        pointing_timeseries=pointing_ts,
        pointing_timeseries_max_files=max(1, args.pointing_timeseries_max_files),
        metadata_cache_path=args.metadata_cache,
        metadata_update_limit=max(0, args.metadata_update_limit),
        metadata_retry_seconds=max(0, args.metadata_retry_seconds),
    )
    print(f"Wrote {args.out / 'manifest.json'}")
    if pointing_ts and wrote_timeseries:
        print(f"Wrote {args.out / 'pointing_timeseries.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
