"""MS view: glue units + routed pages (cloud-safe; H17 integration guarded)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from dsa110_continuum.observability import artifacts, ms_qa
from fastapi.testclient import TestClient
from scripts.qa_server import DashboardConfig, create_app

TS = "2026-01-25T22:26:05"
MS = f"{TS}.ms"
TINY_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    b"AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)

BAD_PARAMS = [
    "..",
    "....",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "passwd.ms",
    "../../../etc/passwd",
    "%2e%2e",
    f"{TS}.MS",
]


def _make_config(tmp_path: Path) -> DashboardConfig:
    return DashboardConfig(
        stage=tmp_path / "stage",
        products=tmp_path / "products",
        incoming=tmp_path / "incoming",
        thumb_dir=tmp_path / "thumbs",
        campaign_outputs=tmp_path / "campaign",
        campaign_date="2026-07-13",
        campaign_hour=11,
    )


def _make_ms(config: DashboardConfig, name: str = MS) -> Path:
    path = config.stage / "ms" / name
    path.mkdir(parents=True)
    (path / "table.dat").write_bytes(b"x")
    return path


class TestMsGlue:
    def test_summary_degrades_without_casa_data(self, tmp_path):
        """check_ms_after_conversion is designed to degrade; summary must not raise."""
        config = _make_config(tmp_path)
        ms = _make_ms(config)
        summary = ms_qa.summary(ms)
        assert "conversion" in summary
        assert summary["conversion"]["exists"] is True

    def test_plot_kinds_gate_on_bpcal(self, tmp_path):
        config = _make_config(tmp_path)
        ms = _make_ms(config)
        assert "bandpass_diag" not in ms_qa.plot_kinds(ms)
        (config.stage / "ms" / f"{TS}_0~23.b").mkdir()
        assert "bandpass_diag" in ms_qa.plot_kinds(ms)

    def test_bp_table_strips_meridian_suffix(self, tmp_path):
        config = _make_config(tmp_path)
        ms = _make_ms(config, name=f"{TS}_meridian.ms")
        (config.stage / "ms" / f"{TS}_0~23.b").mkdir()
        assert "bandpass_diag" in ms_qa.plot_kinds(ms)


class TestMsRoutes:
    def test_traversal_payloads_rejected(self, tmp_path):
        config = _make_config(tmp_path)
        with TestClient(create_app(config)) as client:
            for payload in BAD_PARAMS:
                for route in (
                    f"/artifacts/ms/{payload}",
                    f"/artifacts/ms/{payload}/status",
                    f"/artifacts/ms/{payload}/plot/uv_coverage.png",
                ):
                    response = client.get(route)
                    assert 400 <= response.status_code < 500, route
                    assert "root:" not in response.text

    def test_index_page_and_lifecycle(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_ms(config)
        monkeypatch.setattr(
            ms_qa,
            "summary",
            lambda path: {
                "conversion": {"exists": True, "size_bytes": 1},
                "conversion_passed": True,
                "uvw": {"is_valid": True},
                "rfi": {"total_occupancy": 0.02},
            },
        )
        with TestClient(create_app(config)) as client:
            index = client.get("/artifacts/ms/")
            page = client.get(f"/artifacts/ms/{MS}")
        assert f"/artifacts/ms/{MS}" in index.text
        assert page.status_code == 200
        assert "Lifecycle" in page.text

    def test_plot_route_caches(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_ms(config)
        calls = []

        def fake_render(path, kind, target):
            calls.append(kind)
            target.write_bytes(TINY_PNG)

        monkeypatch.setattr(ms_qa, "render_plot", fake_render)
        with TestClient(create_app(config)) as client:
            for _ in range(2):
                assert client.get(f"/artifacts/ms/{MS}/plot/uv_coverage.png").status_code == 200
        assert calls == ["uv_coverage"]

    def test_render_error_maps_to_424(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_ms(config)

        def broken(path, kind, target):
            raise artifacts.ArtifactRenderError("casacore unavailable")

        monkeypatch.setattr(ms_qa, "render_plot", broken)
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/ms/{MS}/plot/uv_coverage.png")
        assert response.status_code == 424


STAGE_MS = Path("/stage/dsa110-contimg/ms")
requires_stage = pytest.mark.skipif(not STAGE_MS.is_dir(), reason="H17 stage volume not present")


@requires_stage
class TestMsLiveIntegration:
    """Issue #54 acceptance: renders for at least one real MS on stage."""

    def test_real_ms_page_renders(self, tmp_path):
        records = artifacts.list_ms(STAGE_MS, limit=1)
        assert records, "no MS on stage"
        config = DashboardConfig(thumb_dir=tmp_path / "thumbs")
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/ms/{records[0]['name']}")
        assert response.status_code == 200
        assert "Lifecycle" in response.text

    def test_real_uv_coverage_plot(self, tmp_path):
        records = artifacts.list_ms(STAGE_MS, limit=1)
        config = DashboardConfig(thumb_dir=tmp_path / "thumbs")
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/ms/{records[0]['name']}/plot/uv_coverage.png")
        assert response.status_code == 200
        assert response.content[:4] == b"\x89PNG"
