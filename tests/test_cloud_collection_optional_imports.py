"""Regression tests for cloud-safe collection without optional UVH5 deps."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

BLOCK_PYuvdata_SCRIPT = textwrap.dedent(
    """
    import sys

    class BlockPyuvdata:
        prefix = "pyuvdata"

        def find_spec(self, name, path=None, target=None):
            if name == self.prefix or name.startswith(self.prefix + "."):
                raise ModuleNotFoundError(f"blocked optional dependency: {name}")
            return None

    sys.meta_path.insert(0, BlockPyuvdata())

    import dsa110_continuum.conversion as conversion  # noqa: F401
    from dsa110_continuum.calibration import solver_common  # noqa: F401
    from dsa110_continuum.conversion.calibrator_ms_generator import (
        CalibratorMSGenerator,
    )

    assert CalibratorMSGenerator.__name__ == "CalibratorMSGenerator"
    assert "pyuvdata" not in sys.modules
    print("OK")
    """
)


def test_conversion_and_calibration_imports_do_not_require_pyuvdata():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", BLOCK_PYuvdata_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "conversion/calibration imports required pyuvdata during collection\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "OK" in proc.stdout
