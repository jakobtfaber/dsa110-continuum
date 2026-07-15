"""Launch, track, and terminate batch_pipeline.py runs for the dashboard."""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
RFI_MODES = ("full", "conditional", "off")
CONTROL_TOKEN_ENV = "DSA110_CONTROL_TOKEN"


class RunConflictError(RuntimeError):
    """Another launcher-owned pipeline run is still alive."""


@dataclass(frozen=True)
class ControlConfig:
    """Locations and interpreter for launcher-owned pipeline runs."""

    repo_root: Path = field(
        default_factory=lambda: Path(os.environ.get("DSA110_REPO_ROOT", "/data/dsa110-continuum"))
    )
    python: str = field(
        default_factory=lambda: os.environ.get(
            "DSA110_PIPELINE_PYTHON", "/opt/miniforge/envs/casa6/bin/python"
        )
    )
    control_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("DSA110_CONTROL_DIR", "/data/dsa110-proc/products/control")
        )
    )

    @property
    def db_path(self) -> Path:
        """Path of the SQLite run registry."""
        return self.control_dir / "runs.sqlite3"


@dataclass(frozen=True)
class RunRequest:
    """Validated, allowlisted parameters for one batch_pipeline.py invocation."""

    date: str
    cal_date: str | None = None
    start_hour: int | None = None
    end_hour: int | None = None
    rfi_mode: str | None = None
    tile_timeout: int | None = None
    quarantine_after_failures: int | None = None
    photometry_workers: int | None = None
    retry_failed: bool = False
    force_recal: bool = False
    skip_photometry: bool = False
    lenient_qa: bool = False
    clear_quarantine: bool = False
    dry_run: bool = False

    def __post_init__(self) -> None:
        for label, value in (("date", self.date), ("cal_date", self.cal_date)):
            if value is None:
                continue
            if not DATE_RE.fullmatch(value):
                raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}")
            datetime.strptime(value, "%Y-%m-%d")
        for label, value in (("start_hour", self.start_hour), ("end_hour", self.end_hour)):
            if value is not None and not 0 <= value <= 23:
                raise ValueError(f"{label} must be 0-23, got {value}")
        if (
            self.start_hour is not None
            and self.end_hour is not None
            and self.start_hour > self.end_hour
        ):
            raise ValueError("start_hour must be <= end_hour")
        if self.rfi_mode is not None and self.rfi_mode not in RFI_MODES:
            raise ValueError(f"rfi_mode must be one of {RFI_MODES}, got {self.rfi_mode!r}")
        if self.tile_timeout is not None and not 60 <= self.tile_timeout <= 86400:
            raise ValueError("tile_timeout must be 60-86400 seconds")
        if self.quarantine_after_failures is not None and not (
            0 <= self.quarantine_after_failures <= 99
        ):
            raise ValueError("quarantine_after_failures must be 0-99")
        if self.photometry_workers is not None and not 1 <= self.photometry_workers <= 32:
            raise ValueError("photometry_workers must be 1-32")

    def to_argv(self, config: ControlConfig) -> list[str]:
        """Build the exact argv list; only allowlisted flags, never a shell string."""
        argv = [config.python, "scripts/batch_pipeline.py", "--date", self.date]
        value_flags = (
            ("--cal-date", self.cal_date),
            ("--start-hour", self.start_hour),
            ("--end-hour", self.end_hour),
            ("--rfi-mode", self.rfi_mode),
            ("--tile-timeout", self.tile_timeout),
            ("--quarantine-after-failures", self.quarantine_after_failures),
            ("--photometry-workers", self.photometry_workers),
        )
        for flag, value in value_flags:
            if value is not None:
                argv.extend([flag, str(value)])
        switch_flags = (
            ("--retry-failed", self.retry_failed),
            ("--force-recal", self.force_recal),
            ("--skip-photometry", self.skip_photometry),
            ("--lenient-qa", self.lenient_qa),
            ("--clear-quarantine", self.clear_quarantine),
            ("--dry-run", self.dry_run),
        )
        argv.extend(flag for flag, enabled in switch_flags if enabled)
        return argv

    def to_json(self) -> str:
        """Serialize every request field for the registry and reproducibility."""
        return json.dumps({f.name: getattr(self, f.name) for f in fields(self)})


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    request_json TEXT NOT NULL,
    argv_json TEXT NOT NULL,
    pid INTEGER NOT NULL,
    log_path TEXT NOT NULL,
    status TEXT NOT NULL,
    exit_code INTEGER,
    proc_start INTEGER
)
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(_SCHEMA)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    if "proc_start" not in columns:
        connection.execute("ALTER TABLE runs ADD COLUMN proc_start INTEGER")
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _proc_start_ticks(pid: int) -> int | None:
    """Start time (clock ticks since boot) of the current occupant of pid.

    Returns None when the pid is free or its stat is unreadable. A zombie is
    reported as None: it can no longer be signaled meaningfully and its group
    is gone or dying.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    tail = stat.rpartition(")")[2].split()
    try:
        if tail[0] == "Z":
            return None
        return int(tail[19])
    except (IndexError, ValueError):
        return None


def _run_process_alive(pid: int, expected_start: int | None) -> bool:
    """Report whether pid is still occupied by the same process this row launched.

    Rows without a recorded identity (legacy schema, or stat unreadable at
    launch) can never be verified, so they are never treated as alive — they
    reconcile to 'orphaned' and terminate refuses to signal them.
    """
    if expected_start is None:
        return False
    return _proc_start_ticks(pid) == expected_start


def _reconcile(row: dict) -> dict:
    if row["status"] == "running" and not _run_process_alive(row["pid"], row.get("proc_start")):
        row["status"] = "orphaned"
    return row


def _reap(db_path: Path, run_id: str, process: subprocess.Popen) -> None:
    exit_code = process.wait()
    status = "succeeded" if exit_code == 0 else "failed"
    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE runs SET status = ?, exit_code = ?, finished_at = ?"
            " WHERE run_id = ? AND status = 'running'",
            (status, exit_code, _now(), run_id),
        )


def _launch_env(config: ControlConfig) -> dict[str, str]:
    """Pipeline child env: repo on PYTHONPATH, control token never inherited."""
    environment = {**os.environ, "PYTHONPATH": str(config.repo_root)}
    environment.pop(CONTROL_TOKEN_ENV, None)
    return environment


def launch_run(request: RunRequest, config: ControlConfig) -> dict:
    """Start batch_pipeline.py detached in its own process group and register it.

    The single-flight guard, spawn, and registry insert run under an exclusive
    file lock so a concurrent launcher (dashboard click vs systemd timer)
    cannot both pass the guard.
    """
    config.control_dir.mkdir(parents=True, exist_ok=True)
    with (config.control_dir / "launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        live = [row for row in list_runs(config) if row["status"] == "running"]
        if live:
            raise RunConflictError(f"run {live[0]['run_id']} is still running")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        log_path = config.control_dir / f"run_{run_id}.log"
        argv = request.to_argv(config)
        with log_path.open("wb") as stream:
            process = subprocess.Popen(
                argv,
                cwd=config.repo_root,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=_launch_env(config),
            )
        record = {
            "run_id": run_id,
            "created_at": _now(),
            "finished_at": None,
            "request_json": request.to_json(),
            "argv_json": json.dumps(argv),
            "pid": process.pid,
            "log_path": str(log_path),
            "status": "running",
            "exit_code": None,
            "proc_start": _proc_start_ticks(process.pid),
        }
        with _connect(config.db_path) as connection:
            connection.execute(
                "INSERT INTO runs (run_id, created_at, finished_at, request_json, argv_json,"
                " pid, log_path, status, exit_code, proc_start)"
                " VALUES (:run_id, :created_at, :finished_at, :request_json, :argv_json,"
                " :pid, :log_path, :status, :exit_code, :proc_start)",
                record,
            )
    threading.Thread(target=_reap, args=(config.db_path, run_id, process), daemon=True).start()
    return record


def run_dry_run(request: RunRequest, config: ControlConfig, timeout: int = 120) -> str:
    """Run batch_pipeline.py --dry-run synchronously and return its output."""
    if not request.dry_run:
        request = RunRequest(**{**json.loads(request.to_json()), "dry_run": True})
    result = subprocess.run(
        request.to_argv(config),
        cwd=config.repo_root,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_launch_env(config),
    )
    return result.stdout + result.stderr


def list_runs(config: ControlConfig, limit: int = 50) -> list[dict]:
    """Return the newest registry rows, reconciling stale running statuses."""
    if not config.db_path.is_file():
        return []
    with _connect(config.db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_reconcile(dict(row)) for row in rows]


def get_run(run_id: str, config: ControlConfig) -> dict:
    """Return one registry row by run_id; raise KeyError when unknown."""
    with _connect(config.db_path) as connection:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    return _reconcile(dict(row))


def terminate_run(run_id: str, config: ControlConfig, grace_seconds: float = 10.0) -> dict:
    """SIGTERM the run's process group, escalate to SIGKILL after the grace period.

    Signals only after the pid's current occupant is verified to be the
    launched process (get_run reconciles identity mismatches to 'orphaned').
    A run that exits between the check and the signal is not an error.
    """
    row = get_run(run_id, config)
    if row["status"] != "running":
        raise RunConflictError(f"run {run_id} is not running (status={row['status']})")
    pgid = row["pid"]
    expected_start = row.get("proc_start")
    if not _run_process_alive(pgid, expected_start):
        raise RunConflictError(f"run {run_id} process identity unverified; refusing to signal")
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return get_run(run_id, config)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and _run_process_alive(pgid, expected_start):
        time.sleep(0.25)
    if _run_process_alive(pgid, expected_start):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    with _connect(config.db_path) as connection:
        connection.execute(
            "UPDATE runs SET status = 'terminated', finished_at = ? WHERE run_id = ?",
            (_now(), run_id),
        )
    return get_run(run_id, config)
