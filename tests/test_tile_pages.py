"""Tile view: glue units + routed pages (cloud-safe; H17 integration guarded)."""

from __future__ import annotations

import base64
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from dsa110_continuum.observability import artifacts, tile_qa
from fastapi.testclient import TestClient
from scripts.qa_server import DashboardConfig, create_app

TS = "2026-01-25T02:01:43"
TINY_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    b"AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)

BAD_PARAMS = [
    "..",
    "....",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "passwd",
    "../../../etc/passwd",
    "%2e%2e",
    "2026-01-25%0A",
    "2026-01-25",
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


def _make_tile(config: DashboardConfig, suffixes=("image-pb", "residual", "psf")) -> Path:
    tile_dir = config.stage / "images" / f"mosaic_{TS[:10]}"
    tile_dir.mkdir(parents=True, exist_ok=True)
    data = np.zeros((16, 16), dtype=np.float32)
    data[8, 8] = 5.0
    for suffix in suffixes:
        fits.writeto(tile_dir / f"{TS}-{suffix}.fits", data, overwrite=True)
    return tile_dir


class TestTileGlue:
    def test_summary_on_synthetic_tile(self, tmp_path):
        config = _make_config(tmp_path)
        _make_tile(config)
        products = artifacts.tile_products(config.stage / "images", TS)
        summary = tile_qa.summary(products, ms_path=None)
        assert summary["gate"]["overall"] in ("PASS", "WARN", "FAIL")
        assert summary["residual"] is not None
        assert summary["psf_correlation"] is None  # no dirty plane written

    def test_plot_kinds_reflect_products(self, tmp_path):
        config = _make_config(tmp_path)
        _make_tile(config, suffixes=("image",))
        products = artifacts.tile_products(config.stage / "images", TS)
        kinds = tile_qa.plot_kinds(products, ms_available=False)
        assert "image" in kinds
        assert "residual" not in kinds  # product absent
        assert "residual_hist" not in kinds  # no MS


class TestTileRoutes:
    def test_traversal_payloads_rejected(self, tmp_path):
        config = _make_config(tmp_path)
        with TestClient(create_app(config)) as client:
            for payload in BAD_PARAMS:
                for route in (
                    f"/artifacts/tile/{payload}",
                    f"/artifacts/tile/{payload}/status",
                    f"/artifacts/tile/{payload}/plot/image.png",
                ):
                    response = client.get(route)
                    assert 400 <= response.status_code < 500, route
                    assert "root:" not in response.text

    def test_index_and_page_render(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        tile_dir = _make_tile(config)
        fits.writeto(
            tile_dir / f"{TS[:10]}T0200_mosaic.fits",
            np.zeros((4, 4), dtype=np.float32),
            overwrite=True,
        )
        monkeypatch.setattr(
            tile_qa,
            "summary",
            lambda products, ms_path: {
                "gate": {"overall": "PASS", "dynamic_range": 500.0},
                "residual": {"rms": 0.01},
                "psf_correlation": 0.9,
                "noise": None,
            },
        )
        with TestClient(create_app(config)) as client:
            index = client.get("/artifacts/tile/")
            page = client.get(f"/artifacts/tile/{TS}")
        assert f"/artifacts/tile/{TS}" in index.text
        assert page.status_code == 200
        assert "PASS" in page.text
        assert f"/artifacts/mosaic/{TS[:10]}/T0200/status" in page.text  # downstream link

    def test_plot_route_renders_real_image_plot(self, tmp_path):
        """Plot kind 'image' uses fits_plots on the synthetic FITS — no CASA needed."""
        config = _make_config(tmp_path)
        _make_tile(config)
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/tile/{TS}/plot/image.png")
        assert response.status_code == 200
        assert response.content[:4] == b"\x89PNG"

    def test_scattering_unavailable_maps_to_424(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_tile(config)

        def no_scattering(products, kind, target, ms_path=None):
            raise artifacts.ArtifactRenderError("scattering library unavailable")

        monkeypatch.setattr(tile_qa, "render_plot", no_scattering)
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/tile/{TS}/plot/scattering.png")
        assert response.status_code == 424


STAGE = Path("/stage/dsa110-contimg")
requires_stage = pytest.mark.skipif(not STAGE.is_dir(), reason="H17 stage not present")


@requires_stage
class TestTileLiveIntegration:
    """Issue #55 acceptance: renders for at least one real single-tile FITS."""

    def test_real_tile_page_renders(self, tmp_path):
        records = artifacts.list_tiles(STAGE / "images", limit=1)
        assert records, "no tiles on stage"
        config = DashboardConfig(thumb_dir=tmp_path / "thumbs")
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/tile/{records[0]['name']}")
        assert response.status_code == 200
        assert "QA gate" in response.text
