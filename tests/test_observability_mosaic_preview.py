import os
import re
from pathlib import Path

from dsa110_continuum.observability import mosaic_preview
from dsa110_continuum.observability.hour_state import HourStateConfig, collect_hour_state
from dsa110_continuum.observability.mosaic_preview import (
    dagster_public_url,
    epoch_label,
    mosaic_preview_markdown,
    qa_thumb_url,
    resolve_thumb_source,
    sync_mosaic_thumb,
)

QA_SERVER_EPOCH_RE = re.compile(r"T\d{4}")


def _config(tmp_path: Path, date: str = "2026-07-12", hour: int = 7) -> HourStateConfig:
    return HourStateConfig(
        date=date,
        hour=hour,
        stage=tmp_path / "stage",
        products=tmp_path / "products",
        incoming=tmp_path / "incoming",
        campaign_outputs=tmp_path / "campaign",
        disk_roots=(tmp_path,),
    )


def _isolate_thumb_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DSA110_QA_THUMBS", str(tmp_path / "qa_thumbs"))


class TestEpochTokenContract:
    """Correctness criterion: cross-module epoch-token agreement.

    Reference implementation: scripts/qa_server.py validates epochs with
    EPOCH_RE = `T\\d{4}` (fullmatch), serves thumbnails at
    /artifacts/mosaic/{date}/{epoch}/thumb.png, and resolves mosaics as
    {date}{epoch}_mosaic.fits — the same filename hour_state constructs.
    epoch_label/qa_thumb_url must reproduce those exact tokens and routes for
    every valid hour, zero-padded, or dashboard links 404 silently.
    """

    def test_epoch_label_matches_qa_server_regex_for_all_hours(self):
        for hour in range(24):
            assert QA_SERVER_EPOCH_RE.fullmatch(epoch_label(hour))
        assert epoch_label(5) == "T0500"
        assert epoch_label(23) == "T2300"

    def test_qa_thumb_url_matches_qa_server_route(self):
        assert qa_thumb_url("2026-07-12", 7, base="http://qa:1") == (
            "http://qa:1/artifacts/mosaic/2026-07-12/T0700/thumb.png"
        )

    def test_epoch_label_composes_hour_state_mosaic_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dsa110_continuum.observability.hour_state._process_status", lambda: [])
        image_dir = tmp_path / "stage" / "images" / "mosaic_2026-07-12"
        image_dir.mkdir(parents=True)
        (image_dir / f"2026-07-12{epoch_label(7)}_mosaic.fits").write_text("mosaic")

        state = collect_hour_state(_config(tmp_path))

        assert state["mosaic"] is not None
        assert state["summary"]["mosaic_present"] is True


class TestUrlEnvHandling:
    """Correctness criterion: URL well-formedness under env variation.

    A base URL configured with a trailing slash must not yield `//` in the
    route (route matching would fail), and a wildcard bind host (0.0.0.0/::)
    must be rewritten to loopback because a bind address is not
    browser-routable. Basis: URL syntax and bind-vs-connect address semantics.
    """

    def test_trailing_slash_base_produces_single_slash_route(self, monkeypatch):
        monkeypatch.setenv("DSA110_QA_BASE_URL", "http://host:8767/")

        url = qa_thumb_url("2026-07-12", 7)

        assert url == "http://host:8767/artifacts/mosaic/2026-07-12/T0700/thumb.png"
        assert "//" not in url.split("://", 1)[1]

    def test_wildcard_bind_host_rewritten_to_loopback(self, monkeypatch):
        monkeypatch.delenv("DSA110_DAGSTER_PUBLIC_URL", raising=False)
        monkeypatch.setenv("DSA110_DAGSTER_HOST", "0.0.0.0")
        monkeypatch.setenv("DSA110_DAGSTER_PORT", "1234")

        assert dagster_public_url() == "http://127.0.0.1:1234"


