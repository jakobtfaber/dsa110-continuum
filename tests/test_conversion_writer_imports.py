"""Regression tests for conversion writer import order."""

import os
import subprocess
import sys
from pathlib import Path


def test_direct_subband_imports_in_fresh_interpreter():
    """The spawned-worker import order must not re-enter a partial module."""
    repo_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(repo_root))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from dsa110_continuum.conversion.direct_subband import DirectSubbandWriter",
        ],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_conversion_package_exports_direct_subband_writer():
    """Keep the documented package-level concrete writer export working."""
    from dsa110_continuum.conversion import DirectSubbandWriter
    from dsa110_continuum.conversion.writers import get_writer

    assert get_writer("direct-subband") is DirectSubbandWriter
