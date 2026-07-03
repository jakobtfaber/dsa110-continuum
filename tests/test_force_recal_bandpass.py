"""Regression tests for issue #70: --force-recal must thread force=True
into ensure_bandpass so same-date BP/G tables are re-acquired, not
silently reused via the priority-1 same-date branch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ on sys.path so we can import batch_pipeline helpers
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))


def _run_main_capturing_ensure_bandpass(tmp_path, monkeypatch, argv_extra):
    import batch_pipeline as bp
    import dsa110_continuum.calibration.ensure as ensure_mod

    ms_dir = tmp_path / "ms"
    ms_dir.mkdir()
    monkeypatch.setattr(bp, "MS_DIR", str(ms_dir))

    captured = {}

    def fake_ensure_bandpass(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        # SystemExit is not swallowed by the auto-cal except Exception
        # handler, so main() stops here without touching later phases.
        raise SystemExit(0)

    monkeypatch.setattr(ensure_mod, "ensure_bandpass", fake_ensure_bandpass)
    monkeypatch.setattr(
        sys,
        "argv",
        ["batch_pipeline.py", "--date", "2026-04-27", *argv_extra],
    )

    with pytest.raises(SystemExit):
        bp.main()

    assert captured, "ensure_bandpass was never called"
    return captured


def test_force_recal_passes_force_true_to_ensure_bandpass(tmp_path, monkeypatch):
    captured = _run_main_capturing_ensure_bandpass(
        tmp_path,
        monkeypatch,
        ["--force-recal"],
    )
    assert captured["kwargs"].get("force") is True


def test_default_run_does_not_force_bandpass_reacquisition(tmp_path, monkeypatch):
    captured = _run_main_capturing_ensure_bandpass(tmp_path, monkeypatch, [])
    assert captured["kwargs"].get("force", False) is False
