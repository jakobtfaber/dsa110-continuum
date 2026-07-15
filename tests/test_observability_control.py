"""Tests for the pipeline control module (launcher, registry, terminate)."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dsa110_continuum.observability import control as control_module
from dsa110_continuum.observability.control import (
    ControlConfig,
    RunConflictError,
    RunRequest,
    get_run,
    launch_run,
    list_runs,
    run_dry_run,
    terminate_run,
)


def _config(tmp_path: Path) -> ControlConfig:
    return ControlConfig(
        repo_root=tmp_path,
        python=sys.executable,
        control_dir=tmp_path / "control",
    )


FAKE_OK = "import sys; print('plan ok', ' '.join(sys.argv[1:])); sys.exit(0)\n"
FAKE_SLEEPER = (
    "import subprocess, sys, time, pathlib\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "pathlib.Path(sys.argv[sys.argv.index('--date') + 1] + '.childpid')"
    ".write_text(str(child.pid))\n"
    "time.sleep(120)\n"
)


def _install_fake_pipeline(tmp_path: Path, body: str) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "batch_pipeline.py").write_text(body)


class TestRunRequestValidation:
    """Criterion: every field that reaches argv is validated against a closed
    grammar (dates, hour ranges, enums, bounded ints) so no request can smuggle
    shell metacharacters or unexpected flags; argv is built exclusively from an
    allowlist. Basis: injection-safety invariant for a control surface."""

    def test_rejects_shell_metacharacters_in_date(self):
        for bad in ("2026-01-25; rm -rf /", "$(reboot)", "2026-01-25x", "..", ""):
            with pytest.raises(ValueError):
                RunRequest(date=bad)

    def test_rejects_impossible_calendar_date(self):
        with pytest.raises(ValueError):
            RunRequest(date="2026-13-45")

    def test_rejects_out_of_range_hours_and_bad_rfi_mode(self):
        with pytest.raises(ValueError):
            RunRequest(date="2026-01-25", start_hour=24)
        with pytest.raises(ValueError):
            RunRequest(date="2026-01-25", end_hour=-1)
        with pytest.raises(ValueError):
            RunRequest(date="2026-01-25", start_hour=5, end_hour=3)
        with pytest.raises(ValueError):
            RunRequest(date="2026-01-25", rfi_mode="sometimes")

    def test_rejects_out_of_bound_numeric_knobs(self):
        with pytest.raises(ValueError):
            RunRequest(date="2026-01-25", tile_timeout=10)
        with pytest.raises(ValueError):
            RunRequest(date="2026-01-25", quarantine_after_failures=100)
        with pytest.raises(ValueError):
            RunRequest(date="2026-01-25", photometry_workers=0)

    def test_argv_is_allowlisted_flags_only(self, tmp_path):
        request = RunRequest(
            date="2026-01-25",
            cal_date="2026-01-25",
            start_hour=22,
            end_hour=23,
            force_recal=True,
            retry_failed=True,
            lenient_qa=True,
            rfi_mode="conditional",
            quarantine_after_failures=3,
        )
        argv = request.to_argv(_config(tmp_path))
        assert argv[0] == sys.executable
        assert argv[1] == "scripts/batch_pipeline.py"
        assert "--date" in argv and "2026-01-25" in argv
        assert "--start-hour" in argv and "22" in argv
        assert "--force-recal" in argv and "--retry-failed" in argv
        assert "--rfi-mode" in argv and "conditional" in argv
        assert "--skip-photometry" not in argv and "--dry-run" not in argv
        joined = " ".join(argv)
        assert ";" not in joined and "$(" not in joined


class TestLaunchAndReap:
    """Criterion: a launched run is registered immediately with status running,
    its exit is reaped into succeeded/failed with the real exit code, its output
    lands in the per-run log, and only one launcher-owned run may be alive at a
    time. Basis: run-registry state machine contract."""

    def test_launch_registers_run_and_reaper_marks_succeeded(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_OK)
        config = _config(tmp_path)
        record = launch_run(RunRequest(date="2026-01-25"), config)
        assert record["status"] == "running" and record["pid"] > 0
        deadline = time.monotonic() + 15
        row = record
        while time.monotonic() < deadline:
            row = get_run(record["run_id"], config)
            if row["status"] not in ("running", "orphaned"):
                break
            time.sleep(0.2)
        assert row["status"] == "succeeded" and row["exit_code"] == 0
        assert "plan ok" in Path(row["log_path"]).read_text()

    def test_failed_pipeline_is_reaped_as_failed(self, tmp_path):
        _install_fake_pipeline(tmp_path, "import sys; sys.exit(3)\n")
        config = _config(tmp_path)
        record = launch_run(RunRequest(date="2026-01-25"), config)
        deadline = time.monotonic() + 15
        row = record
        while time.monotonic() < deadline:
            row = get_run(record["run_id"], config)
            if row["status"] not in ("running", "orphaned"):
                break
            time.sleep(0.2)
        assert row["status"] == "failed" and row["exit_code"] == 3

    def test_single_flight_guard_rejects_concurrent_launch(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_SLEEPER)
        config = _config(tmp_path)
        first = launch_run(RunRequest(date="2026-01-25"), config)
        try:
            with pytest.raises(RunConflictError):
                launch_run(RunRequest(date="2026-01-26"), config)
        finally:
            terminate_run(first["run_id"], config, grace_seconds=2.0)

    def test_terminate_kills_whole_process_group(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_SLEEPER)
        config = _config(tmp_path)
        record = launch_run(RunRequest(date="2026-01-25"), config)
        childpid_file = tmp_path / "2026-01-25.childpid"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not childpid_file.exists():
            time.sleep(0.1)
        assert childpid_file.exists(), "fake pipeline never spawned its child"
        child_pid = int(childpid_file.read_text())
        terminate_run(record["run_id"], config, grace_seconds=2.0)
        row = get_run(record["run_id"], config)
        assert row["status"] == "terminated"
        time.sleep(0.5)
        assert not Path(f"/proc/{record['pid']}").exists()
        assert not Path(f"/proc/{child_pid}").exists()

    def test_terminate_non_running_run_raises(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_OK)
        config = _config(tmp_path)
        record = launch_run(RunRequest(date="2026-01-25"), config)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if get_run(record["run_id"], config)["status"] == "succeeded":
                break
            time.sleep(0.2)
        with pytest.raises(RunConflictError):
            terminate_run(record["run_id"], config)

    def test_dry_run_returns_plan_text_and_registers_nothing(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_OK)
        config = _config(tmp_path)
        output = run_dry_run(RunRequest(date="2026-01-25", dry_run=True), config)
        assert "plan ok" in output and "--dry-run" in output
        assert list_runs(config) == []

    def test_get_unknown_run_raises_keyerror(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_OK)
        config = _config(tmp_path)
        launch_run(RunRequest(date="2026-01-25"), config)
        with pytest.raises(KeyError):
            get_run("nope", config)


class TestProcessIdentity:
    """Criterion: a registry row may only be treated as live — and signaled —
    when the current occupant of its PID is the same process that was launched
    (verified via /proc/<pid>/stat start time). PID reuse after a reboot or
    wraparound must reconcile to 'orphaned' and make terminate refuse; it must
    never signal a stranger's process group. Basis: PR #117 review finding 1."""

    def test_identity_mismatch_reconciles_orphaned_and_refuses_terminate(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_SLEEPER)
        config = _config(tmp_path)
        record = launch_run(RunRequest(date="2026-01-25"), config)
        try:
            with sqlite3.connect(config.db_path) as connection:
                connection.execute(
                    "UPDATE runs SET proc_start = proc_start + 991 WHERE run_id = ?",
                    (record["run_id"],),
                )
            assert get_run(record["run_id"], config)["status"] == "orphaned"
            with pytest.raises(RunConflictError):
                terminate_run(record["run_id"], config, grace_seconds=1.0)
            assert Path(f"/proc/{record['pid']}").exists(), "refusal must not signal the pgid"
        finally:
            os.killpg(record["pid"], signal.SIGKILL)

    def test_legacy_row_without_identity_is_orphaned_and_not_killable(self, tmp_path):
        config = _config(tmp_path)
        config.control_dir.mkdir(parents=True, exist_ok=True)
        decoy = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
        )
        try:
            with control_module._connect(config.db_path) as connection:
                connection.execute(
                    "INSERT INTO runs (run_id, created_at, finished_at, request_json,"
                    " argv_json, pid, log_path, status, exit_code, proc_start)"
                    " VALUES (?, ?, NULL, '{}', '[]', ?, ?, 'running', NULL, NULL)",
                    ("legacy-1", "2026-07-15T00:00:00+00:00", decoy.pid, str(tmp_path / "x.log")),
                )
            assert get_run("legacy-1", config)["status"] == "orphaned"
            with pytest.raises(RunConflictError):
                terminate_run("legacy-1", config, grace_seconds=1.0)
            assert decoy.poll() is None, "legacy row must never be signaled"
        finally:
            decoy.kill()
            decoy.wait()

    def test_terminate_survives_exit_race_without_error(self, tmp_path):
        _install_fake_pipeline(tmp_path, FAKE_SLEEPER)
        config = _config(tmp_path)
        record = launch_run(RunRequest(date="2026-01-25"), config)
        try:

            def _gone(pgid, sig):
                raise ProcessLookupError(pgid)

            with pytest.MonkeyPatch.context() as patcher:
                patcher.setattr(os, "killpg", _gone)
                result = terminate_run(record["run_id"], config, grace_seconds=0.5)
            assert result["run_id"] == record["run_id"]
        finally:
            os.killpg(record["pid"], signal.SIGKILL)

    def test_child_env_excludes_control_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DSA110_CONTROL_TOKEN", "sekrit-test-token")
        body = (
            "import json, os, sys, pathlib\n"
            "pathlib.Path(sys.argv[sys.argv.index('--date') + 1] + '.env.json')"
            ".write_text(json.dumps({'token': os.environ.get('DSA110_CONTROL_TOKEN'),"
            " 'pythonpath': os.environ.get('PYTHONPATH')}))\n"
        )
        _install_fake_pipeline(tmp_path, body)
        config = _config(tmp_path)
        launch_run(RunRequest(date="2026-01-25"), config)
        marker = tmp_path / "2026-01-25.env.json"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.1)
        data = json.loads(marker.read_text())
        assert data["token"] is None, "control token must not leak into pipeline env"
        assert data["pythonpath"] == str(tmp_path)
