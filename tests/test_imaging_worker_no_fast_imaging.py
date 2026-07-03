"""The fast-imaging leg targeted a module that never existed anywhere."""
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1] / "dsa110_continuum" / "imaging" / "worker.py"


def test_worker_has_no_legacy_fast_imaging():
    src = WORKER.read_text()
    assert "dsa110_contimg.core.imaging.fast_imaging" not in src
    assert "future_fast" not in src
