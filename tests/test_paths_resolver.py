"""Path resolver env precedence: DSA110_* over CONTIMG_*."""
from __future__ import annotations

from dsa110_continuum.utils.paths.resolver import (
    _resolve_base_dir_with_source,
    _resolve_staging_dir_with_source,
    _resolve_tmpfs_dir_with_source,
)


def test_prefers_dsa110_base_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DSA110_BASE_DIR", str(tmp_path / "new"))
    monkeypatch.setenv("CONTIMG_BASE_DIR", str(tmp_path / "old"))
    base, src = _resolve_base_dir_with_source()
    assert base == (tmp_path / "new").resolve()
    assert src == "DSA110_BASE_DIR"


def test_falls_back_to_contimg_base_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("DSA110_BASE_DIR", raising=False)
    monkeypatch.setenv("CONTIMG_BASE_DIR", str(tmp_path / "old"))
    base, src = _resolve_base_dir_with_source()
    assert base == (tmp_path / "old").resolve()
    assert src == "CONTIMG_BASE_DIR"


def test_prefers_dsa110_staging_and_tmpfs(monkeypatch, tmp_path):
    monkeypatch.setenv("DSA110_STAGING_DIR", str(tmp_path / "stage"))
    monkeypatch.setenv("CONTIMG_STAGING_DIR", str(tmp_path / "old_stage"))
    monkeypatch.setenv("DSA110_TMPFS_DIR", str(tmp_path / "tmpfs"))
    monkeypatch.setenv("CONTIMG_TMPFS_DIR", str(tmp_path / "old_tmpfs"))
    staging, staging_src = _resolve_staging_dir_with_source(tmp_path)
    tmpfs, tmpfs_src = _resolve_tmpfs_dir_with_source()
    assert staging == tmp_path / "stage"
    assert staging_src == "DSA110_STAGING_DIR"
    assert tmpfs == tmp_path / "tmpfs"
    assert tmpfs_src == "DSA110_TMPFS_DIR"
