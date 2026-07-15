"""Unit tests for dsa110_continuum.observability.artifacts (pure filesystem)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dsa110_continuum.observability import artifacts

TS = "2026-01-25T22:26:05"


def _make_caltable(ms_dir: Path, name: str) -> Path:
    path = ms_dir / name
    path.mkdir(parents=True)
    (path / "table.dat").write_bytes(b"x")
    return path


class TestResolveCaltable:
    def test_valid_name_resolves(self, tmp_path):
        _make_caltable(tmp_path, f"{TS}_0~23.b")
        assert artifacts.resolve_caltable(tmp_path, f"{TS}_0~23.b").is_dir()

    @pytest.mark.parametrize(
        "bad",
        [
            "..",
            "../x.b",
            "a/b.b",
            f"{TS}_0~23",
            "x.b",
            f"{TS}_0~23.q",
            "%2e%2e%2fetc%2fpasswd.b",
            f"{TS}_0~23.b\n",
            "verify_2026-01-25T22:26:05.g",
        ],
    )
    def test_malformed_rejected(self, tmp_path, bad):
        with pytest.raises(artifacts.ArtifactNotFound):
            artifacts.resolve_caltable(tmp_path, bad)

    def test_wellformed_but_missing_rejected(self, tmp_path):
        with pytest.raises(artifacts.ArtifactNotFound):
            artifacts.resolve_caltable(tmp_path, f"{TS}_0~23.g")


class TestResolveMs:
    def test_plain_and_meridian_resolve(self, tmp_path):
        for name in (f"{TS}.ms", f"{TS}_meridian.ms"):
            (tmp_path / name).mkdir()
            assert artifacts.resolve_ms(tmp_path, name).is_dir()

    @pytest.mark.parametrize("bad", ["..", "x.ms", f"{TS}.MS", f"{TS}_other.ms"])
    def test_malformed_rejected(self, tmp_path, bad):
        with pytest.raises(artifacts.ArtifactNotFound):
            artifacts.resolve_ms(tmp_path, bad)


class TestTileProducts:
    def test_products_found_by_timestamp(self, tmp_path):
        tile_dir = tmp_path / "mosaic_2026-01-25"
        tile_dir.mkdir()
        for suffix in ("image-pb", "image", "psf"):
            (tile_dir / f"{TS}-{suffix}.fits").write_bytes(b"F")
        products = artifacts.tile_products(tmp_path, TS)
        assert products["image-pb"].is_file()
        assert products["residual"] is None

    def test_missing_tile_raises(self, tmp_path):
        (tmp_path / "mosaic_2026-01-25").mkdir()
        with pytest.raises(artifacts.ArtifactNotFound):
            artifacts.tile_products(tmp_path, TS)

    @pytest.mark.parametrize("bad", ["..", "2026-01-25", f"{TS}x", "a/b"])
    def test_malformed_timestamp_rejected(self, tmp_path, bad):
        with pytest.raises(artifacts.ArtifactNotFound):
            artifacts.tile_products(tmp_path, bad)


class TestListings:
    def test_list_caltables_newest_first_and_limited(self, tmp_path):
        old = _make_caltable(tmp_path, "2026-01-01T00:00:00_0~23.b")
        new = _make_caltable(tmp_path, "2026-02-01T00:00:00_0~23.g")
        os.utime(old, (1, 1))
        records = artifacts.list_caltables(tmp_path, limit=1)
        assert [record["name"] for record in records] == [new.name]

    def test_list_ignores_noncanonical(self, tmp_path):
        _make_caltable(tmp_path, "verify_2026-01-25T22:26:05.g")
        assert artifacts.list_caltables(tmp_path) == []

    def test_list_ms_skips_flagversions(self, tmp_path):
        (tmp_path / f"{TS}.ms").mkdir()
        (tmp_path / f"{TS}_meridian.ms.flagversions").mkdir()
        assert [record["name"] for record in artifacts.list_ms(tmp_path)] == [f"{TS}.ms"]

    def test_list_tiles_dedupes_pb_and_plain(self, tmp_path):
        tile_dir = tmp_path / "mosaic_2026-01-25"
        tile_dir.mkdir()
        (tile_dir / f"{TS}-image.fits").write_bytes(b"F")
        (tile_dir / f"{TS}-image-pb.fits").write_bytes(b"F")
        assert [record["name"] for record in artifacts.list_tiles(tmp_path)] == [TS]


class TestRelatedArtifacts:
    def test_links_across_stage(self, tmp_path):
        (tmp_path / "ms").mkdir()
        (tmp_path / "ms" / f"{TS}.ms").mkdir()
        _make_caltable(tmp_path / "ms", f"{TS}_0~23.b")
        tile_dir = tmp_path / "images" / "mosaic_2026-01-25"
        tile_dir.mkdir(parents=True)
        (tile_dir / f"{TS}-image-pb.fits").write_bytes(b"F")
        (tile_dir / "2026-01-25T2200_mosaic.fits").write_bytes(b"F")
        related = artifacts.related_artifacts(tmp_path, TS)
        assert related["ms"] == f"{TS}.ms"
        assert related["caltables"] == [f"{TS}_0~23.b"]
        assert related["tile"] == TS
        assert related["epoch_token"] == "T2200"
        assert related["mosaic_exists"] is True

    def test_malformed_timestamp_rejected(self, tmp_path):
        with pytest.raises(artifacts.ArtifactNotFound):
            artifacts.related_artifacts(tmp_path, "../etc")


class TestCachedArtifactFile:
    def test_builder_called_once_per_mtime(self, tmp_path):
        calls = []

        def build(target: Path) -> None:
            calls.append(1)
            target.write_bytes(b"PNG")

        for _ in range(2):
            out = artifacts.cached_artifact_file(
                tmp_path, "caltable", "x.b", "snr", 111.0, ".png", build
            )
        assert out.read_bytes() == b"PNG"
        assert len(calls) == 1

    def test_new_mtime_rerenders_and_cleans_stale(self, tmp_path):
        def build(target: Path) -> None:
            target.write_bytes(b"P")

        first = artifacts.cached_artifact_file(tmp_path, "c", "x.b", "k", 1.0, ".png", build)
        second = artifacts.cached_artifact_file(tmp_path, "c", "x.b", "k", 2.0, ".png", build)
        assert first != second
        assert not first.exists()
        assert second.exists()

    def test_builder_writing_nothing_raises(self, tmp_path):
        with pytest.raises(artifacts.ArtifactRenderError):
            artifacts.cached_artifact_file(
                tmp_path, "c", "x.b", "k", 1.0, ".png", lambda target: None
            )
