"""Scheduled entrypoint: launch a date's batch pipeline via the control registry."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsa110_continuum.observability.control import (  # noqa: E402
    ControlConfig,
    RunConflictError,
    RunRequest,
    launch_run,
)


def decide_and_launch(date: str, config: ControlConfig | None = None) -> dict:
    """Launch the batch pipeline for one date unless a run is already active."""
    config = config or ControlConfig()
    try:
        request = RunRequest(
            date=date, retry_failed=True, quarantine_after_failures=3, photometry_workers=4
        )
    except ValueError as exc:
        return {"action": "invalid_request", "detail": str(exc)}
    try:
        record = launch_run(request, config)
    except RunConflictError as exc:
        return {"action": "skipped_running", "detail": str(exc)}
    return {"action": "launched", "run_id": record["run_id"], "pid": record["pid"]}


def main() -> int:
    """CLI entrypoint for cron/systemd."""
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
