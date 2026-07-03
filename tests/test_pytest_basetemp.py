"""Regression tests for issue #47: pytest basetemp hygiene.

The suite must not pin ``--basetemp`` to an absolute host-specific path in
``pyproject.toml``.  A shared absolute basetemp breaks any checkout that is
not at that exact path (FileNotFoundError on tmp_path setup) and makes
concurrent/consecutive runs from multiple checkouts race on the same
directory (OSError [Errno 39] Directory not empty during cleanup).

Instead, ``tests/conftest.py`` re-roots pytest's temp tree under the current
checkout (``<rootdir>/.pytest_tmp/uid-<uid>``) via
``PYTEST_DEBUG_TEMPROOT``, so each run gets its own numbered ``pytest-N``
directory with pytest's built-in retention-based cleanup.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_ini_addopts_do_not_pin_basetemp(pytestconfig: pytest.Config) -> None:
    """addopts must not hard-code --basetemp (issue #47)."""
    addopts = pytestconfig.getini("addopts")
    offending = [opt for opt in addopts if "--basetemp" in opt]
    assert not offending, (
        f"pyproject.toml addopts pins basetemp to a host-specific path: {offending}. "
        "Temp re-rooting is handled per-checkout in tests/conftest.py (issue #47)."
    )


def test_default_basetemp_is_rooted_in_current_checkout_uid_namespace(
    pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Without overrides, temp dirs land under <rootdir>/.pytest_tmp/uid-<uid>."""
    if pytestconfig.getoption("basetemp"):
        pytest.skip("explicit --basetemp override in effect")

    uid = getattr(os, "getuid", lambda: "unknown")()
    expected_root = Path(pytestconfig.rootpath, ".pytest_tmp", f"uid-{uid}").resolve()
    temproot = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    assert temproot is not None, "tests/conftest.py did not set PYTEST_DEBUG_TEMPROOT"
    if Path(temproot).resolve() != expected_root:
        pytest.skip("external PYTEST_DEBUG_TEMPROOT override in effect")

    basetemp = tmp_path_factory.getbasetemp().resolve()
    assert basetemp.is_relative_to(expected_root)
    # Each run must get its own numbered dir, never the shared root itself,
    # so consecutive/concurrent runs cannot collide on cleanup.
    assert basetemp != expected_root
