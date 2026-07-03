"""Regression tests for issue #47: pytest basetemp hygiene.

The suite must not pin ``--basetemp`` to an absolute host-specific path in
``pyproject.toml``.  A shared absolute basetemp breaks any checkout that is
not at that exact path (FileNotFoundError on tmp_path setup) and makes
concurrent/consecutive runs from multiple checkouts race on the same
directory (OSError [Errno 39] Directory not empty during cleanup).

Instead, ``tests/conftest.py`` sets pytest's explicit basetemp under the
current checkout (``<rootdir>/.pytest_tmp/uid-<uid>/run-<pid>``), avoiding
pytest's shared ``pytest-of-<user>`` owner check and giving each run an
isolated cleanup target.
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
    """Without overrides, temp dirs land under <rootdir>/.pytest_tmp/uid-<uid>/run-*."""
    uid = getattr(os, "getuid", lambda: "unknown")()
    expected_parent = Path(pytestconfig.rootpath, ".pytest_tmp", f"uid-{uid}").resolve()

    basetemp = tmp_path_factory.getbasetemp().resolve()
    configured = Path(pytestconfig.getoption("basetemp")).resolve()
    assert basetemp == configured
    assert basetemp.parent == expected_parent
    # Each run must get its own per-process dir, never the shared uid root,
    # so consecutive/concurrent runs cannot collide on cleanup.
    assert basetemp.name.startswith("run-")
    assert basetemp != expected_parent


def test_default_casa_log_dir_is_test_local(pytestconfig: pytest.Config) -> None:
    """Tests should not create CASA logs under production /data defaults."""
    if os.environ.get("DSA110_TEST_DEFAULT_CASA_LOG_DIR") != "1":
        pytest.skip("external CASA log configuration in effect")

    casa_log_dir = Path(os.environ["CASA_LOG_DIR"]).resolve()
    basetemp = Path(pytestconfig.getoption("basetemp")).resolve()
    assert casa_log_dir.is_relative_to(basetemp)
