# Implementation Plan: Production-ready pipeline dashboard (monitoring + control)

---
**Date:** 2026-07-15
**Author:** AI Assistant (Claude Fable 5)
**Status:** Implemented on branch `dashboard-production` — awaiting user review (manual verification + deploy pending)
**Related Documents:**
- [Research: Production-ready pipeline dashboard](research-dashboard-production-readiness.md)
- Prior session: `outputs/observability-dashboard-2026-07-14/{DECISION,ARCHITECTURE,STATUS}.md`
- GitHub issues #48–#62 (dsa110/dsa110-continuum)

---

## Overview

This plan takes the DSA-110 continuum dashboard from an uncommitted read-only tracer-bullet to a
production dashboard that monitors the full imaging pipeline and lets the operator launch,
re-run, and terminate pipeline stages when automation fails. It resolves the orchestrator
question the user delegated ("investigate and determine"): **Dagster is kept only in its
existing read-only observability lane; it is not the control plane.** The control plane is a
small, auth-gated run-launcher inside the existing FastAPI dashboard (`scripts/qa_server.py`)
that drives `scripts/batch_pipeline.py` — the component that already owns checkpoint/resume,
quarantine, retry, per-hour windows, and dry-run semantics.

**Goal:** One dashboard origin (`:8767`) where the operator can watch every epoch's products,
QA verdicts, and light curves; preview a re-run with `--dry-run`; launch/terminate real runs
with structured, validated parameters; and where scheduled automation uses the same launcher
code path.

**Motivation:** The pipeline is meant to run automated with manual fallback; today there is no
automation trigger, no control surface, no auth, and the recent observability work is
uncommitted (research doc, Findings §5).

## Current State Analysis

**Existing Implementation:**
- `scripts/qa_server.py:1-597` — live routed FastAPI dashboard (uncommitted rewrite): mosaic
  artifact router (`:526-549`), `/api/status` (`:558-566`), server-rendered HTML with 30 s
  reload (`:510`), hardcoded `EPOCHS` (`:52-59`), `DashboardConfig` env-driven paths (`:32-49`).
- `dsa110_continuum/observability/hour_state.py:151-233` — read-only per-hour collector
  (uncommitted); `dagster_defs.py:80-190` — six assets + 1-min sensor on :3212 (uncommitted).
- `scripts/batch_pipeline.py:1217-1440` — CLI-only entrypoint; all control knobs are flags
  (`--date :1228`, `--cal-date :1229`, `--start-hour/--end-hour :1291-1304`,
  `--tile-timeout :1305`, `--retry-failed :1313`, `--force-recal :1320`, `--lenient-qa :1342`,
  `--dry-run :1375`, `--quarantine-after-failures :1386`, `--clear-quarantine :1397`,
  `--photometry-workers :1409`, `--rfi-mode :1273` choices `full|conditional|off :1275`,
  `--skip-photometry :1257`).
- Run truth: `{date}_manifest.json` (verified keys: `pipeline_verdict`, `gates[]` with
  `gate/verdict/reason`, `epochs[]` with `hour/n_tiles/status/mosaic_path/peak/rms/qa_result`,
  `tiles[]`, `git_sha`, `command_line`, `run_log`), `{date}_run_summary.json`, `run_report.md`
  under `/data/dsa110-proc/products/mosaics/{date}/` (writers:
  `dsa110_continuum/qa/provenance.py:248`, `scripts/batch_pipeline.py:2245`,
  `dsa110_continuum/qa/run_report.py:68`).
- Forced-photometry CSV schema (verified on disk):
  `ra_deg,dec_deg,nvss_flux_jy,dsa_peak_jyb,dsa_peak_err_jyb,dsa_nvss_ratio` at
  `products/mosaics/{date}/{date}T{HH}00_forced_phot.csv`.
- Variability metrics: `dsa110_continuum/photometry/metrics.py` (`calculate_eta_metric` — the
  VAST-canonical η at line 112, `calculate_v_metric`).
- Tests idiom: `tests/test_qa_server.py:15-110` — `DashboardConfig` over `tmp_path`,
  `TestClient(create_app(config))`, synthetic FITS/CSV, invariant-based classes.

**Current Limitations:** no control surface, no auth, no automation, hardcoded epochs, no
science-product (light-curve) view, uncommitted baseline (research doc, Findings §5).

## Desired End State

**New Behavior:**
- `GET /` shows auto-discovered recent epochs, active-run state, and a Pipeline-control panel.
- `POST /api/runs` (Bearer-token) validates a structured request, previews via `--dry-run`
  synchronously or launches `batch_pipeline.py` detached in its own process group, registered
  in a SQLite run registry with a per-run log.
- `POST /api/runs/{run_id}/terminate` (Bearer-token) kills the whole process group.
- `GET /runs/{date}` renders manifest gates/verdict/epochs + run report for any date.
- `GET /sources/lightcurve?ra=…&dec=…` renders a positional light curve across all epochs with
  η/V metrics.
- A systemd timer (unit files shipped, installation documented) launches the same code path on
  schedule; overlap is impossible (single-flight guard).

**Success Looks Like:**
- Operator can go from "hour 11 mosaic QA-FAIL" to "dry-run preview → re-run hour 11 with
  `--force-recal` → watch log tail → see new verdict" without leaving the browser.
- `make test-cloud PYTHON=/opt/miniforge/envs/casa6/bin/python` green; all new tests green.
- Nothing mutating is reachable without the token; token absent ⇒ control disabled (403).

## What We're NOT Doing

- [ ] Dagster as control plane (partitioned assets wrapping the CLI) — documented fallback
      only; the tracer-bullet on :3212 stays as-is, unmodified.
