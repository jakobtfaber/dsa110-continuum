"""Regression tests for issue #79: missing casa_tables must raise ImportError, not TypeError.

``solver_common`` binds ``table = None`` when ``dsa110_continuum.adapters.casa_tables``
cannot be imported, so downstream ``with table(...)`` call sites in solve_delay /
solve_bandpass raised ``TypeError: 'NoneType' object is not callable``. These tests
load a fresh copy of ``solver_common`` with the adapter import blocked and assert the
first-use paths raise a clear ImportError instead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import dsa110_continuum.adapters as adapters_pkg
import pytest
from dsa110_continuum.calibration import solve_bandpass, solve_delay, solver_common


@pytest.fixture
def solver_common_no_casa_tables(monkeypatch):
    """Load a fresh solver_common with the casa_tables adapter import blocked."""
    monkeypatch.setitem(sys.modules, "dsa110_continuum.adapters.casa_tables", None)
    monkeypatch.delattr(adapters_pkg, "casa_tables", raising=False)
    spec = importlib.util.spec_from_file_location(
        "_solver_common_no_casa_tables", Path(solver_common.__file__)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_remains_permissive_without_casa_tables(solver_common_no_casa_tables):
    assert callable(solver_common_no_casa_tables.table)


def test_table_call_raises_clear_import_error(solver_common_no_casa_tables):
    with pytest.raises(ImportError, match=r"dsa110_continuum\.adapters\.casa_tables") as excinfo:
        with solver_common_no_casa_tables.table("/nonexistent.ms") as _:
            pass
    assert "casa6" in str(excinfo.value)


def test_solve_delay_first_use_raises_import_error(solver_common_no_casa_tables, monkeypatch):
    monkeypatch.setattr(solve_delay, "table", solver_common_no_casa_tables.table)
    with pytest.raises(ImportError, match=r"dsa110_continuum\.adapters\.casa_tables"):
        solve_delay._validate_delay_solve_preconditions("/nonexistent.ms", "0", "103")


def test_solve_bandpass_first_use_raises_import_error(solver_common_no_casa_tables, monkeypatch):
    monkeypatch.setattr(solve_bandpass, "table", solver_common_no_casa_tables.table)
    with pytest.raises(ImportError, match=r"dsa110_continuum\.adapters\.casa_tables"):
        solve_bandpass._check_flag_fraction("/nonexistent.tbl")
