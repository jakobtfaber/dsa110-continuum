"""
pytest conftest for dsa110-continuum cloud/CI test environment.

Installs minimal mock stubs for packages that are only available on H17
(casacore, casa6, etc.) so that tests can import and mock them without the
real binary dependencies.

Also gates ``@pytest.mark.slow`` tests behind a ``--run-slow`` CLI flag
so the default ``pytest tests/`` run finishes in seconds, not minutes.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Re-root pytest's temp tree under the current checkout (issue #47).

    A hard-coded ``--basetemp=/data/dsa110-continuum/.pytest_tmp`` broke any
    checkout not at that path and made runs from multiple checkouts share one
    directory, so pytest's start-of-session wipe raced live runs
    (``OSError [Errno 39] Directory not empty``) and contaminated consecutive
    full-suite runs.

    Instead, set pytest's explicit ``basetemp`` to a per-process path under
    ``<rootdir>/.pytest_tmp/uid-<uid>``.  This keeps tmp_path output on the
    checkout's filesystem (not tiny /tmp on H17), avoids the shared
    ``pytest-of-<user>`` owner check that fails on H17's root-owned /data
    mount, and prevents consecutive/concurrent runs from wiping each other's
    temp trees.  An explicit user-provided ``--basetemp`` still wins.
    """
    if config.option.basetemp is None:
        uid = getattr(os, "getuid", lambda: "unknown")()
        basetemp = Path(config.rootpath, ".pytest_tmp", f"uid-{uid}", f"run-{os.getpid()}")
        basetemp.parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(basetemp)

        tmp_path_factory = getattr(config, "_tmp_path_factory", None)
        if (
            tmp_path_factory is not None
            and tmp_path_factory._given_basetemp is None
            and tmp_path_factory._basetemp is None
        ):
            tmp_path_factory._given_basetemp = basetemp

    if (
        "CASA_LOG_DIR" not in os.environ
        and "CONTIMG_PATHS__CASA_LOGS_DIR" not in os.environ
        and "CONTIMG_BASE_DIR" not in os.environ
    ):
        casa_log_dir = Path(config.option.basetemp, "casa-logs")
        os.environ["CASA_LOG_DIR"] = str(casa_log_dir)
        os.environ["DSA110_TEST_DEFAULT_CASA_LOG_DIR"] = "1"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--run-slow`` to opt into ``@pytest.mark.slow`` tests."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Include tests marked @pytest.mark.slow (skipped by default).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip ``slow``-marked tests unless ``--run-slow`` was passed."""
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="slow test (use --run-slow to enable)")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


def _install_casacore_mock() -> None:
    """Install a minimal casacore mock into sys.modules.

    Only installed when the real casacore is absent.  Tests that need to
    exercise casacore behaviour should patch the specific symbols they use
    (e.g. ``patch("casacore.tables.table", ...)``).
    """
    try:
        import casacore  # noqa: F401 — already installed, nothing to do
        return
    except ImportError:
        pass

    casacore_mod = types.ModuleType("casacore")
    casacore_tables = types.ModuleType("casacore.tables")
    casacore_quanta = types.ModuleType("casacore.quanta")
    casacore_measures = types.ModuleType("casacore.measures")

    # Minimal table stub: behaves like casacore.tables.table
    class _TableInstance(MagicMock):
        """Instance returned by table() — context-manager-compatible."""

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def colnames(self):
            return []  # default: no columns

        def nrows(self):
            return 0

    def _table_factory(path, *args, **kwargs):
        """Raise TypeError for non-string paths, OSError for non-existent paths.

        Mirrors real casacore.tables.table behaviour:
        - Non-string argument → TypeError
        - Non-existent path on disk → OSError (RuntimeError in some casacore builds)
        - Existing path → returns a context-manager-compatible _TableInstance

        Tests that need specific column data should patch
        ``casacore.tables.table`` with a MagicMock configured for their use case.
        """
        import os as _os
        if not isinstance(path, str):
            raise TypeError(
                f"casacore.tables.table: expected str path, got {type(path).__name__!r}"
            )
        # Strip casacore subtable suffix (e.g. "foo.ms::FIELD" → "foo.ms")
        base_path = path.split("::")[0]
        if not _os.path.exists(base_path):
            raise OSError(
                f"casacore.tables.table: path does not exist: {base_path!r}"
            )
        return _TableInstance()

    def _default_ms(path, *args, **kwargs):
        """Stub default_ms — this function requires real casacore to create a
        proper Measurement Set on disk.  Any test that calls default_ms is
        intentionally marked as requiring casacore and will be skipped."""
        import pytest as _pytest
        _pytest.skip("casacore stub: default_ms requires real casacore (H17/casa6 env)")

    casacore_tables.table = _table_factory
    casacore_tables.default_ms = _default_ms
    # Mark as stub so tests can detect it programmatically if needed
    casacore_tables._is_stub = True

    # Wire up the module hierarchy
    casacore_mod.tables = casacore_tables
    casacore_mod.quanta = casacore_quanta
    casacore_mod.measures = casacore_measures

    sys.modules["casacore"] = casacore_mod
    sys.modules["casacore.tables"] = casacore_tables
    sys.modules["casacore.quanta"] = casacore_quanta
    sys.modules["casacore.measures"] = casacore_measures


# Run at collection time so every test file sees the mock
_install_casacore_mock()
