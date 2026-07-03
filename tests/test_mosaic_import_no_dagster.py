"""Regression test for issue #75.

Importing dsa110_continuum.mosaic (or any submodule such as builder) must not
touch the legacy ``dsa110_contimg.workflow.dagster`` package, whose
``definitions`` module runs ``_validate_pipeline_prerequisites()`` at import
time and raises RuntimeError unless /dev/shm/dsa110-contimg/ is writable.

Rather than depending on host /dev/shm permissions, a subprocess installs an
import finder that raises RuntimeError for any legacy Dagster module — the
same exception type the real validator raises, which the ImportError guards
in science_jobs.py do not catch.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

POISONED_IMPORT_SCRIPT = textwrap.dedent(
    """
    import sys

    class PoisonLegacyDagster:
        prefix = "dsa110_contimg.workflow.dagster"

        def find_spec(self, name, path=None, target=None):
            if name == self.prefix or name.startswith(self.prefix + "."):
                raise RuntimeError(
                    f"legacy Dagster bootstrap touched at import time: {name}"
                )
            return None

    sys.meta_path.insert(0, PoisonLegacyDagster())

    import dsa110_continuum.mosaic.builder  # noqa: F401
    import dsa110_continuum.mosaic  # noqa: F401

    loaded = [m for m in sys.modules if m.startswith(PoisonLegacyDagster.prefix)]
    assert not loaded, f"legacy Dagster modules loaded: {loaded}"
    print("OK")
    """
)


def test_mosaic_imports_do_not_trigger_legacy_dagster_bootstrap():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", POISONED_IMPORT_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"mosaic import triggered legacy Dagster bootstrap\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "OK" in proc.stdout
