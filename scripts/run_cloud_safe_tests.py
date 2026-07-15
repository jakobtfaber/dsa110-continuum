#!/usr/bin/env python3
"""Run the stable pure-Python CI gate without CASA or H17 data."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUD_SAFE_TESTS = (
    "tests/test_provenance.py",
    "tests/test_resume_semantics.py",
    "tests/test_qa_default_strict.py",
    "tests/test_run_logging.py",
    "tests/test_run_report.py",
    "tests/test_forced_photometry_parallel.py",
    "tests/test_batch_pipeline_dry_run_quarantine.py",
    "tests/test_batch_e1_hygiene.py",
    "tests/test_batch_e2_hygiene.py",
    "tests/test_skymodel_phase_dir.py",
    "tests/test_epoch_gaincal_field_shape.py",
    "tests/test_import_migration_checker.py",
    "tests/test_no_latent_nameerror_imports.py",
    "tests/test_vendored_utils.py",
    "tests/test_unified_config.py",
    "tests/test_workflow_registry.py",
    "tests/test_vendored_database.py",
    "tests/test_init_reexports_new_namespace.py",
    "tests/test_no_compat_layer.py",
    "tests/test_imaging_worker_no_fast_imaging.py",
    "tests/test_no_stale_contimg_api_refs.py",
    "tests/test_paths_resolver.py",
    "tests/test_qa_server.py",
    "tests/test_artifact_substrate.py",
    "tests/test_caltable_pages.py",
    "tests/test_tile_pages.py",
    "tests/test_ms_pages.py",
)


def _environment() -> dict[str, str]:
    """Return the environment shared by the import and pytest gates."""
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{ROOT}{os.pathsep}{current_pythonpath}" if current_pythonpath else str(ROOT)
    )
    env.setdefault("CASKADE_BACKEND", "numpy")
    return env


def _run(command: list[str], env: dict[str, str]) -> None:
    """Run one gate and fail immediately with its exit status."""
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    """Run the legacy-import guard followed by the cloud-safe pytest subset."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--basetemp",
        type=Path,
        help="pytest temporary directory (defaults to an isolated system temp dir)",
    )
    args = parser.parse_args()

    temp_context = (
        nullcontext(args.basetemp)
        if args.basetemp is not None
        else tempfile.TemporaryDirectory(prefix="dsa110-cloud-safe-")
    )
    env = _environment()
    with temp_context as basetemp:
        _run(
            [sys.executable, "scripts/check_import_migration.py", "--fail-on-any"],
            env,
        )
        _run(
            [sys.executable, "scripts/check_contimg_mentions.py", "--fail"],
            env,
        )
        _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                f"--basetemp={basetemp}",
                *CLOUD_SAFE_TESTS,
            ],
            env,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