class TestThumbSourceResolution:
    """Correctness criteria for resolve_thumb_source.

    (a) A zero-byte file is never a valid PNG, so empty candidates must be
        skipped (file-format invariant, not a pinned value).
    (b) Candidate precedence follows the documented order: campaign previews
        beat the stage qa_diag copy; the DSA110_QA_THUMBS cache is a last
        resort and within it the newest mtime wins.
    (c) With no candidates the result is None (never a guess).
    Tests use date 2026-07-12 / hour 7 so the hard-coded 2026-07-13 candidate
    directory on H17 cannot leak in.
    """

    def _preview_png(self, tmp_path: Path, content: bytes = b"png-bytes") -> Path:
        preview_dir = tmp_path / "campaign" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        path = preview_dir / "2026-07-12T0700_mosaic_qa_thumb.png"
        path.write_bytes(content)
        return path

    def _stage_png(self, tmp_path: Path) -> Path:
        image_dir = tmp_path / "stage" / "images" / "mosaic_2026-07-12"
        image_dir.mkdir(parents=True, exist_ok=True)
        path = image_dir / "2026-07-12T0700_mosaic_qa_diag.png"
        path.write_bytes(b"diag-bytes")
        return path

    def test_no_candidates_returns_none(self, tmp_path, monkeypatch):
        _isolate_thumb_fallback(monkeypatch, tmp_path)

        assert resolve_thumb_source(_config(tmp_path)) is None

    def test_zero_byte_candidate_skipped(self, tmp_path, monkeypatch):
        _isolate_thumb_fallback(monkeypatch, tmp_path)
        self._preview_png(tmp_path, content=b"")
        stage = self._stage_png(tmp_path)

        assert resolve_thumb_source(_config(tmp_path)) == stage

    def test_campaign_preview_preferred_over_stage_diag(self, tmp_path, monkeypatch):
        _isolate_thumb_fallback(monkeypatch, tmp_path)
        preview = self._preview_png(tmp_path)
        self._stage_png(tmp_path)

        assert resolve_thumb_source(_config(tmp_path)) == preview

    def test_qa_thumbs_fallback_picks_newest_match(self, tmp_path, monkeypatch):
        thumbs = tmp_path / "qa_thumbs"
        thumbs.mkdir()
        monkeypatch.setenv("DSA110_QA_THUMBS", str(thumbs))
        older = thumbs / "2026-07-12_T0700_aaa.png"
        newer = thumbs / "2026-07-12_T0700_bbb.png"
        older.write_bytes(b"old")
        newer.write_bytes(b"new")
        os.utime(older, (2_000, 2_000))
        os.utime(newer, (3_000, 3_000))

        assert resolve_thumb_source(_config(tmp_path)) == newer


class TestSyncMosaicThumb:
    """Correctness criteria for sync_mosaic_thumb.

    (a) With no thumbnail source the call must report synced=False and write
        nothing (read-only-when-nothing-to-do invariant).
    (b) The sync is idempotent: a second call with unchanged inputs succeeds,
        the destination byte-for-byte equals the source, and the static-root
        file set does not grow.
    (c) The hard-coded hour-11 alias files are written only for the
        2026-07-13/11 campaign, never for other epochs.
    webapp_build_dir is patched to None so tests never write into the real
    dagster_webserver site-packages tree.
    """

    def test_no_source_is_unsynced_and_writes_nothing(self, tmp_path, monkeypatch):
        _isolate_thumb_fallback(monkeypatch, tmp_path)
        monkeypatch.setattr(mosaic_preview, "webapp_build_dir", lambda: None)
        static_root = tmp_path / "static"

        result = sync_mosaic_thumb(_config(tmp_path), static_root=static_root)

        assert result["synced"] is False
        assert result["source_path"] is None
        assert not static_root.exists()

    def test_sync_idempotent_and_no_hour11_alias_for_other_epochs(self, tmp_path, monkeypatch):
        _isolate_thumb_fallback(monkeypatch, tmp_path)
        monkeypatch.setattr(mosaic_preview, "webapp_build_dir", lambda: None)
        preview_dir = tmp_path / "campaign" / "previews"
        preview_dir.mkdir(parents=True)
        source = preview_dir / "2026-07-12T0700_mosaic_qa_thumb.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\npixels")
        static_root = tmp_path / "static"
        config = _config(tmp_path)

        first = sync_mosaic_thumb(config, static_root=static_root)
        files_after_first = sorted(p.name for p in (static_root / "dsa110").iterdir())
        second = sync_mosaic_thumb(config, static_root=static_root)
        files_after_second = sorted(p.name for p in (static_root / "dsa110").iterdir())

        assert first["synced"] is True and second["synced"] is True
        assert files_after_first == files_after_second
        dest = static_root / "dsa110" / "2026-07-12_T0700_mosaic_thumb.png"
        assert dest.read_bytes() == source.read_bytes()
        assert not (static_root / "dsa110" / "hour11_mosaic_thumb.png").exists()
        assert not (static_root / "dsa110" / "hour11_mosaic.html").exists()


class TestPreviewMarkdownFallback:
    """Correctness criterion: the markdown always references a renderable image.

    When the static sync did not happen but a qa_server URL exists, the inline
    image must point at qa_server — pointing at the never-written dagster
    static path would render a broken image silently. Basis: the unsynced
    dagster thumb path has no file behind it by construction.
    """

    def test_unsynced_preview_embeds_qa_server_image(self):
        preview = {
            "epoch": "T0700",
            "qa_thumb_url": "http://qa:1/artifacts/mosaic/2026-07-12/T0700/thumb.png",
            "dagster_thumb_url": "http://d:2/dsa110/2026-07-12_T0700_mosaic_thumb.png",
            "dagster_page_url": "http://d:2/dsa110/2026-07-12_T0700_mosaic.html",
            "synced": False,
        }

        md = mosaic_preview_markdown(preview, mosaic_path=None)

        assert f"![mosaic thumbnail]({preview['qa_thumb_url']})" in md
        assert f"![mosaic thumbnail]({preview['dagster_thumb_url']})" not in md
