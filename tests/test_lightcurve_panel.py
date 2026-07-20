"""Unit tests for the optional Panel lightcurve scaffold."""

from __future__ import annotations

import pytest

pytest.importorskip("panel")
pytest.importorskip("holoviews")

from dsa110_continuum.dashboard.lightcurve_panel import (  # noqa: E402
    build_lightcurve_app,
)


def _fake_table() -> dict:
    return {
        "n_epochs": 3,
        "sources": [
            {
                "source_id": "NVSS_0001",
                "n_epochs": 3,
                "mean_flux_jy": 1.0,
                "v": 0.1,
                "eta": 12.0,
                "epochs": ["2026-01-01T2200", "2026-01-02T2200", "2026-01-03T2200"],
                "flux_jy": [1.0, 0.9, 1.1],
                "flux_err_jy": [0.05, 0.05, 0.05],
            },
            {
                "source_id": "NVSS_0007",
                "n_epochs": 3,
                "mean_flux_jy": 0.8,
                "v": 0.4,
                "eta": 172.0,
                "epochs": ["2026-01-01T2200", "2026-01-02T2200", "2026-01-03T2200"],
                "flux_jy": [1.0, 0.4, 1.0],
                "flux_err_jy": [0.05, 0.05, 0.05],
            },
        ],
    }


def test_build_lightcurve_app_returns_viewable():
    factory = build_lightcurve_app(_fake_table)
    view = factory()
    assert view is not None
    # Panel Column with selector + bound plot
    assert hasattr(view, "objects")
    assert len(view.objects) >= 2


def test_build_lightcurve_app_empty_table():
    factory = build_lightcurve_app(lambda: {"n_epochs": 1, "sources": []})
    view = factory()
    assert view is not None
