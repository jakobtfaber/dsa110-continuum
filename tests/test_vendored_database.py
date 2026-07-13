"""The vendored database layer must work without dsa110_contimg or H17 paths.

Phase 5 of the contimg-import-retirement migration
(docs/archive/contimg-retirement/plan-contimg-import-retirement.md).
"""
from pathlib import Path


def test_pipeline_db_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DB", str(tmp_path / "pipeline.sqlite3"))
    from dsa110_continuum.database import ensure_pipeline_db, get_pipeline_db_path

    assert get_pipeline_db_path() == tmp_path / "pipeline.sqlite3"
    ensure_pipeline_db()
    assert (tmp_path / "pipeline.sqlite3").exists()


def test_database_class_connects(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DB", str(tmp_path / "pipeline.sqlite3"))
    from dsa110_continuum.database import Database, ensure_pipeline_db

    ensure_pipeline_db()
    with Database(tmp_path / "pipeline.sqlite3") as db:
        assert db.query_val("SELECT 1") == 1


def test_hdf5_index_selector_importable():
    from dsa110_continuum.database.hdf5_index import (
        parse_subband_filename,
        query_subband_groups,
        select_hdf5_groups_by_position,
    )

    assert callable(select_hdf5_groups_by_position)
    assert callable(query_subband_groups)
    # oracle: filename convention '<iso timestamp>_sb<NN>.hdf5'
    assert parse_subband_filename("2026-01-25T22:00:00_sb05.hdf5") == (
        "2026-01-25T22:00:00",
        5,
    )
    assert parse_subband_filename("not_a_subband.txt") is None


def test_models_and_session_importable(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DB", str(tmp_path / "pipeline.sqlite3"))
    from dsa110_continuum.database.models import MSIndex, QAPlot
    from dsa110_continuum.database.session import get_session

    assert hasattr(QAPlot, "__tablename__")
    assert hasattr(MSIndex, "__tablename__")
    assert callable(get_session)


def test_provenance_modules():
    from dsa110_continuum.database.provenance import track_calibration_provenance
    from dsa110_continuum.database.tracking import ProvenanceTracker

    assert callable(track_calibration_provenance)
    tracker = ProvenanceTracker(job_id="test-job-1")
    assert tracker is not None


def test_pipeline_metrics():
    from dsa110_continuum.database.pipeline_metrics import (
        PipelineStage,
        record_stage_timing,
    )

    assert callable(record_stage_timing)
    assert PipelineStage is not None


def test_data_registry_importable(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_DB", str(tmp_path / "pipeline.sqlite3"))
    from dsa110_continuum.database.data_registry import (
        ensure_data_registry_db,
        link_photometry_to_data,
    )

    assert callable(ensure_data_registry_db)
    assert callable(link_photometry_to_data)


def test_validate_path_safe_vendored(tmp_path):
    from dsa110_continuum.utils.naming import validate_path_safe

    base = tmp_path / "staging"
    base.mkdir()
    ok, _ = validate_path_safe(base / "file.hdf5", base)
    assert ok is True
    bad, msg = validate_path_safe(Path("/etc/passwd"), base)
    assert bad is False and msg