- [ ] Prefect/Airflow migration.
- [ ] Per-MS/per-tile/per-caltable deep QA views (#54–#56), stage-event contract (#52),
      full lifecycle-state badge taxonomy (#53 beyond epoch auto-discovery), CARTA/interactive
      FITS (#50), mosaic-on-demand mutating routes (#61), monitor_server retirement (#62 —
      blocked by #57). These are follow-up plans per-issue.
- [ ] Legacy stack teardown (old contimg Dagster host+Docker services, nginx fronts,
      lightcurve `http.server`) — needs its own inventory + user sign-off; out of scope here.
- [ ] WebSockets/SSE push; the 30 s reload + on-demand JSON stays (matches #48 provisional
      baseline "polling, lazy, single-process").
- [ ] Frontend framework rewrite; server-rendered HTML + minimal vanilla JS only.

**Rationale:** ship the monitoring+control core first; every deferred item layers onto the same
routed FastAPI substrate without rework.

## Implementation Approach

**Key Architectural Decisions:**
1. **Decision:** FastAPI unified server is the product; Dagster stays read-only.
   - **Rationale:** `batch_pipeline.py` already owns re-run semantics (checkpoint, quarantine,
     manifest-keyed epoch rebuild); wrapping it in Dagster partitions duplicates state and adds
     daemon ops burden + the grandchild-kill caveat (research doc, Prior Art). Observatory
     precedent (Simons, Keck) favors lighter control planes.
   - **Trade-offs:** we own a small run registry + reaper (~200 LOC) instead of getting a
     partition grid UI for free.
   - **Alternatives considered:** Dagster-as-control (rejected: two sources of truth, ops
     burden, retired-bridge history), Prefect (rejected: migration cost, no installed base).
2. **Decision:** Auth = pre-shared Bearer token (`DSA110_CONTROL_TOKEN`), fail-closed,
   constant-time compare, JSONL audit log; read-only routes stay open. Resolves #49 for the
   single-operator case; Cloudflare Access on the tunnel is documented optional hardening.
   - **Trade-offs:** no per-user identity; acceptable for one operator, revisit if that changes.
3. **Decision:** Control API accepts only a structured request and builds argv itself — no raw
   command strings anywhere (kills the `POST /exec` failure mode).
4. **Decision:** Launcher uses `start_new_session=True` (fresh process group) +
   `os.killpg` terminate + daemon reaper thread + pid-liveness reconciliation; registry is
   SQLite created inline (repo pattern: `calibration/jobs.py:50`).
5. **Decision:** Automation = systemd timer invoking `scripts/auto_pipeline.py`, which calls
   the same `launch_run()`; single-flight guard refuses overlapping runs.

**Patterns to Follow:**
- Config dataclass with env-default fields — `scripts/qa_server.py:32-49`,
  `dsa110_continuum/observability/hour_state.py:14-49`.
- Inline `CREATE TABLE IF NOT EXISTS` in the owning module — `calibration/jobs.py:50`.
- Test style — `tests/test_qa_server.py:15-110`.
- No ThreadPoolExecutor+SIGALRM (CLAUDE.md); the reaper thread only `wait()`s — no signals.

## Implementation Phases

Run all commands from `/data/dsa110-continuum` with
`PY=/opt/miniforge/envs/casa6/bin/python` and `PYTHONPATH=/data/dsa110-continuum`.

### Phase 0: Land the uncommitted observability baseline

**Objective:** the working-tree tracer-bullet (routed qa_server, observability package, tests)
becomes committed, lint-clean history — the foundation every later phase edits.

**Tasks:**
- [x] Run the affected tests, watch them pass (they exist and passed on 2026-07-14):
  `$PY -m pytest tests/test_qa_server.py tests/test_observability_hour_state.py tests/test_observability_mosaic_preview.py tests/test_mosaic_import_no_dagster.py -q` → 80 passed.
- [x] `ruff check` + `ruff format --check` on baseline files → clean.
- [x] Staged only in-scope files (unrelated working-tree modifications left unstaged).
- [x] Committed on branch `dashboard-production` (7183fe7).
- [ ] *(deferred to user — outward-facing)* Cross-reference issue #51 via `gh issue comment`.

**Verification:**
- [x] `git log --oneline -1` shows the commit; unrelated files remain unstaged as intended.
- [x] `make test-cloud PYTHON=$PY` → 200 passed, exit 0.

### Phase 1: Control module — validated requests, registry, launcher, terminate

**Objective:** `dsa110_continuum/observability/control.py`: a framework-free module that builds
safe argv from a structured request, launches `batch_pipeline.py` in its own process group,
tracks it in SQLite, reaps exit status, terminates process groups, and reports liveness.

**Tasks:**
- [x] **Failing test — request validation + argv building.** File: `tests/test_observability_control.py` (new)

  ```python
  import sys
  from pathlib import Path

  import pytest

  from dsa110_continuum.observability.control import ControlConfig, RunRequest


  def _config(tmp_path: Path) -> ControlConfig:
      return ControlConfig(
          repo_root=tmp_path,
          python=sys.executable,
          control_dir=tmp_path / "control",
      )


  class TestRunRequestValidation:
      def test_rejects_shell_metacharacters_in_date(self):
          for bad in ("2026-01-25; rm -rf /", "$(reboot)", "2026-01-25x", "..", ""):
              with pytest.raises(ValueError):
                  RunRequest(date=bad)

      def test_rejects_out_of_range_hours_and_bad_rfi_mode(self):
          with pytest.raises(ValueError):
              RunRequest(date="2026-01-25", start_hour=24)
          with pytest.raises(ValueError):
              RunRequest(date="2026-01-25", start_hour=5, end_hour=3)
          with pytest.raises(ValueError):
              RunRequest(date="2026-01-25", rfi_mode="sometimes")

      def test_argv_is_allowlisted_flags_only(self, tmp_path):
          request = RunRequest(
              date="2026-01-25", cal_date="2026-01-25", start_hour=22, end_hour=23,
              force_recal=True, retry_failed=True, lenient_qa=True,
              rfi_mode="conditional", quarantine_after_failures=3,
          )
          argv = request.to_argv(_config(tmp_path))
          assert argv[0] == sys.executable
          assert argv[1] == "scripts/batch_pipeline.py"
          assert "--date" in argv and "2026-01-25" in argv
          assert "--force-recal" in argv and "--retry-failed" in argv
          assert "--rfi-mode" in argv and "conditional" in argv
          joined = " ".join(argv)
          assert ";" not in joined and "$(" not in joined
  ```

- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_observability_control.py -q` → FAIL (module missing).
- [x] **Implement request + config.** File: `dsa110_continuum/observability/control.py` (new)

  ```python
  """Launch, track, and terminate batch_pipeline.py runs for the dashboard."""

  from __future__ import annotations

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


  class RunConflictError(RuntimeError):
      """Another launcher-owned pipeline run is still alive."""


  @dataclass(frozen=True)
  class ControlConfig:
      repo_root: Path = field(
          default_factory=lambda: Path(
              os.environ.get("DSA110_REPO_ROOT", "/data/dsa110-continuum")
          )
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
          return self.control_dir / "runs.sqlite3"


  @dataclass(frozen=True)
  class RunRequest:
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
          return json.dumps({f.name: getattr(self, f.name) for f in fields(self)})
  ```

- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_observability_control.py -q` → PASS.
- [x] **Commit:** `git commit -am "Add validated RunRequest and argv builder for pipeline control"`
- [x] **Failing test — launch, registry, reaper.** Append to `tests/test_observability_control.py`:

  ```python
  import time

  from dsa110_continuum.observability.control import (
      RunConflictError, get_run, launch_run, list_runs, run_dry_run, terminate_run,
  )

  FAKE_OK = "import sys; print('plan ok', ' '.join(sys.argv[1:])); sys.exit(0)\n"
  FAKE_SLEEPER = (
      "import subprocess, sys, time, pathlib\n"
      "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
      "pathlib.Path(sys.argv[sys.argv.index('--date') + 1] + '.childpid').write_text(str(child.pid))\n"
      "time.sleep(120)\n"
  )


  def _install_fake_pipeline(tmp_path: Path, body: str) -> None:
      scripts = tmp_path / "scripts"
      scripts.mkdir(exist_ok=True)
      (scripts / "batch_pipeline.py").write_text(body)


  class TestLaunchAndReap:
      def test_launch_registers_run_and_reaper_marks_succeeded(self, tmp_path):
          _install_fake_pipeline(tmp_path, FAKE_OK)
          config = _config(tmp_path)
          record = launch_run(RunRequest(date="2026-01-25"), config)
          assert record["status"] == "running" and record["pid"] > 0
          deadline = time.monotonic() + 15
          while time.monotonic() < deadline:
              row = get_run(record["run_id"], config)
              if row["status"] != "running":
                  break
              time.sleep(0.2)
          assert row["status"] == "succeeded" and row["exit_code"] == 0
          assert "plan ok" in Path(row["log_path"]).read_text()

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
          child_pid = int(childpid_file.read_text())
          terminate_run(record["run_id"], config, grace_seconds=2.0)
          row = get_run(record["run_id"], config)
          assert row["status"] == "terminated"
          time.sleep(0.5)
          assert not Path(f"/proc/{record['pid']}").exists()
          assert not Path(f"/proc/{child_pid}").exists()

      def test_dry_run_returns_plan_text_and_registers_nothing(self, tmp_path):
          _install_fake_pipeline(tmp_path, FAKE_OK)
          config = _config(tmp_path)
          output = run_dry_run(RunRequest(date="2026-01-25", dry_run=True), config)
          assert "plan ok" in output and "--dry-run" in output
          assert list_runs(config) == []
  ```

- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_observability_control.py -q` → FAIL (functions missing).
- [x] **Implement launcher/registry/reaper/terminate.** Append to `dsa110_continuum/observability/control.py`:

  ```python
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
      exit_code INTEGER
  )
  """


  def _connect(db_path: Path) -> sqlite3.Connection:
      connection = sqlite3.connect(db_path, timeout=10)
      connection.row_factory = sqlite3.Row
      connection.execute(_SCHEMA)
      return connection


  def _now() -> str:
      return datetime.now(timezone.utc).isoformat()


  def _pid_alive(pid: int) -> bool:
      return Path(f"/proc/{pid}").exists()


  def _reconcile(row: dict) -> dict:
      if row["status"] == "running" and not _pid_alive(row["pid"]):
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


  def launch_run(request: RunRequest, config: ControlConfig) -> dict:
      config.control_dir.mkdir(parents=True, exist_ok=True)
      live = [row for row in list_runs(config) if row["status"] == "running"]
      if live:
          raise RunConflictError(f"run {live[0]['run_id']} is still running")
      run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
      log_path = config.control_dir / f"run_{run_id}.log"
      argv = request.to_argv(config)
      environment = {**os.environ, "PYTHONPATH": str(config.repo_root)}
      with log_path.open("wb") as stream:
          process = subprocess.Popen(
              argv,
              cwd=config.repo_root,
              stdout=stream,
              stderr=subprocess.STDOUT,
              start_new_session=True,
              env=environment,
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
      }
      with _connect(config.db_path) as connection:
          connection.execute(
              "INSERT INTO runs VALUES (:run_id, :created_at, :finished_at, :request_json,"
              " :argv_json, :pid, :log_path, :status, :exit_code)",
              record,
          )
      threading.Thread(
          target=_reap, args=(config.db_path, run_id, process), daemon=True
      ).start()
      return record


  def run_dry_run(request: RunRequest, config: ControlConfig, timeout: int = 120) -> str:
      if not request.dry_run:
          request = RunRequest(**{**json.loads(request.to_json()), "dry_run": True})
      result = subprocess.run(
          request.to_argv(config),
          cwd=config.repo_root,
          capture_output=True,
          text=True,
          timeout=timeout,
          env={**os.environ, "PYTHONPATH": str(config.repo_root)},
      )
      return result.stdout + result.stderr


  def list_runs(config: ControlConfig, limit: int = 50) -> list[dict]:
      if not config.db_path.is_file():
          return []
      with _connect(config.db_path) as connection:
          rows = connection.execute(
              "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
          ).fetchall()
      return [_reconcile(dict(row)) for row in rows]


  def get_run(run_id: str, config: ControlConfig) -> dict:
      with _connect(config.db_path) as connection:
          row = connection.execute(
              "SELECT * FROM runs WHERE run_id = ?", (run_id,)
          ).fetchone()
      if row is None:
          raise KeyError(run_id)
      return _reconcile(dict(row))


  def terminate_run(run_id: str, config: ControlConfig, grace_seconds: float = 10.0) -> dict:
      row = get_run(run_id, config)
      if row["status"] != "running":
          raise RunConflictError(f"run {run_id} is not running (status={row['status']})")
      pgid = row["pid"]
      os.killpg(pgid, signal.SIGTERM)
      deadline = time.monotonic() + grace_seconds
      while time.monotonic() < deadline and _pid_alive(pgid):
          time.sleep(0.25)
      if _pid_alive(pgid):
          os.killpg(pgid, signal.SIGKILL)
      with _connect(config.db_path) as connection:
          connection.execute(
              "UPDATE runs SET status = 'terminated', finished_at = ? WHERE run_id = ?",
              (_now(), run_id),
          )
      return get_run(run_id, config)
  ```

- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_observability_control.py -q` → PASS (all classes).
- [x] `ruff check dsa110_continuum/observability/control.py tests/test_observability_control.py` → clean.
- [x] **Commit:** `git commit -am "Add pipeline run launcher, registry, reaper, and group terminate"`

**Dependencies:** Phase 0.

**Verification:**
- [x] `$PY -m pytest tests/test_observability_control.py -q` → all pass, < 60 s.
- [x] `$PY -c "from dsa110_continuum.observability.control import launch_run"` → no import error, no Dagster import triggered (`$PY -c "import sys; import dsa110_continuum.observability.control; assert 'dagster' not in sys.modules"`).

### Phase 2: Auth + `/api/runs` router + audit log in qa_server

**Objective:** the control module becomes an authenticated HTTP surface on the existing
dashboard app; every mutating request is audited; token absent ⇒ fail closed.

**Tasks:**
- [x] **Failing test — auth posture.** Append to `tests/test_qa_server.py`:

  ```python
  class TestControlAuth:
      """Mutating control routes must fail closed: no token env => 403 always;
      wrong/missing header => 403; correct bearer => accepted."""

      def _client(self, tmp_path, monkeypatch, token_env):
          if token_env is None:
              monkeypatch.delenv("DSA110_CONTROL_TOKEN", raising=False)
          else:
              monkeypatch.setenv("DSA110_CONTROL_TOKEN", token_env)
          monkeypatch.setenv("DSA110_CONTROL_DIR", str(tmp_path / "control"))
          monkeypatch.setenv("DSA110_REPO_ROOT", str(tmp_path))
          import sys as _sys
          monkeypatch.setenv("DSA110_PIPELINE_PYTHON", _sys.executable)
          scripts = tmp_path / "scripts"
          scripts.mkdir(exist_ok=True)
          (scripts / "batch_pipeline.py").write_text("print('plan ok')\n")
          return TestClient(create_app(_make_config(tmp_path)))

      def test_launch_without_token_env_is_403_even_with_header(self, tmp_path, monkeypatch):
          with self._client(tmp_path, monkeypatch, token_env=None) as client:
              response = client.post(
                  "/api/runs",
                  json={"date": "2026-01-25", "dry_run": True},
                  headers={"Authorization": "Bearer anything"},
              )
          assert response.status_code == 403

      def test_wrong_token_is_403_and_not_audited_as_launch(self, tmp_path, monkeypatch):
          with self._client(tmp_path, monkeypatch, token_env="s3cret") as client:
              response = client.post(
                  "/api/runs",
                  json={"date": "2026-01-25", "dry_run": True},
                  headers={"Authorization": "Bearer wrong"},
              )
          assert response.status_code == 403

      def test_dry_run_with_token_returns_plan_and_audits(self, tmp_path, monkeypatch):
          with self._client(tmp_path, monkeypatch, token_env="s3cret") as client:
              response = client.post(
                  "/api/runs",
                  json={"date": "2026-01-25", "dry_run": True},
                  headers={"Authorization": "Bearer s3cret"},
              )
          assert response.status_code == 200
          assert "plan ok" in response.json()["plan"]
          audit = (tmp_path / "control" / "audit.jsonl").read_text()
          assert '"dry_run": true' in audit and "s3cret" not in audit

      def test_injection_shaped_date_is_422(self, tmp_path, monkeypatch):
          with self._client(tmp_path, monkeypatch, token_env="s3cret") as client:
              response = client.post(
                  "/api/runs",
                  json={"date": "2026-01-25; rm -rf /", "dry_run": True},
                  headers={"Authorization": "Bearer s3cret"},
              )
          assert response.status_code in (400, 422)

      def test_run_listing_is_readable_without_token(self, tmp_path, monkeypatch):
          with self._client(tmp_path, monkeypatch, token_env="s3cret") as client:
              response = client.get("/api/runs")
          assert response.status_code == 200
          assert response.json() == {"runs": []}
  ```

- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_qa_server.py::TestControlAuth -q` → FAIL (404 route).
- [x] **Implement router.** Edit `scripts/qa_server.py`. Add imports near the top (after line 24):

  ```python
  import secrets

  from pydantic import BaseModel

  from dsa110_continuum.observability import control as pipeline_control
  ```

  Add after `ops_router` definition (`scripts/qa_server.py:527`):

  ```python
  control_router = APIRouter(prefix="/api/runs", tags=["pipeline control"])
  CONTROL_TOKEN_ENV = "DSA110_CONTROL_TOKEN"


  class RunRequestBody(BaseModel):
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


  def _require_control_token(request: Request) -> None:
      expected = os.environ.get(CONTROL_TOKEN_ENV, "")
      provided = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
      if not expected or not secrets.compare_digest(provided, expected):
          raise HTTPException(status_code=403, detail="control token missing or invalid")


  def _audit(config: pipeline_control.ControlConfig, action: str, request: Request, payload: dict) -> None:
      config.control_dir.mkdir(parents=True, exist_ok=True)
      entry = {
          "time": datetime.now(timezone.utc).isoformat(),
          "action": action,
          "remote": request.client.host if request.client else None,
          "payload": payload,
      }
      with (config.control_dir / "audit.jsonl").open("a") as stream:
          stream.write(json.dumps(entry) + "\n")


  @control_router.get("")
  def control_list_runs():
      return {"runs": pipeline_control.list_runs(pipeline_control.ControlConfig())}


  @control_router.get("/{run_id}")
  def control_get_run(run_id: str, tail: int = 40):
      config = pipeline_control.ControlConfig()
      try:
          record = pipeline_control.get_run(run_id, config)
      except KeyError:
          raise HTTPException(status_code=404, detail="unknown run") from None
      record["log_tail"] = _tail(Path(record["log_path"]), tail)
      return record


  @control_router.post("")
  def control_launch(body: RunRequestBody, request: Request):
      _require_control_token(request)
      config = pipeline_control.ControlConfig()
      try:
          run_request = pipeline_control.RunRequest(**body.model_dump())
      except ValueError as exc:
          raise HTTPException(status_code=400, detail=str(exc)) from None
      _audit(config, "dry_run" if body.dry_run else "launch", request, body.model_dump())
      if body.dry_run:
          try:
              plan = pipeline_control.run_dry_run(run_request, config)
          except subprocess.TimeoutExpired:
              raise HTTPException(status_code=504, detail="dry-run timed out") from None
          return {"plan": plan}
      try:
          return pipeline_control.launch_run(run_request, config)
      except pipeline_control.RunConflictError as exc:
          raise HTTPException(status_code=409, detail=str(exc)) from None


  @control_router.post("/{run_id}/terminate")
  def control_terminate(run_id: str, request: Request):
      _require_control_token(request)
      config = pipeline_control.ControlConfig()
      _audit(config, "terminate", request, {"run_id": run_id})
      try:
          return pipeline_control.terminate_run(run_id, config)
      except KeyError:
          raise HTTPException(status_code=404, detail="unknown run") from None
      except pipeline_control.RunConflictError as exc:
          raise HTTPException(status_code=409, detail=str(exc)) from None
  ```

  Add `import json` to the stdlib import block (it is not imported today), and register the
  router in `create_app` after `application.include_router(ops_router)`
  (`scripts/qa_server.py:576`):

  ```python
      application.include_router(control_router)
  ```

- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_qa_server.py::TestControlAuth -q` → PASS.
- [x] Regression: `$PY -m pytest tests/test_qa_server.py -q` → all pass (existing suites unaffected).
- [x] `ruff check scripts/qa_server.py` → clean for new code.
- [x] **Commit:** `git commit -am "Add auth-gated pipeline control API with audit log"`

**Dependencies:** Phase 1.

**Verification:**
- [x] `$PY -m pytest tests/test_qa_server.py tests/test_observability_control.py -q` → pass.
- [ ] Manual smoke on H17 (token set): `curl -s -X POST -H "Authorization: Bearer $DSA110_CONTROL_TOKEN" -H 'Content-Type: application/json' -d '{"date":"2026-01-25","start_hour":22,"end_hour":23,"dry_run":true}' http://127.0.0.1:8767/api/runs | head` → JSON with the real dry-run plan text.

### Phase 3: Dashboard UI — control panel, run pages, epoch auto-discovery

**Objective:** the operator can see and drive runs from the browser, and the historical QA
table stops being hardcoded to six epochs.

**Tasks:**
- [x] **Failing test — auto-discovery.** Append to `tests/test_qa_server.py`:

  ```python
  class TestEpochDiscovery:
      def test_discovers_epochs_from_stage_newest_first(self, tmp_path):
          config = _make_config(tmp_path)
          old = _write_mosaic(config, "2026-01-25", "T0200", np.ones((4, 4), np.float32))
          new = _write_mosaic(config, "2026-07-13", "T1100", np.ones((4, 4), np.float32))
          os.utime(old, (1, 1))
          discovered = qa_server.discover_epochs(config)
          assert discovered[0] == ("2026-07-13", "T1100")
          assert ("2026-01-25", "T0200") in discovered

      def test_falls_back_to_static_epochs_when_stage_empty(self, tmp_path):
          assert qa_server.discover_epochs(_make_config(tmp_path)) == qa_server.EPOCHS

      def test_ignores_malformed_names(self, tmp_path):
          config = _make_config(tmp_path)
          directory = config.stage / "images" / "mosaic_2026-01-25"
          directory.mkdir(parents=True)
          (directory / "junk_mosaic.fits").write_bytes(b"x")
          assert qa_server.discover_epochs(config) == qa_server.EPOCHS
  ```

- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_qa_server.py::TestEpochDiscovery -q` → FAIL.
- [x] **Implement discovery.** Add to `scripts/qa_server.py` after `find_csv` (`:88`):

  ```python
  MOSAIC_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(T\d{4})_mosaic\.fits")


  def discover_epochs(config: DashboardConfig, limit: int = 24) -> list[tuple[str, str]]:
      """List (date, epoch) for every hourly-epoch mosaic on stage, newest first."""
      root = config.stage / "images"
      found = []
      if root.is_dir():
          for path in root.glob("mosaic_*/*_mosaic.fits"):
              match = MOSAIC_NAME_RE.fullmatch(path.name)
              if match:
                  found.append((path.stat().st_mtime, match.group(1), match.group(2)))
      found.sort(reverse=True)
      discovered = [(date, epoch) for _, date, epoch in found[:limit]]
      return discovered or EPOCHS
  ```

  In `render_dashboard` replace `for date, epoch in EPOCHS` (`:409`) with
  `for date, epoch in discover_epochs(config)` and the `len(EPOCHS)` total (`:521`) with the
  discovered count.
- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_qa_server.py::TestEpochDiscovery -q` → PASS.
- [x] **Failing test — control panel + run page rendering.** Append to `tests/test_qa_server.py`:

  ```python
  class TestControlUi:
      def test_dashboard_shows_pipeline_control_panel(self, tmp_path):
          with TestClient(create_app(_make_config(tmp_path))) as client:
              page = client.get("/").text
          assert "Pipeline control" in page
          assert 'id="run-form"' in page and "/api/runs" in page

      def test_run_detail_page_renders_log_tail(self, tmp_path, monkeypatch):
          monkeypatch.setenv("DSA110_CONTROL_DIR", str(tmp_path / "control"))
          import sys as _sys
          monkeypatch.setenv("DSA110_PIPELINE_PYTHON", _sys.executable)
          monkeypatch.setenv("DSA110_REPO_ROOT", str(tmp_path))
          (tmp_path / "scripts").mkdir()
          (tmp_path / "scripts" / "batch_pipeline.py").write_text("print('hello from run')\n")
          from dsa110_continuum.observability.control import ControlConfig, RunRequest, launch_run
          record = launch_run(RunRequest(date="2026-01-25"), ControlConfig(
              repo_root=tmp_path, python=_sys.executable, control_dir=tmp_path / "control"))
          import time as _time
          _time.sleep(1.0)
          with TestClient(create_app(_make_config(tmp_path))) as client:
              page = client.get(f"/control/runs/{record['run_id']}")
          assert page.status_code == 200
          assert "hello from run" in page.text
  ```

- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_qa_server.py::TestControlUi -q` → FAIL.
- [x] **Implement UI.** In `scripts/qa_server.py`:
  1. Add a `control_page_router = APIRouter(include_in_schema=False)` with
     `GET /control/runs/{run_id}` returning an HTML page: run metadata table (status badge via
     `_badge`, argv, created/finished, exit code) + `<pre>` log tail via
     `_tail(Path(record["log_path"]), 200)`, HTML-escaped, with the same 30 s reload script.
     Register it in `create_app`.
  2. In `render_dashboard`, insert a `Pipeline control` section between the campaign section
     and `Historical hourly-epoch QA` containing:
     - a runs table from `pipeline_control.list_runs(pipeline_control.ControlConfig(), limit=10)`
       (run id linked to `/control/runs/{run_id}`, status badge, created_at, exit code, and a
       Terminate button for `running` rows);
     - a `<form id="run-form">` with inputs: date (required, `pattern="\d{4}-\d{2}-\d{2}"`),
       cal-date, start/end hour (`type=number min=0 max=23`), selects for rfi-mode
       (blank/full/conditional/off), checkboxes (force-recal, retry-failed, skip-photometry,
       lenient-qa, clear-quarantine), a password-type token field, and two buttons:
       **Dry-run preview** and **Launch**;
     - an empty `<pre id="run-output"></pre>`;
     - an inline `<script>` that serializes the form to the `/api/runs` JSON body, sends
       `fetch("/api/runs", {method:"POST", headers:{"Content-Type":"application/json",
       "Authorization":"Bearer "+token}, body})` with `dry_run` true/false per button,
       writes the response (plan text or run record or error detail) into `#run-output`,
       and wires each Terminate button to `POST /api/runs/{id}/terminate` with the same
       token header. Suspend the 30 s auto-reload while the token field is non-empty
       (`if(!document.getElementById("control-token").value) location.reload()`), so form
       state is not lost mid-entry.
- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_qa_server.py::TestControlUi -q` → PASS.
- [x] Full-file regression + lint: `$PY -m pytest tests/test_qa_server.py -q && ruff check scripts/qa_server.py` → pass/clean.
- [x] **Commit:** `git commit -am "Add pipeline control panel, run pages, and epoch auto-discovery"`

**Dependencies:** Phase 2.

**Verification:**
- [x] `$PY -m pytest tests/test_qa_server.py -q` → pass.
- [ ] Manual: open `http://lxd110h17:8767/`, confirm discovered epochs (2026-07-13T1100 present), control panel renders, dry-run preview round-trips.

### Phase 4: Run provenance page `/runs/{date}` (#60 minimal)

**Objective:** surface manifest verdict, gates, epochs, and the run report for any date —
the "why did QA fail" page.

**Tasks:**
- [x] **Failing test.** Append to `tests/test_qa_server.py`:

  ```python
  class TestRunProvenancePage:
      def _write_manifest(self, config, date="2026-01-25"):
          directory = config.products / date
          directory.mkdir(parents=True, exist_ok=True)
          manifest = {
              "date": date, "cal_date": date, "git_sha": "abc1234",
              "command_line": "batch_pipeline.py --date " + date,
              "pipeline_verdict": "DEGRADED",
              "gates": [{"gate": "epoch_qa:22", "verdict": "FAIL",
                         "reason": "catalog completeness 0.42 < 0.5"}],
              "epochs": [{"hour": 22, "n_tiles": 11, "status": "ok", "qa_result": "FAIL",
                          "peak": 12.5, "rms": 0.008, "mosaic_path": "/x.fits",
                          "gaincal_status": "ok", "n_sources": 40, "median_ratio": 0.9,
                          "weight_path": "/x.w.fits"}],
              "tiles": [], "run_log": "run_x.log",
          }
          (directory / f"{date}_manifest.json").write_text(json.dumps(manifest))
          (directory / "run_report.md").write_text("# Run report\nverdict DEGRADED\n")

      def test_renders_verdict_gates_and_epochs(self, tmp_path):
          config = _make_config(tmp_path)
          self._write_manifest(config)
          with TestClient(create_app(config)) as client:
              page = client.get("/runs/2026-01-25")
          assert page.status_code == 200
          assert "DEGRADED" in page.text
          assert "catalog completeness" in page.text
          assert "T2200" in page.text or ">22<" in page.text

      def test_missing_manifest_is_404_and_traversal_rejected(self, tmp_path):
          with TestClient(create_app(_make_config(tmp_path))) as client:
              assert client.get("/runs/2026-01-26").status_code == 404
              assert 400 <= client.get("/runs/..%2f..%2fetc").status_code < 500
  ```

  (add `import json` to the test-file imports if not already present)
- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_qa_server.py::TestRunProvenancePage -q` → FAIL.
- [x] **Implement.** In `scripts/qa_server.py` add `GET /runs/{date}` to a `runs_page_router`
  (registered in `create_app`): `_validate_date_epoch(date)`; load
  `config.products / date / f"{date}_manifest.json"` (404 if absent); render an HTML page with
  the same stylesheet: header (verdict badge — `pass`-green for CLEAN, `fail`-red otherwise —
  date, cal_date, git_sha, command line), a gates table (`gate/verdict/reason`), an epochs
  table (hour, n_tiles, status, qa_result badge, peak, rms, n_sources, median_ratio, link to
  `/artifacts/mosaic/{date}/T{hour:02d}00/status`), and the run report `run_report.md`
  rendered inside `<pre>` (HTML-escaped). Missing keys render as `—` via `_format_number` /
  `dict.get` — the page must never 500 on a partial manifest (guard every field access).
  Link each dashboard historical-table row's date cell to `/runs/{date}`.
- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_qa_server.py::TestRunProvenancePage -q` → PASS.
- [x] **Commit:** `git commit -am "Add per-date run provenance page with gates and verdicts"`

**Dependencies:** Phase 3 (stylesheet/section helpers), but functionally independent of control.

**Verification:**
- [ ] Manual on H17: `http://lxd110h17:8767/runs/2026-07-13` shows the real DEGRADED manifest
      (gates + hour-11 epoch row) — the validated 2026-01-25 hour-22 case in CLAUDE.md also renders.

### Phase 5: Positional light-curve view (#59 minimal)

**Objective:** the primary science surface — a light curve for any sky position across all
epochs with per-point provenance and η/V variability metrics.

**Tasks:**
- [x] **Failing test.** Append to `tests/test_qa_server.py`:

  ```python
  class TestLightcurveView:
      HEADER = "ra_deg,dec_deg,nvss_flux_jy,dsa_peak_jyb,dsa_peak_err_jyb,dsa_nvss_ratio\n"

      def _write_epoch_csv(self, config, date, epoch, flux):
          directory = config.products / date
          directory.mkdir(parents=True, exist_ok=True)
          (directory / f"{date}{epoch}_forced_phot.csv").write_text(
              self.HEADER + f"47.499,17.099833,4.87,{flux},0.02,0.95\n"
              + f"120.0,-5.0,1.0,1.1,0.05,1.1\n"
          )

      def test_matches_position_across_epochs_and_computes_metrics(self, tmp_path):
          config = _make_config(tmp_path)
          self._write_epoch_csv(config, "2026-01-25", "T0200", 3.9)
          self._write_epoch_csv(config, "2026-02-12", "T0000", 4.1)
          points = qa_server.lightcurve_points(config, ra_deg=47.499, dec_deg=17.0998,
                                               radius_arcsec=30.0)
          assert len(points) == 2
          assert {point["epoch"] for point in points} == {"2026-01-25T0200", "2026-02-12T0000"}
          assert all(abs(point["flux_jy"] - 4.0) < 0.2 for point in points)

      def test_no_match_outside_radius(self, tmp_path):
          config = _make_config(tmp_path)
          self._write_epoch_csv(config, "2026-01-25", "T0200", 3.9)
          assert qa_server.lightcurve_points(config, 47.499, 18.5, 30.0) == []

      def test_lightcurve_page_and_png(self, tmp_path):
          config = _make_config(tmp_path)
          self._write_epoch_csv(config, "2026-01-25", "T0200", 3.9)
          self._write_epoch_csv(config, "2026-02-12", "T0000", 4.1)
          with TestClient(create_app(config)) as client:
              page = client.get("/sources/lightcurve?ra=47.499&dec=17.0998")
              png = client.get("/sources/lightcurve.png?ra=47.499&dec=17.0998")
          assert page.status_code == 200 and "2026-01-25" in page.text
          assert "η" in page.text or "eta" in page.text.lower()
          assert png.status_code == 200 and png.content.startswith(b"\x89PNG")

      def test_bad_coords_rejected(self, tmp_path):
          with TestClient(create_app(_make_config(tmp_path))) as client:
              assert 400 <= client.get("/sources/lightcurve?ra=999&dec=0").status_code < 500
  ```

- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_qa_server.py::TestLightcurveView -q` → FAIL.
- [x] **Implement.** In `scripts/qa_server.py`:

  ```python
  PHOT_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(T\d{4})_forced_phot\.csv")


  def lightcurve_points(
      config: DashboardConfig, ra_deg: float, dec_deg: float, radius_arcsec: float = 30.0
  ) -> list[dict]:
      """Nearest forced-photometry match per epoch within the match radius."""
      points = []
      if not config.products.is_dir():
          return points
      radius_deg = radius_arcsec / 3600.0
      cos_dec = max(np.cos(np.radians(dec_deg)), 1e-6)
      for csv_path in sorted(config.products.glob("*/*_forced_phot.csv")):
          match = PHOT_NAME_RE.fullmatch(csv_path.name)
          if not match:
              continue
          try:
              frame = pd.read_csv(csv_path)
          except Exception as exc:
              logger.warning("Lightcurve CSV error %s: %s", csv_path, exc)
              continue
          if not {"ra_deg", "dec_deg", "dsa_peak_jyb"}.issubset(frame.columns):
              continue
          separation = np.hypot(
              (frame["ra_deg"] - ra_deg) * cos_dec, frame["dec_deg"] - dec_deg
          )
          index = separation.idxmin() if len(separation) else None
          if index is None or separation[index] > radius_deg:
              continue
          row = frame.loc[index]
          points.append(
              {
                  "epoch": match.group(1) + match.group(2),
                  "date": match.group(1),
                  "epoch_token": match.group(2),
                  "flux_jy": float(row["dsa_peak_jyb"]),
                  "flux_err_jy": float(row.get("dsa_peak_err_jyb", np.nan)),
                  "separation_arcsec": float(separation[index] * 3600.0),
                  "csv": str(csv_path),
              }
          )
      points.sort(key=lambda point: point["epoch"])
      return points
  ```

  Routes on a `sources_router = APIRouter(prefix="/sources", tags=["science sources"])`:
  - `GET /sources/lightcurve?ra=&dec=&radius_arcsec=30` — validate `0 <= ra < 360`,
    `-90 <= dec <= 90`, `1 <= radius_arcsec <= 300` (else 400); render HTML: points table
    (epoch linked to `/artifacts/mosaic/{date}/{epoch_token}/status` — per-point click-through
    to the contributing mosaic), `<img>` of the PNG route, and a metrics block computing
    η and V from the points via
    `from dsa110_continuum.photometry.metrics import calculate_eta_metric, calculate_v_metric`
    (function-scope import; guard `len(points) >= 2`, else show "insufficient epochs").
  - `GET /sources/lightcurve.png?...` — matplotlib errorbar plot of flux vs epoch label
    (Agg, same dark style as `make_thumbnail`), 404 when no points.
- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_qa_server.py::TestLightcurveView -q` → PASS.
- [x] Add a dashboard entry point: a small "Light curve lookup" form (ra/dec inputs, GET) in
  the dashboard HTML linking to `/sources/lightcurve`. Assert in the existing
  `test_dashboard_shows_pipeline_control_panel`-style test:
  `assert "/sources/lightcurve" in page`.
- [x] Full regression + lint; **Commit:** `git commit -am "Add positional light-curve science view with variability metrics"`

**Dependencies:** Phase 3 (HTML helpers); independent of control routes.

**Verification:**
- [ ] Manual on H17: `http://lxd110h17:8767/sources/lightcurve?ra=47.499&dec=17.0998` shows the
      3C48-field source across the 2026-01/02 epochs with sensible fluxes and η/V values.

### Phase 6: Automation trigger + service hardening

**Objective:** the pipeline runs unattended through the same launcher path; both services are
supervised; operational posture documented.

**Tasks:**
- [x] **Failing test — auto-launch decision logic.** File: `tests/test_auto_pipeline.py` (new)

  ```python
  import sys
  from pathlib import Path

  from dsa110_continuum.observability.control import ControlConfig
  from scripts.auto_pipeline import decide_and_launch


  def _config(tmp_path: Path) -> ControlConfig:
      scripts = tmp_path / "scripts"
      scripts.mkdir()
      (scripts / "batch_pipeline.py").write_text("print('auto ok')\n")
      return ControlConfig(repo_root=tmp_path, python=sys.executable,
                           control_dir=tmp_path / "control")


  def test_launches_for_given_date_and_reports_run_id(self, tmp_path=None):
      pass  # replaced below — pytest functions take tmp_path fixture directly


  def test_launch_and_conflict(tmp_path):
      config = _config(tmp_path)
      outcome = decide_and_launch(date="2026-01-25", config=config)
      assert outcome["action"] == "launched" and outcome["run_id"]
      import time
      time.sleep(1.0)
      second = decide_and_launch(date="2026-01-25", config=config)
      assert second["action"] in ("launched", "skipped_running")
  ```

- [x] **Run it, watch it fail:** `$PY -m pytest tests/test_auto_pipeline.py -q` → FAIL.
- [x] **Implement.** File: `scripts/auto_pipeline.py` (new)

  ```python
  """Scheduled entrypoint: launch today's batch pipeline via the control registry."""

  from __future__ import annotations

  import argparse
  import json
  import sys
  from datetime import datetime, timedelta, timezone
  from pathlib import Path

  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

  from dsa110_continuum.observability.control import (
      ControlConfig, RunConflictError, RunRequest, launch_run,
  )


  def decide_and_launch(date: str, config: ControlConfig | None = None) -> dict:
      config = config or ControlConfig()
      request = RunRequest(
          date=date, retry_failed=True, quarantine_after_failures=3, photometry_workers=4
      )
      try:
          record = launch_run(request, config)
      except RunConflictError as exc:
          return {"action": "skipped_running", "detail": str(exc)}
      return {"action": "launched", "run_id": record["run_id"], "pid": record["pid"]}


  def main() -> int:
      parser = argparse.ArgumentParser(description=__doc__)
      parser.add_argument(
          "--date",
          default=(datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d"),
          help="Observation date to process (default: yesterday UTC).",
      )
      arguments = parser.parse_args()
      outcome = decide_and_launch(arguments.date)
      print(json.dumps(outcome))
      return 0


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [x] **Run it, watch it pass:** `$PY -m pytest tests/test_auto_pipeline.py -q` → PASS.
- [x] **Ship service units.** Files: `ops/systemd/dsa110-dashboard.service`,
  `ops/systemd/dsa110-autopipeline.service`, `ops/systemd/dsa110-autopipeline.timer` (new):

  ```ini
  # ops/systemd/dsa110-dashboard.service
  [Unit]
  Description=DSA-110 continuum QA dashboard (qa_server, port 8767)
  After=network.target

  [Service]
  User=ubuntu
  WorkingDirectory=/data/dsa110-continuum
  Environment=PYTHONPATH=/data/dsa110-continuum
  EnvironmentFile=-/data/dsa110-proc/products/control/dashboard.env
  ExecStart=/opt/miniforge/envs/casa6/bin/python -m uvicorn scripts.qa_server:app --host 0.0.0.0 --port 8767 --log-level warning
  Restart=on-failure
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```

  ```ini
  # ops/systemd/dsa110-autopipeline.service
  [Unit]
  Description=DSA-110 continuum daily batch pipeline launcher

  [Service]
  Type=oneshot
  User=ubuntu
  WorkingDirectory=/data/dsa110-continuum
  Environment=PYTHONPATH=/data/dsa110-continuum
  ExecStart=/opt/miniforge/envs/casa6/bin/python scripts/auto_pipeline.py
  ```

  ```ini
  # ops/systemd/dsa110-autopipeline.timer
  [Unit]
  Description=Launch the DSA-110 batch pipeline daily at 02:00 UTC

  [Timer]
  OnCalendar=*-*-* 02:00:00 UTC
  Persistent=true

  [Install]
  WantedBy=timers.target
  ```

  `dashboard.env` holds `DSA110_CONTROL_TOKEN=…` (mode 600, owned by ubuntu; NOT committed).
- [x] **Document.** File: `docs/operations/dashboard.md` (new): token generation
  (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), env file creation, unit
  install (`sudo cp ops/systemd/*.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now dsa110-dashboard dsa110-autopipeline.timer`),
  Cloudflare posture (tunnel stays read-only-safe because mutating routes are token-gated;
  enabling Cloudflare Access is the documented hardening step), audit-log location, and the
  manual-intervention runbook (dry-run → launch → watch → terminate). Update the CLAUDE.md
  FastAPI section sentence for `scripts/qa_server.py` to mention the token-gated control API,
  and add `scripts/auto_pipeline.py` to the CLAUDE.md command table.
- [x] Full gate: `make test-cloud PYTHON=$PY && $PY -m pytest tests/test_qa_server.py tests/test_observability_control.py tests/test_auto_pipeline.py -q && ruff check scripts/ dsa110_continuum/observability/ tests/` → all green.
- [x] **Commit:** `git commit -am "Add scheduled pipeline launcher, systemd units, and operations docs"`
- [ ] Open a PR from `dashboard-production` to `main` referencing #51, #53 (partial), #59
  (minimal), #60 (minimal), and the #49 decision; request user review. Installing/enabling the
  systemd units on H17 is a user-approved step performed after merge (sudo, shared state).

**Dependencies:** Phases 1–5.

**Verification:**
- [ ] `systemd-analyze verify ops/systemd/dsa110-dashboard.service` (on H17) → no errors.
- [ ] After user installs units: `systemctl status dsa110-dashboard` active;
      `systemctl list-timers dsa110-autopipeline*` shows next trigger;
      one timer-driven run appears in `/api/runs` the next morning.

## Success Criteria

### Automated Verification

- [x] `make test-cloud PYTHON=/opt/miniforge/envs/casa6/bin/python` → exit 0.
- [x] `$PY -m pytest tests/test_qa_server.py tests/test_observability_control.py tests/test_auto_pipeline.py tests/test_observability_hour_state.py tests/test_observability_mosaic_preview.py -q` → all pass.
- [x] `ruff check scripts/qa_server.py scripts/auto_pipeline.py dsa110_continuum/observability/` → clean.
- [x] `$PY -c "import sys, dsa110_continuum.observability.control; assert 'dagster' not in sys.modules"` → exit 0.
- [x] Files exist: `dsa110_continuum/observability/control.py`, `scripts/auto_pipeline.py`, `ops/systemd/dsa110-autopipeline.timer`, `docs/operations/dashboard.md`.
- [x] `$PY -m pytest tests/test_mosaic_import_no_dagster.py -q` → still green (no new Dagster coupling).

### Manual Verification

- [ ] Browser at `http://lxd110h17:8767/`: discovered epochs include 2026-07-13T1100; control
      panel renders; `/runs/2026-07-13` shows the real DEGRADED verdict with its gate reason.
- [ ] Full intervention loop on a scratch date: dry-run preview shows the real plan; Launch
      starts a run visible in `ps` and the runs table; log tail updates; Terminate kills
      `batch_pipeline.py` AND its WSClean/CASA children (verify `pgrep -g <pid>` empty).
- [ ] With `DSA110_CONTROL_TOKEN` unset in the service env, the Launch button yields 403 —
      control is provably disabled by default.
- [ ] Light-curve page for the 3C48-field position returns plausible fluxes and metrics.
- [ ] Audit log `/data/dsa110-proc/products/control/audit.jsonl` records every launch/terminate
      with timestamp and remote address, never the token.

### Reproducibility & Correctness

- [x] Light-curve η/V computed by `photometry/metrics.py` canonical formulas (already
      test-covered in `tests/test_metrics_canonical.py`); the view itself is checked against
      hand-written two-epoch CSVs with known fluxes (Phase 5 tests).
- [x] Run registry rows carry the full argv + request JSON — any launched run is reproducible
      from its registry row alone.

## Testing Strategy

**Unit (in-phase, test-first):** RunRequest validation/argv (injection-shaped inputs),
launch/reap/terminate/single-flight against a fake `batch_pipeline.py`, auth fail-closed
matrix, epoch discovery, provenance page on synthetic manifests (incl. partial manifests),
light-curve matching on synthetic CSVs, auto-launch decision.

**Integration:** Phase 2/6 manual smoke on H17 with the real `batch_pipeline.py --dry-run`
(read-only, safe); full intervention loop on a scratch date (Manual Verification).

**Test data:** all unit tests use `tmp_path` + synthetic FITS/CSV/JSON per existing
`tests/test_qa_server.py` idiom; no telescope data needed; cloud-safe (no casa6-only imports
in the new modules).

## Migration Strategy

No breaking changes: all existing routes keep their URLs; `EPOCHS` remains the fallback when
stage is empty; the Dagster tracer-bullet and its launcher scripts are untouched.

**Rollback:** revert the branch; the control registry directory is additive and ignorable;
disable units with `systemctl disable --now dsa110-autopipeline.timer dsa110-dashboard`.

## Risk Assessment

1. **Risk:** Terminate leaves WSClean/CASA grandchildren alive (the Dagster failure mode we
   avoided). **Likelihood:** Low **Impact:** High.
   **Mitigation:** `start_new_session=True` + `os.killpg`; `batch_pipeline.py` workers run in
   the same session; explicit grandchild-kill test in Phase 1; manual `pgrep -g` check.
2. **Risk:** Token leaks via audit/logs/HTML. **Likelihood:** Low **Impact:** High.
   **Mitigation:** audit writes payload only (no headers); token field is `type=password`,
   never persisted server-side; test asserts token absent from audit.
3. **Risk:** Dashboard (uvicorn) restart orphans running-status rows. **Likelihood:** Medium
   **Impact:** Low. **Mitigation:** `_reconcile` marks pid-dead rows `orphaned` on read; the
   underlying pipeline run continues unharmed (it is session-detached) and its manifest still
   lands.
4. **Risk:** Concurrent dashboard + hand-launched CLI runs collide on stage files.
   **Likelihood:** Medium **Impact:** Medium. **Mitigation:** single-flight guard covers
   launcher-owned runs; `process_status()` already surfaces any external `batch_pipeline.py`
   in the UI heartbeat so the operator can see hand-launched runs before launching.
5. **Risk:** `run_dry_run` blocks a uvicorn worker up to 120 s. **Likelihood:** Medium
   **Impact:** Low. **Mitigation:** explicit timeout → 504; dry-run of batch_pipeline is
   read-only and typically seconds.

## Edge Cases and Error Handling

1. **Token env unset** → every mutating route 403s (fail-closed); read-only dashboard fully
   functional. Tested.
2. **Injection-shaped fields** (`date="2026-01-25; rm -rf /"`) → `RunRequest` ValueError → 400;
   argv is a list (no shell) so even a missed case cannot become a shell string. Tested.
3. **Terminate on finished/unknown run** → 409/404, no signal sent. Tested via status guard.
4. **Partial/corrupt manifest** → provenance page renders `—` fields, never 500. Tested.
5. **Photometry CSV with missing columns** → epoch skipped in light curve with a log warning.
   Tested (header subset check).
6. **Timer fires while yesterday's run still active** → `skipped_running` outcome, JSON on
   stdout into the journal; no queueing (deliberate: next timer tick retries).

## Documentation Updates

- [ ] `docs/operations/dashboard.md` (new — Phase 6).
- [ ] CLAUDE.md: qa_server sentence (token-gated control API), command table row for
      `scripts/auto_pipeline.py` (Phase 6).
- [ ] Issue hygiene: comment on #51/#53/#59/#60 with what landed and what remains; note the
      #49 auth decision on #49 and propose ADR follow-up in `docs/adr/` (the ADR itself can be
      a short follow-up commit).

## Open Questions

*(none — decisions 1–5 in Implementation Approach resolve the research doc's open items;
deferred work is listed in What We're NOT Doing)*

---

## References

**Research Documents:**
- [Research: Production-ready pipeline dashboard](research-dashboard-production-readiness.md)

**Files Analyzed:**
- `scripts/qa_server.py` (fully), `scripts/batch_pipeline.py:1217-1440`,
  `dsa110_continuum/observability/hour_state.py` (fully), `tests/test_qa_server.py:1-110`,
  `scripts/stack_lightcurves.py`, real manifest/summary/CSV schemas on
  `/data/dsa110-proc/products/mosaics/`.

**External Documentation:**
- Cited in the research doc (Dagster/Prefect/Airflow docs and issues, observatory prior art).

---

## Review History

### Version 1.0 — 2026-07-15
- Initial plan created (Direct mode; orchestrator determination delegated by user).

### Version 1.1 — 2026-07-15
- All phases implemented (commits `7183fe7..136b99c`); checkboxes updated. Deviations and
  results in [implement-dashboard-production-readiness.md](implement-dashboard-production-readiness.md).
- Manual-verification items and outward-facing steps (issue comments, PR, systemd install)
  left unchecked for the user.
