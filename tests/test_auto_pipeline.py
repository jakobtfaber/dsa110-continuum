"""Tests for the scheduled batch-pipeline launcher."""

import sys
import time
from pathlib import Path

from dsa110_continuum.observability.control import ControlConfig, get_run
from scripts import auto_pipeline
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


def test_forwards_cflag_mode_to_batch_request(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    def launch(request, _config):
        captured["request"] = request
        return {"run_id": "test", "pid": 1}

    monkeypatch.setattr(auto_pipeline, "launch_run", launch)
    outcome = decide_and_launch("2026-01-25", config, rfi_mode="cflag")

    assert outcome["action"] == "launched"
    assert captured["request"].rfi_mode == "cflag"


def test_prepares_before_launching(tmp_path, monkeypatch):
    config = _config(tmp_path)
    events = []

    def prepare(*args, **kwargs):
        events.append("prepare")
        return {"action": "ready", "ms_count": 1}

    def launch(*args, **kwargs):
        events.append("launch")
        return {"action": "launched", "run_id": "test", "pid": 1}

    monkeypatch.setattr(auto_pipeline, "prepare_date", prepare)
    monkeypatch.setattr(auto_pipeline, "decide_and_launch", launch)

    outcome = auto_pipeline.prepare_and_launch("2026-01-25", config)

    assert events == ["prepare", "launch"]
    assert outcome["intake"]["ms_count"] == 1


def test_does_not_launch_when_intake_fails(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def must_not_launch(*args, **kwargs):
        raise AssertionError("must not launch")

    monkeypatch.setattr(
        auto_pipeline,
        "prepare_date",
        lambda *args, **kwargs: {"action": "conversion_failed"},
    )
    monkeypatch.setattr(auto_pipeline, "decide_and_launch", must_not_launch)

    assert auto_pipeline.prepare_and_launch("2026-01-25", config) == {"action": "conversion_failed"}


def test_waits_for_registered_run_completion(tmp_path, monkeypatch):
    states = iter([{"status": "running"}, {"status": "succeeded", "exit_code": 0}])
    monkeypatch.setattr(auto_pipeline, "get_run", lambda *args: next(states))
    monkeypatch.setattr(auto_pipeline.time, "sleep", lambda *args: None)

    record = auto_pipeline.wait_for_run("run-id", _config(tmp_path))

    assert record == {"status": "succeeded", "exit_code": 0}
