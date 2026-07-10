# ruff: noqa: D103
"""Tests for DSA-110 incoming HDF5 manifest and gap logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dsacamera_monitor.gaps import compute_gaps, gaps_from_by_day_rows
from dsacamera_monitor.hdf5_pointing import (
    read_phase_center_dec_deg,
    read_pointing_metadata,
    read_pointing_ra_dec_deg,
)
from dsacamera_monitor.manifest import (
    BeamAgg,
    DayAgg,
    ScanAccum,
    build_manifest,
    try_parse_filename,
)


def test_try_parse_filename_valid() -> None:
    p = try_parse_filename("2025-05-05T12:34:56_sb03.hdf5")
    assert p is not None
    dt, beam = p
    assert beam == 3
    assert dt == datetime(2025, 5, 5, 12, 34, 56, tzinfo=timezone.utc)


def test_try_parse_filename_invalid() -> None:
    assert try_parse_filename("not_a_match.hdf5") is None
    assert try_parse_filename("2025-05-05T12:34:56_sb03.txt") is None


def test_compute_gaps_middle() -> None:
    days = {date(2025, 1, 1), date(2025, 1, 5)}
    gaps = compute_gaps(days, date(2025, 1, 1), date(2025, 1, 5))
    assert len(gaps) == 1
    assert gaps[0]["start"] == "2025-01-02"
    assert gaps[0]["end"] == "2025-01-04"
    assert gaps[0]["days"] == 3


def test_compute_gaps_none() -> None:
    days = {date(2025, 6, 1)}
    gaps = compute_gaps(days, date(2025, 6, 1), date(2025, 6, 1))
    assert gaps == []


def test_gaps_from_by_day_rows() -> None:
    rows = [
        {"date": "2025-01-01", "count": 1, "bytes": 0},
        {"date": "2025-01-03", "count": 1, "bytes": 0},
    ]
    g = gaps_from_by_day_rows(rows)
    assert len(g) == 1
    assert g[0]["start"] == "2025-01-02"
    assert g[0]["end"] == "2025-01-02"
    assert g[0]["days"] == 1


def test_build_manifest_roundtrip() -> None:
    accum = ScanAccum()
    d = date(2025, 1, 1)
    accum.by_day[d] = DayAgg()
    accum.by_day[d].count = 2
    accum.by_day[d].bytes = 100
    accum.by_beam[4] = BeamAgg()
    accum.by_beam[4].count = 2
    accum.by_beam[4].bytes = 100
    accum.file_count = 2
    accum.total_bytes = 100
    accum.latest_filename_dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    accum.earliest_filename_dt = accum.latest_filename_dt
    accum.latest_mtime = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    accum.earliest_mtime = accum.latest_mtime

    m = build_manifest(
        source_root="/tmp/incoming",
        accum=accum,
        no_stat=False,
        hdf5_metadata=False,
        generated_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    assert m["schema_version"] == 2
    assert m["totals"]["file_count"] == 2
    assert m["by_day"][0]["date"] == "2025-01-01"
    assert m["gaps"] == []
    assert "pointing" not in m
    assert m["options"]["hdf5_metadata"] is False


def test_scan_directory(tmp_path: Path) -> None:
    from dsacamera_monitor.scan import scan_directory

    (tmp_path / "2025-01-01T00:00:00_sb01.hdf5").write_bytes(b"x")
    (tmp_path / "2025-01-01T01:00:00_sb02.hdf5").write_bytes(b"yy")
    (tmp_path / "skip.txt").write_text("x")
    accum = scan_directory(tmp_path, no_stat=False, hdf5_metadata=False)
    assert accum.file_count == 2
    assert accum.total_bytes == 3
    assert set(accum.by_beam.keys()) == {1, 2}


def test_scan_directory_with_hdf5_dec(tmp_path: Path) -> None:
    import h5py
    import numpy as np
    from dsacamera_monitor.scan import scan_directory

    fn = "2025-06-15T10:00:00_sb05.hdf5"
    p = tmp_path / fn
    with h5py.File(p, "w") as f:
        f.create_dataset(
            "Header/extra_keywords/phase_center_dec",
            data=np.float64(np.deg2rad(40.5)),
        )
    accum = scan_directory(tmp_path, no_stat=True, hdf5_metadata=True)
    assert accum.file_count == 1
    assert accum.files_with_dec == 1
    assert accum.files_dec_missing == 0
    assert accum.files_dec_read_failed == 0
    assert accum.global_dec_min is not None and abs(accum.global_dec_min - 40.5) < 1e-6
    assert read_phase_center_dec_deg(p) is not None


def test_build_out_copies_site(tmp_path: Path) -> None:
    from dsacamera_monitor.scan import build_out

    src = tmp_path / "src"
    src.mkdir()
    (src / "2025-01-01T00:00:00_sb01.hdf5").write_bytes(b"x")
    out = tmp_path / "out"
    _, wrote_ts = build_out(root=src, out_dir=out, no_stat=True, hdf5_metadata=False)
    assert wrote_ts is False
    assert (out / "manifest.json").is_file()
    assert (out / "index.html").is_file()
    assert (out / "js" / "dashboard.js").is_file()
    man = (out / "manifest.json").read_text()
    assert '"schema_version": 2' in man


def test_dashboard_explains_unavailable_or_warming_metadata(tmp_path: Path) -> None:
    from dsacamera_monitor.scan import build_out

    src = tmp_path / "src"
    src.mkdir()
    (src / "2025-01-01T00:00:00_sb01.hdf5").write_bytes(b"x")
    out = tmp_path / "out"
    build_out(root=src, out_dir=out, no_stat=True, hdf5_metadata=False)
    html = (out / "index.html").read_text()
    javascript = (out / "js" / "dashboard.js").read_text()
    assert 'id="dec-metadata-status"' in html
    assert 'id="pointing-metadata-status"' in html
    assert "Metadata warming/unavailable" in javascript


def test_pointing_timeseries_file(tmp_path: Path) -> None:
    import h5py
    import numpy as np
    from dsacamera_monitor.scan import build_out

    src = tmp_path / "src"
    src.mkdir()
    fn = "2025-03-01T08:00:00_sb00.hdf5"
    p = src / fn
    with h5py.File(p, "w") as f:
        f.create_dataset("Header/extra_keywords/phase_center_dec", data=np.float64(0.7))
        f.create_dataset("Header/extra_keywords/ha_phase_center", data=np.float64(0.01))
        f.create_dataset("Header/time_array", data=np.array([2450000.5, 2450000.6]))

    out = tmp_path / "out"
    _, wrote_ts = build_out(
        root=src,
        out_dir=out,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        pointing_timeseries_max_files=10,
    )
    assert wrote_ts is True
    assert (out / "pointing_timeseries.json").is_file()
    m = __import__("json").loads((out / "manifest.json").read_text())
    assert m["pointing_timeseries"]["row_count"] == 1
    assert "pointing" in m
    assert "dec_unique" not in m["pointing"]


def test_pointing_fallback_header_keys(tmp_path: Path) -> None:
    import h5py
    import numpy as np

    fn = "2025-04-01T00:00:00_sb01.hdf5"
    p = tmp_path / fn
    with h5py.File(p, "w") as f:
        f.create_dataset("Header/phase_center_dec", data=np.float64(np.deg2rad(15.25)))
        f.create_dataset("Header/phase_center_ra", data=np.float64(np.deg2rad(230.5)))
        f.create_dataset("Header/time_array", data=np.array([2450100.1, 2450100.2]))

    ra, dec = read_pointing_ra_dec_deg(p)
    assert ra is not None and abs(ra - 230.5) < 1e-6
    assert dec is not None and abs(dec - 15.25) < 1e-6


def test_pointing_ha_fallback_allows_offline_predictive_iers(tmp_path: Path) -> None:
    import h5py
    import numpy as np

    path = tmp_path / "2026-03-15T00:00:00_sb01.hdf5"
    with h5py.File(path, "w") as handle:
        handle.create_dataset(
            "Header/extra_keywords/phase_center_dec",
            data=np.float64(np.deg2rad(16.1)),
        )
        handle.create_dataset(
            "Header/extra_keywords/ha_phase_center",
            data=np.float64(0.01),
        )
        handle.create_dataset("Header/time_array", data=np.array([2461114.5, 2461114.6]))

    metadata = read_pointing_metadata(path)
    assert metadata["pointing_status"] == "ok"
    assert metadata["t_mid_utc"] is not None
    assert metadata["ra_deg"] is not None
    assert metadata["error"] is None


def test_scan_counts_read_failures(tmp_path: Path) -> None:
    from dsacamera_monitor.scan import scan_directory

    bad = tmp_path / "2025-07-01T00:00:00_sb01.hdf5"
    bad.write_bytes(b"not hdf5")
    accum = scan_directory(tmp_path, no_stat=True, hdf5_metadata=True, pointing_timeseries=True)
    assert accum.file_count == 1
    assert accum.files_with_dec == 0
    assert accum.files_dec_missing == 0
    assert accum.files_dec_read_failed == 1
    assert accum.files_pointing_read_failed == 1
    assert len(accum.timeseries_rows) == 1


def _cached_meta(path: Path, *, failed: bool = False) -> dict:
    if failed:
        return {
            "filename": path.name,
            "t_mid_utc": None,
            "ra_deg": None,
            "dec_deg": None,
            "dec_status": "read_failed",
            "pointing_status": "read_failed",
            "error": "OSError: corrupt fixture",
        }
    parsed = try_parse_filename(path.name)
    assert parsed is not None
    timestamp, _ = parsed
    return {
        "filename": path.name,
        "t_mid_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "ra_deg": float(timestamp.hour),
        "dec_deg": 16.1,
        "dec_status": "ok",
        "pointing_status": "ok",
        "error": None,
    }


def _make_named_files(root: Path, timestamps: list[str]) -> list[Path]:
    paths = []
    for timestamp in timestamps:
        path = root / f"{timestamp}_sb00.hdf5"
        path.write_bytes(b"fixture")
        paths.append(path)
    return paths


def test_incremental_cache_cold_warm_and_one_new_file(tmp_path: Path, monkeypatch) -> None:
    from dsacamera_monitor.scan import scan_directory

    root = tmp_path / "incoming"
    root.mkdir()
    _make_named_files(root, ["2025-01-01T00:00:00", "2025-01-01T01:00:00"])
    cache = tmp_path / "pointing.sqlite3"
    opened: list[str] = []

    def fake_read(path: Path) -> dict:
        opened.append(path.name)
        return _cached_meta(path)

    monkeypatch.setattr("dsacamera_monitor.scan.read_pointing_metadata", fake_read)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cold = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_update_limit=100,
        metadata_now=now,
    )
    assert len(opened) == 2
    assert cold.metadata_cached == 2
    assert cold.metadata_pending == 0
    assert cold.metadata_emitted == 2

    opened.clear()
    warm = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_update_limit=100,
        metadata_now=now + timedelta(minutes=5),
    )
    assert opened == []
    assert warm.metadata_cached == 2

    _make_named_files(root, ["2025-01-01T02:00:00"])
    opened.clear()
    updated = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_update_limit=100,
        metadata_now=now + timedelta(minutes=10),
    )
    assert opened == ["2025-01-01T02:00:00_sb00.hdf5"]
    assert updated.metadata_cached == 3


def test_incremental_cache_bounds_newest_first_and_emits_deterministically(
    tmp_path: Path, monkeypatch
) -> None:
    from dsacamera_monitor.scan import scan_directory

    root = tmp_path / "incoming"
    root.mkdir()
    _make_named_files(
        root,
        [
            "2025-01-01T00:00:00",
            "2025-01-01T01:00:00",
            "2025-01-01T02:00:00",
        ],
    )
    opened: list[str] = []

    def fake_read(path: Path) -> dict:
        opened.append(path.name)
        return _cached_meta(path)

    monkeypatch.setattr("dsacamera_monitor.scan.read_pointing_metadata", fake_read)
    accum = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        pointing_timeseries_max_files=2,
        metadata_cache_path=tmp_path / "cache.sqlite3",
        metadata_update_limit=2,
        metadata_now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert opened == [
        "2025-01-01T02:00:00_sb00.hdf5",
        "2025-01-01T01:00:00_sb00.hdf5",
    ]
    assert accum.metadata_pending == 1
    assert [row["filename"] for row in accum.timeseries_rows] == [
        "2025-01-01T01:00:00_sb00.hdf5",
        "2025-01-01T02:00:00_sb00.hdf5",
    ]


def test_incremental_cache_retries_failures_after_interval(tmp_path: Path, monkeypatch) -> None:
    from dsacamera_monitor.scan import scan_directory

    root = tmp_path / "incoming"
    root.mkdir()
    path = _make_named_files(root, ["2025-01-01T00:00:00"])[0]
    opened: list[str] = []
    attempts = 0

    def fake_read(candidate: Path) -> dict:
        nonlocal attempts
        attempts += 1
        opened.append(candidate.name)
        return _cached_meta(candidate, failed=attempts == 1)

    monkeypatch.setattr("dsacamera_monitor.scan.read_pointing_metadata", fake_read)
    cache = tmp_path / "cache.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_retry_seconds=3600,
        metadata_now=now,
    )
    assert first.metadata_failed == 1

    opened.clear()
    before_retry = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_retry_seconds=3600,
        metadata_now=now + timedelta(minutes=59),
    )
    assert opened == []
    assert before_retry.metadata_retried == 0

    after_retry = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_retry_seconds=3600,
        metadata_now=now + timedelta(hours=1),
    )
    assert opened == [path.name]
    assert after_retry.metadata_retried == 1
    assert after_retry.metadata_failed == 0


def test_retryable_failure_is_not_starved_by_uncached_backlog(tmp_path: Path, monkeypatch) -> None:
    from dsacamera_monitor.scan import scan_directory

    root = tmp_path / "incoming"
    root.mkdir()
    old_path = _make_named_files(root, ["2025-01-01T00:00:00"])[0]
    attempts: dict[str, int] = {}
    opened: list[str] = []

    def fake_read(path: Path) -> dict:
        opened.append(path.name)
        attempts[path.name] = attempts.get(path.name, 0) + 1
        return _cached_meta(path, failed=attempts[path.name] == 1)

    monkeypatch.setattr("dsacamera_monitor.scan.read_pointing_metadata", fake_read)
    cache = tmp_path / "cache.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        metadata_cache_path=cache,
        metadata_update_limit=1,
        metadata_now=now,
    )
    new_path = _make_named_files(root, ["2025-01-01T01:00:00"])[0]
    opened.clear()
    retried = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        metadata_cache_path=cache,
        metadata_update_limit=1,
        metadata_retry_seconds=3600,
        metadata_now=now + timedelta(hours=1),
    )
    assert opened == [old_path.name]
    assert new_path.name not in opened
    assert retried.metadata_retried == 1
    assert retried.metadata_pending == 1


def test_incremental_cache_ignores_removed_files_and_reports_manifest_progress(
    tmp_path: Path, monkeypatch
) -> None:
    from dsacamera_monitor.scan import scan_directory

    root = tmp_path / "incoming"
    root.mkdir()
    paths = _make_named_files(root, ["2025-01-01T00:00:00", "2025-01-01T01:00:00"])
    monkeypatch.setattr(
        "dsacamera_monitor.scan.read_pointing_metadata",
        lambda path: _cached_meta(path),
    )
    cache = tmp_path / "cache.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_now=now,
    )
    paths[0].unlink()
    accum = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=cache,
        metadata_now=now + timedelta(minutes=5),
    )
    manifest = build_manifest(
        source_root=str(root),
        accum=accum,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        generated_at=now,
    )
    assert accum.file_count == 1
    assert [row["filename"] for row in accum.timeseries_rows] == [paths[1].name]
    assert manifest["metadata_cache"] == {
        "cached": 1,
        "pending": 0,
        "failed": 0,
        "retried": 0,
        "emitted": 1,
        "error": None,
    }


def test_incremental_cache_does_not_retry_missing_metadata(tmp_path: Path, monkeypatch) -> None:
    from dsacamera_monitor.scan import scan_directory

    root = tmp_path / "incoming"
    root.mkdir()
    _make_named_files(root, ["2025-01-01T00:00:00"])
    opened: list[str] = []

    def missing_read(path: Path) -> dict:
        opened.append(path.name)
        meta = _cached_meta(path)
        meta.update(
            t_mid_utc=None,
            ra_deg=None,
            dec_deg=None,
            dec_status="missing",
            pointing_status="missing",
        )
        return meta

    monkeypatch.setattr("dsacamera_monitor.scan.read_pointing_metadata", missing_read)
    cache = tmp_path / "cache.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        metadata_cache_path=cache,
        metadata_now=now,
    )
    assert first.files_dec_missing == 1
    opened.clear()
    second = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        metadata_cache_path=cache,
        metadata_now=now + timedelta(days=1),
    )
    assert opened == []
    assert second.files_dec_missing == 1


def test_cache_failure_still_produces_count_inventory(tmp_path: Path, monkeypatch) -> None:
    from dsacamera_monitor.scan import scan_directory

    root = tmp_path / "incoming"
    root.mkdir()
    _make_named_files(root, ["2025-01-01T00:00:00"])
    monkeypatch.setattr(
        "dsacamera_monitor.scan.MetadataCache.load_rows",
        lambda self: (_ for _ in ()).throw(OSError("synthetic cache failure")),
    )
    accum = scan_directory(
        root,
        no_stat=True,
        hdf5_metadata=True,
        pointing_timeseries=True,
        metadata_cache_path=tmp_path / "cache.sqlite3",
    )
    assert accum.file_count == 1
    assert accum.by_day[date(2025, 1, 1)].count == 1
    assert accum.metadata_pending == 1
    assert accum.metadata_cache_error == "OSError: synthetic cache failure"


def test_metadata_cache_batch_write_is_atomic(tmp_path: Path) -> None:
    import sqlite3

    from dsacamera_monitor.metadata_cache import MetadataCache

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    valid = _cached_meta(tmp_path / "2025-01-01T00:00:00_sb00.hdf5")
    invalid = _cached_meta(tmp_path / "2025-01-01T01:00:00_sb00.hdf5")
    invalid["dec_status"] = None
    cache_path = tmp_path / "cache.sqlite3"
    with MetadataCache(cache_path) as cache:
        try:
            cache.write_attempts([valid, invalid], now)
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("invalid cache batch unexpectedly committed")
        assert cache.load_rows() == {}
