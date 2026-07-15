"""Per-artifact in-flight job state (#57): unit + routed-card tests (cloud-safe)."""

from __future__ import annotations

from pathlib import Path

import pytest
from dsa110_continuum.observability import job_state
from fastapi.testclient import TestClient
from scripts.qa_server import DashboardConfig, create_app

TS = "2026-01-25T22:26:05"
MS = f"{TS}.ms"


def _make_config(tmp_path: Path) -> DashboardConfig:
    return DashboardConfig(
        stage=tmp_path / "stage",
        products=tmp_path / "products",
        incoming=tmp_path / "incoming",
        thumb_dir=tmp_path / "thumbs",
        campaign_outputs=tmp_path / "campaign",
        campaign_date="2026-07-13",
        campaign_hour=11,
    )


def _make_ms(config: DashboardConfig, name: str = MS) -> Path:
    path = config.stage / "ms" / name
    path.mkdir(parents=True)
    (path / "table.dat").write_bytes(b"x")
    return path


class TestJobsForTimestamp:
    def test_direct_process_match_on_timestamp(self, monkeypatch):
        monkeypatch.setattr(
            job_state,
            "active_processes",
            lambda: [
                {"pid": 111, "elapsed_seconds": 5, "command": f"wsclean -name /x/{TS} ..."},
                {"pid": 222, "elapsed_seconds": 9, "command": "wsclean -name /x/other ..."},
            ],
        )
        monkeypatch.setattr(job_state, "_running_runs", lambda config: [])
        job = job_state.jobs_for_timestamp(TS)
        assert [proc["pid"] for proc in job["processes"]] == [111]
        assert job["active"] is True

    def test_date_level_batch_match(self, monkeypatch):
        monkeypatch.setattr(
            job_state,
            "active_processes",
            lambda: [
                {
                    "pid": 333,
                    "elapsed_seconds": 60,
                    "command": "python scripts/batch_pipeline.py --date 2026-01-25",
                }
            ],
        )
        monkeypatch.setattr(job_state, "_running_runs", lambda config: [])
        job = job_state.jobs_for_timestamp(TS)
        assert job["processes"] == []
        assert [proc["pid"] for proc in job["batch"]] == [333]
        assert job["active"] is True

    def test_running_run_log_filtered_to_timestamp(self, tmp_path, monkeypatch):
        log = tmp_path / "run_x.log"
        log.write_text(
            f"start\nprocessing {TS}.ms stage=applycal\nother 2026-01-25T23:00:00 line\n"
            f"{TS} wsclean iteration 12\n"
        )
        monkeypatch.setattr(job_state, "active_processes", lambda: [])
        monkeypatch.setattr(
            job_state,
            "_running_runs",
            lambda config: [{"run_id": "r1", "pid": 999, "log_path": str(log), "request_json": ""}],
        )
        job = job_state.jobs_for_timestamp(TS)
        assert len(job["runs"]) == 1
        lines = job["runs"][0]["log_lines"]
        assert len(lines) == 2
        assert all(TS in line for line in lines)

    def test_inactive_when_nothing_matches(self, monkeypatch):
        monkeypatch.setattr(job_state, "active_processes", lambda: [])
        monkeypatch.setattr(job_state, "_running_runs", lambda config: [])
        job = job_state.jobs_for_timestamp(TS)
        assert job == {"processes": [], "batch": [], "runs": [], "active": False}

    def test_registry_errors_degrade_to_no_runs(self, monkeypatch):
        monkeypatch.setattr(job_state, "active_processes", lambda: [])

        def boom(config):
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr(job_state, "_running_runs", boom)
        job = job_state.jobs_for_timestamp(TS)
        assert job["runs"] == []
        assert job["active"] is False


class TestJobCardOnPages:
    def test_ms_page_shows_active_job(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_ms(config)
        monkeypatch.setattr(
            job_state,
            "jobs_for_timestamp",
            lambda ts, control_config=None, max_lines=40: {
                "processes": [{"pid": 12345, "elapsed_seconds": 30, "command": f"wsclean {ts}"}],
                "batch": [],
                "runs": [
                    {
                        "run_id": "r9",
                        "pid": 12345,
                        "log_path": "/tmp/run.log",
                        "log_lines": [f"{ts} cleaning <b>bold</b>"],
                    }
                ],
                "active": True,
            },
        )
        with TestClient(create_app(config)) as client:
            page = client.get(f"/artifacts/ms/{MS}")
            status = client.get(f"/artifacts/ms/{MS}/status").json()
        assert "In-flight job" in page.text
        assert "12345" in page.text
        assert "&lt;b&gt;bold&lt;/b&gt;" in page.text  # log lines escaped
        assert status["job"]["active"] is True

    def test_ms_page_shows_no_active_job(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_ms(config)
        monkeypatch.setattr(
            job_state,
            "jobs_for_timestamp",
            lambda ts, control_config=None, max_lines=40: {
                "processes": [],
                "batch": [],
                "runs": [],
                "active": False,
            },
        )
        with TestClient(create_app(config)) as client:
            page = client.get(f"/artifacts/ms/{MS}")
        assert "No active job touching this artifact" in page.text

    def test_caltable_and_tile_status_carry_job(self, tmp_path, monkeypatch):
        import numpy as np
        from astropy.io import fits
        from dsa110_continuum.observability import caltable_qa, tile_qa

        config = _make_config(tmp_path)
        table = config.stage / "ms" / f"{TS}_0~23.g"
        table.mkdir(parents=True)
        tile_ts = "2026-01-25T02:01:43"
        tile_dir = config.stage / "images" / "mosaic_2026-01-25"
        tile_dir.mkdir(parents=True)
        fits.writeto(tile_dir / f"{tile_ts}-image.fits", np.zeros((4, 4), dtype=np.float32))
        monkeypatch.setattr(caltable_qa, "summary", lambda path: {"name": table.name})
        monkeypatch.setattr(tile_qa, "summary", lambda products, ms_path: {"gate": None})
        monkeypatch.setattr(
            job_state,
            "jobs_for_timestamp",
            lambda ts, control_config=None, max_lines=40: {
                "processes": [],
                "batch": [],
                "runs": [],
                "active": False,
            },
        )
        with TestClient(create_app(config)) as client:
            cal = client.get(f"/artifacts/caltable/{table.name}/status").json()
            tile = client.get(f"/artifacts/tile/{tile_ts}/status").json()
        assert cal["job"]["active"] is False
        assert tile["job"]["active"] is False


STAGE_MS = Path("/stage/dsa110-contimg/ms")
requires_stage = pytest.mark.skipif(not STAGE_MS.is_dir(), reason="H17 stage volume not present")


@requires_stage
class TestLiveJobCard:
    def test_real_ms_page_renders_job_section(self, tmp_path):
        from dsa110_continuum.observability import artifacts

        records = artifacts.list_ms(STAGE_MS, limit=1)
        config = DashboardConfig(thumb_dir=tmp_path / "thumbs")
        with TestClient(create_app(config)) as client:
            page = client.get(f"/artifacts/ms/{records[0]['name']}")
        assert page.status_code == 200
        assert "In-flight job" in page.text
