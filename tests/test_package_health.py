"""Tests for package health dependency checks."""

from __future__ import annotations

from types import SimpleNamespace

from dsa110_continuum.validation import package_health


def test_core_dependency_check_includes_production_reprojection(monkeypatch):
    imported: list[str] = []

    def fake_import(name: str):
        imported.append(name)
        return SimpleNamespace(__version__="test")

    monkeypatch.setattr(package_health.importlib, "import_module", fake_import)

    passed, missing = package_health.check_core_dependencies()

    assert "reproject" in imported
    assert passed == len(imported)
    assert missing == []
