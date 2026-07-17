"""Scheduled entrypoint: index, convert, and launch one date's science pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsa110_continuum.observability.control import (  # noqa: E402
    ControlConfig,
    RunConflictError,
    RunRequest,
    get_run,
    launch_run,
    list_runs,
)

INPUT_DIR = Path(os.environ.get("DSA110_INPUT_DIR", "/data/incoming"))
MS_DIR = Path(os.environ.get("DSA110_MS_DIR", "/stage/dsa110-contimg/ms"))
PIPELINE_DB = Path(os.environ.get("PIPELINE_DB", "/data/dsa110-contimg/state/db/pipeline.sqlite3"))


def _base_ms_count(ms_dir: Path, date: str) -> int:
    """Count converted base MS directories, excluding derived copies."""
    count = 0
    for path in ms_dir.glob(f"{date}T*.ms"):
        try:
            datetime.strptime(path.stem, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        if (path / "table.dat").is_file():
            count += 1
    return count


def prepare_date(
    date: str,
    *,
    input_dir: Path = INPUT_DIR,
    ms_dir: Path = MS_DIR,
    pipeline_db: Path = PIPELINE_DB,
) -> dict:
    """Index one date's incoming subbands and convert complete groups to MS."""
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        return {"action": "invalid_request", "detail": str(exc)}
    if parsed_date.strftime("%Y-%m-%d") != date:
        return {"action": "invalid_request", "detail": "date must be YYYY-MM-DD"}

    existing_ms = _base_ms_count(ms_dir, date)
    if not input_dir.is_dir():
        if existing_ms:
            return {
                "action": "ready",
                "input_files": 0,
                "indexed_files": 0,
                "ms_count": existing_ms,
            }
        return {"action": "input_unavailable", "detail": f"input directory not found: {input_dir}"}

    files = sorted(input_dir.glob(f"{date}T*_sb*.hdf5"))
    if not files and not existing_ms:
        return {"action": "waiting_for_data", "input_files": 0, "ms_count": 0}

    os.environ["PIPELINE_DB"] = str(pipeline_db)
    try:
        from dsa110_continuum.database.hdf5_index import index_subband_files
        from dsa110_continuum.database.unified import init_unified_db

        database = init_unified_db(pipeline_db)
        try:
            indexed = index_subband_files(database.conn, files)
        finally:
            database.close()
    except Exception as exc:
        return {"action": "index_failed", "detail": str(exc), "input_files": len(files)}

    try:
        from dsa110_continuum.conversion.conversion_orchestrator import (
            convert_subband_groups_to_ms,
        )

        conversion = convert_subband_groups_to_ms(
            str(input_dir),
            str(ms_dir),
            f"{date}T00:00:00",
            f"{date}T23:59:59",
            skip_incomplete=True,
            skip_existing=True,
        )
    except Exception as exc:
        return {
            "action": "conversion_failed",
            "detail": str(exc),
            "input_files": len(files),
            "indexed_files": indexed,
        }

    if conversion["failed"]:
        return {
            "action": "conversion_failed",
            "input_files": len(files),
            "indexed_files": indexed,
            "converted": len(conversion["converted"]),
            "skipped": len(conversion["skipped"]),
            "failed": conversion["failed"],
        }

    ms_count = _base_ms_count(ms_dir, date)
    if not ms_count:
        return {
            "action": "waiting_for_complete_groups",
            "input_files": len(files),
            "indexed_files": indexed,
            "converted": len(conversion["converted"]),
            "skipped": len(conversion["skipped"]),
            "ms_count": 0,
        }
    return {
        "action": "ready",
        "input_files": len(files),
        "indexed_files": indexed,
        "converted": len(conversion["converted"]),
        "skipped": len(conversion["skipped"]),
        "ms_count": ms_count,
    }


def decide_and_launch(
    date: str,
    config: ControlConfig | None = None,
    *,
    rfi_mode: str | None = None,
) -> dict:
    """Launch the batch pipeline for one date unless a run is already active."""
    config = config or ControlConfig()
    try:
        request = RunRequest(
            date=date,
            rfi_mode=rfi_mode,
            retry_failed=True,
            quarantine_after_failures=3,
            photometry_workers=4,
        )
    except ValueError as exc:
        return {"action": "invalid_request", "detail": str(exc)}
    try:
        record = launch_run(request, config)
    except RunConflictError as exc:
        return {"action": "skipped_running", "detail": str(exc)}
    return {"action": "launched", "run_id": record["run_id"], "pid": record["pid"]}


def prepare_and_launch(
    date: str,
    config: ControlConfig | None = None,
    *,
    input_dir: Path = INPUT_DIR,
    ms_dir: Path = MS_DIR,
    pipeline_db: Path = PIPELINE_DB,
    rfi_mode: str | None = None,
) -> dict:
    """Prepare raw data, then launch the existing strict-QA batch pipeline."""
    config = config or ControlConfig()
    if any(run["status"] == "running" for run in list_runs(config)):
        return {"action": "skipped_running"}
    intake = prepare_date(
        date,
        input_dir=input_dir,
        ms_dir=ms_dir,
        pipeline_db=pipeline_db,
    )
    if intake["action"] != "ready":
        return intake
    outcome = decide_and_launch(date, config, rfi_mode=rfi_mode)
    outcome["intake"] = intake
    return outcome


def wait_for_run(
    run_id: str,
    config: ControlConfig,
    poll_seconds: float = 5.0,
    orphan_grace_seconds: float = 30.0,
) -> dict:
    """Keep the scheduled launcher alive until its registry run is final."""
    orphaned_since = None
    while True:
        record = get_run(run_id, config)
        if record["status"] in {"succeeded", "failed", "terminated"}:
            return record
        if record["status"] == "orphaned":
            orphaned_since = orphaned_since or time.monotonic()
            if time.monotonic() - orphaned_since >= orphan_grace_seconds:
                return record
        else:
            orphaned_since = None
        time.sleep(poll_seconds)


def main() -> int:
    """CLI entrypoint for cron/systemd."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=(datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Observation date to process (default: yesterday UTC).",
    )
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--ms-dir", type=Path, default=MS_DIR)
    parser.add_argument("--pipeline-db", type=Path, default=PIPELINE_DB)
    parser.add_argument(
        "--rfi-mode",
        choices=("conditional", "cflag", "full", "off"),
        default=None,
        help="RFI policy forwarded to the science batch (default: conditional).",
    )
    arguments = parser.parse_args()
    config = ControlConfig()
    outcome = prepare_and_launch(
        arguments.date,
        config,
        input_dir=arguments.input_dir,
        ms_dir=arguments.ms_dir,
        pipeline_db=arguments.pipeline_db,
        rfi_mode=arguments.rfi_mode,
    )
    if outcome["action"] == "launched":
        record = wait_for_run(outcome["run_id"], config)
        outcome["run_status"] = record["status"]
        outcome["exit_code"] = record["exit_code"]
    print(json.dumps(outcome))
    failed_actions = {"invalid_request", "input_unavailable", "index_failed", "conversion_failed"}
    run_failed = outcome["action"] == "launched" and outcome.get("run_status") != "succeeded"
    return int(outcome["action"] in failed_actions or run_failed)


if __name__ == "__main__":
    raise SystemExit(main())
