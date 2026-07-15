"""Caltable view: glue units + routed pages (cloud-safe; H17 integration guarded)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from dsa110_continuum.observability import artifacts, caltable_qa
from fastapi.testclient import TestClient
from scripts.qa_server import DashboardConfig, create_app

TS = "2026-01-25T22:26:05"
TABLE = f"{TS}_0~23.g"
# 1x1 transparent PNG
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
    "x_0~23.b",
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


def _make_caltable(config: DashboardConfig, name: str = TABLE) -> Path:
    path = config.stage / "ms" / name
    path.mkdir(parents=True)
    (path / "table.dat").write_bytes(b"x")
    return path


class TestCaltableGlue:
    def test_plot_kinds_by_extension(self):
        assert "bandpass_amp" in caltable_qa.plot_kinds("x_0~23.b")
        assert "bandpass_amp" not in caltable_qa.plot_kinds("x_0~23.g")
        assert "delay" in caltable_qa.plot_kinds("x_0~23.k")
        assert "stability" in caltable_qa.plot_kinds("x_0~23.g")

    def test_caltable_type(self):
        assert caltable_qa.caltable_type("x.b") == "BP"
        assert caltable_qa.caltable_type("x.g") == "G"
        assert caltable_qa.caltable_type("x.k") == "K"

    def test_provenance_reads_sidecar_next_to_bp(self, tmp_path):
        gtable = tmp_path / TABLE
        gtable.mkdir()
        sidecar = tmp_path / f"{TS}_0~23.b.cal_provenance.json"
        sidecar.write_text(
            json.dumps({"selection_pool": "bright_fallback", "flux_anchor": "vla_catalog"})
        )
        prov = caltable_qa.provenance(gtable)
        assert prov["selection_pool"] == "bright_fallback"

    def test_provenance_absent_returns_none(self, tmp_path):
        gtable = tmp_path / TABLE
        gtable.mkdir()
        assert caltable_qa.provenance(gtable) is None


class TestCaltableRouteSafety:
    def test_traversal_payloads_rejected(self, tmp_path):
        config = _make_config(tmp_path)
        with TestClient(create_app(config)) as client:
            for payload in BAD_PARAMS:
                for route in (
                    f"/artifacts/caltable/{payload}",
                    f"/artifacts/caltable/{payload}/status",
                    f"/artifacts/caltable/{payload}/plot/snr.png",
                ):
                    response = client.get(route)
                    assert 400 <= response.status_code < 500, route
                    assert "root:" not in response.text

    def test_wellformed_unknown_name_404(self, tmp_path):
        config = _make_config(tmp_path)
        with TestClient(create_app(config)) as client:
            assert client.get(f"/artifacts/caltable/{TABLE}").status_code == 404


class TestCaltableIndex:
    def test_empty_state(self, tmp_path):
        config = _make_config(tmp_path)
        with TestClient(create_app(config)) as client:
            response = client.get("/artifacts/caltable/")
        assert response.status_code == 200
        assert "No calibration tables" in response.text

    def test_lists_tables_with_links(self, tmp_path):
        config = _make_config(tmp_path)
        _make_caltable(config)
        with TestClient(create_app(config)) as client:
            response = client.get("/artifacts/caltable/")
        assert f"/artifacts/caltable/{TABLE}" in response.text


class TestCaltablePage:
    def test_page_renders_stubbed_summary(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_caltable(config)
        monkeypatch.setattr(
            caltable_qa,
            "summary",
            lambda path: {
                "name": TABLE,
                "cal_type": "G",
                "quality": {
                    "fraction_flagged": 0.44,
                    "median_snr": 3.1,
                    "issues": ["<script>alert(1)</script>"],
                    "warnings": [],
                },
                "per_spw": [{"spw_id": 0, "fraction_flagged": 0.44, "is_problematic": True}],
                "snr_summary": {"median": 3.1},
                "provenance": {
                    "selection_pool": "bright_fallback",
                    "flux_anchor": "vla_catalog",
                    "calibrator_name": "2253+161",
                    "source": "generated",
                    "cal_date": "2026-01-25",
                },
            },
        )
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/caltable/{TABLE}")
        assert response.status_code == 200
        assert "bright_fallback" in response.text
        assert "<script>alert(1)</script>" not in response.text  # escaped
        assert f"/artifacts/caltable/{TABLE}/plot/gain_amp.png" in response.text
        assert "/runs/2026-01-25" in response.text

    def test_page_tolerates_missing_provenance(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_caltable(config)
        monkeypatch.setattr(
            caltable_qa,
            "summary",
            lambda path: {
                "name": TABLE,
                "cal_type": "G",
                "quality": {},
                "per_spw": [],
                "snr_summary": None,
                "provenance": None,
            },
        )
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/caltable/{TABLE}")
        assert response.status_code == 200
        assert "no provenance sidecar" in response.text.lower()

    def test_status_json(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_caltable(config)
        monkeypatch.setattr(caltable_qa, "summary", lambda path: {"name": TABLE})
        with TestClient(create_app(config)) as client:
            payload = client.get(f"/artifacts/caltable/{TABLE}/status").json()
        assert payload["summary"]["name"] == TABLE
        assert payload["file"]["size_bytes"] > 0
        assert "related" in payload


class TestCaltablePlotRoutes:
    def test_plot_rendered_and_cached(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_caltable(config)
        calls = []

        def fake_render(path, kind, target):
            calls.append(kind)
            target.write_bytes(TINY_PNG)

        monkeypatch.setattr(caltable_qa, "render_plot", fake_render)
        with TestClient(create_app(config)) as client:
            for _ in range(2):
                response = client.get(f"/artifacts/caltable/{TABLE}/plot/snr.png")
                assert response.status_code == 200
                assert response.headers["content-type"] == "image/png"
                assert response.content[:4] == b"\x89PNG"
        assert calls == ["snr"]

    def test_unknown_kind_404(self, tmp_path):
        config = _make_config(tmp_path)
        _make_caltable(config)
        with TestClient(create_app(config)) as client:
            assert client.get(f"/artifacts/caltable/{TABLE}/plot/nope.png").status_code == 404

    def test_bandpass_kind_rejected_for_gain_table(self, tmp_path):
        config = _make_config(tmp_path)
        _make_caltable(config)
        with TestClient(create_app(config)) as client:
            assert (
                client.get(f"/artifacts/caltable/{TABLE}/plot/bandpass_amp.png").status_code == 404
            )

    def test_render_failure_maps_to_424_with_reason(self, tmp_path, monkeypatch):
        config = _make_config(tmp_path)
        _make_caltable(config)

        def broken(path, kind, target):
            raise artifacts.ArtifactRenderError("casacore unavailable")

        monkeypatch.setattr(caltable_qa, "render_plot", broken)
        with TestClient(create_app(config)) as client:
            response = client.get(f"/artifacts/caltable/{TABLE}/plot/snr.png")
        assert response.status_code == 424
        assert "casacore unavailable" in response.text


STAGE_MS = Path("/stage/dsa110-contimg/ms")
requires_stage = pytest.mark.skipif(not STAGE_MS.is_dir(), reason="H17 stage volume not present")


@requires_stage
class TestCaltableLiveIntegration:
    """Issue #56 acceptance: renders for at least one real caltable on stage."""

    def _live_config(self, tmp_path):
        return DashboardConfig(thumb_dir=tmp_path / "thumbs")

    def test_real_caltable_page_renders(self, tmp_path):
        records = artifacts.list_caltables(STAGE_MS, limit=1)
        assert records, "no caltables on stage"
        name = records[0]["name"]
        with TestClient(create_app(self._live_config(tmp_path))) as client:
            response = client.get(f"/artifacts/caltable/{name}")
        assert response.status_code == 200
        assert "Provenance" in response.text

    def test_real_gain_plot_renders(self, tmp_path):
        gains = [
            record
            for record in artifacts.list_caltables(STAGE_MS, limit=40)
            if record["name"].endswith(".g")
        ]
        assert gains, "no gain tables on stage"
        with TestClient(create_app(self._live_config(tmp_path))) as client:
            response = client.get(f"/artifacts/caltable/{gains[0]['name']}/plot/gain_amp.png")
        assert response.status_code == 200
        assert response.content[:4] == b"\x89PNG"
