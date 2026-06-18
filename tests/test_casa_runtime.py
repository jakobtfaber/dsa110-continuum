import os
import sys
import types
from pathlib import Path

from dsa110_continuum.calibration import casa_service


def test_casa_runtime_redirects_and_restores(monkeypatch, tmp_path):
    monkeypatch.setenv("CASALOGFILE", "/original/casa.log")
    start_cwd = Path.cwd()
    log_dir = tmp_path / "logs"

    with casa_service.casa_runtime(log_dir=log_dir) as active_log_dir:
        assert active_log_dir == log_dir
        assert Path.cwd() == log_dir
        assert os.environ["CASALOGFILE"] == str(log_dir / "casa.log")

    assert Path.cwd() == start_cwd
    assert os.environ["CASALOGFILE"] == "/original/casa.log"


def test_casa_service_runs_task_inside_runtime(monkeypatch, tmp_path):
    log_dir = tmp_path / "casa-logs"
    observed = {}

    def fake_task(**kwargs):
        observed["cwd"] = Path.cwd()
        observed["casalog"] = os.environ.get("CASALOGFILE")
        observed["kwargs"] = kwargs
        return "ok"

    monkeypatch.setenv("DSA110_CASA_PROCESS_ISOLATION", "false")
    monkeypatch.setenv("CASA_LOG_DIR", str(log_dir))
    monkeypatch.setitem(
        sys.modules,
        "casatasks",
        types.SimpleNamespace(fake_task=fake_task),
    )
    monkeypatch.setattr(casa_service, "_casa_task_cache", {})

    result = casa_service.CASAService().run_task("fake_task", vis="example.ms")

    assert result == "ok"
    assert observed == {
        "cwd": log_dir,
        "casalog": str(log_dir / "casa.log"),
        "kwargs": {"vis": "example.ms"},
    }
