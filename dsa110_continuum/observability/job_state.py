"""Per-artifact in-flight job state: processes + registry runs + filtered log tails (#57)."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROCESS_KEYWORDS = ("wsclean", "aoflagger", "batch_pipeline.py", "dsa110", "casa")
BATCH_MARKERS = ("batch_pipeline.py", "auto_pipeline.py", "mosaic_day.py", "dsa110 convert")
EXCLUDE_MARKERS = ("uvicorn", "pytest", "ps -eo", "job_state")
TAIL_WINDOW = 4000


def active_processes() -> list[dict]:
    """List pipeline-relevant processes (pid, elapsed seconds, command)."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,etimes=,args="],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    processes = []
    for line in result.stdout.splitlines():
        lowered = line.lower()
        if not any(keyword in lowered for keyword in PROCESS_KEYWORDS):
            continue
        if any(marker in lowered for marker in EXCLUDE_MARKERS):
            continue
        parts = line.strip().split(maxsplit=2)
        if len(parts) == 3:
            processes.append(
                {
                    "pid": int(parts[0]),
                    "elapsed_seconds": int(parts[1]),
                    "command": parts[2],
                }
            )
    return processes


def _filtered_tail(log_path: Path, needle: str, max_lines: int) -> list[str]:
    try:
        with log_path.open(errors="replace") as stream:
            lines = stream.readlines()[-TAIL_WINDOW:]
    except OSError:
        return []
    matched = [line.rstrip("\n") for line in lines if needle in line]
    return matched[-max_lines:]


def _running_runs(control_config) -> list[dict]:
    from dsa110_continuum.observability import control as pipeline_control

    config = control_config or pipeline_control.ControlConfig()
    return [run for run in pipeline_control.list_runs(config) if run.get("status") == "running"]


def jobs_for_timestamp(ts: str, control_config=None, max_lines: int = 40) -> dict:
    """In-flight job state for one artifact timestamp (YYYY-MM-DDTHH:MM:SS).

    A process whose argv contains the full timestamp is a direct hit (wsclean
    imagename, conversion MS path); a process carrying only the date is a
    date-level batch hit whose registry log is then line-filtered by the full
    timestamp.
    """
    date = ts[:10]
    processes = active_processes()
    direct = [proc for proc in processes if ts in proc["command"]]
    batch = [
        proc
        for proc in processes
        if proc not in direct
        and date in proc["command"]
        and any(marker in proc["command"] for marker in BATCH_MARKERS)
    ]
    runs: list[dict] = []
    try:
        for run in _running_runs(control_config):
            log_path = run.get("log_path")
            lines = _filtered_tail(Path(log_path), ts, max_lines) if log_path else []
            if lines:
                runs.append(
                    {
                        "run_id": run.get("run_id"),
                        "pid": run.get("pid"),
                        "log_path": log_path,
                        "log_lines": lines,
                    }
                )
    except Exception:
        runs = []
    return {
        "processes": direct,
        "batch": batch,
        "runs": runs,
        "active": bool(direct or batch or runs),
    }
