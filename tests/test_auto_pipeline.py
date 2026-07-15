"""Tests for the scheduled batch-pipeline launcher."""

import sys
import time
from pathlib import Path

from dsa110_continuum.observability.control import ControlConfig, get_run
from scripts.auto_pipeline import decide_and_launch


def _config(tmp_path: Path) -> ControlConfig:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "batch_pipeline.py").write_text("print('auto ok')\n")
    return ControlConfig(
        repo_root=tmp_path, python=sys.executable, control_dir=tmp_path / "control"
    )


def test_launches_and_registers_run(tmp_path):
    config = _config(tmp_path)
    outcome = decide_and_launch(date="2026-01-25", config=config)
    assert outcome["action"] == "launched"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        row = get_run(outcome["run_id"], config)
        if row["status"] != "running":
            break
        time.sleep(0.2)
    assert row["status"] == "succeeded"
    assert "--retry-failed" in row["argv_json"]


def test_skips_when_a_run_is_already_active(tmp_path):
    config = _config(tmp_path)
    (tmp_path / "scripts" / "batch_pipeline.py").write_text("import time; time.sleep(60)\n")
    first = decide_and_launch(date="2026-01-25", config=config)
    assert first["action"] == "launched"
    try:
        second = decide_and_launch(date="2026-01-25", config=config)
        assert second["action"] == "skipped_running"
    finally:
        from dsa110_continuum.observability.control import terminate_run

        terminate_run(first["run_id"], config, grace_seconds=2.0)


def test_invalid_date_reports_error_outcome(tmp_path):
    config = _config(tmp_path)
    outcome = decide_and_launch(date="not-a-date", config=config)
    assert outcome["action"] == "invalid_request"
