# Implementation Plan: Phase A — per-artifact QA views (caltable #56, tile #55, MS #54)

---
**Date:** 2026-07-15
**Author:** AI Assistant
**Status:** Implemented 2026-07-15 (PRs #120/#121/#122; record:
[implement-phase-a-artifact-qa-views.md](implement-phase-a-artifact-qa-views.md)).
Manual verification pending Jakob; deviations recorded on the closed issues
**Related Documents:**
- [Campaign roadmap](plan-dashboard-feature-campaign-2026-07-15.md) — Phase A is the first gate row
- [Plan: dashboard production readiness](plan-dashboard-production-readiness.md) — decisions 1–5 bind the substrate
- [Research: dashboard production readiness](research-dashboard-production-readiness.md)
- [Handoff 2026-07-15 09:34](handoff-2026-07-15-09-34-dashboard-ship-gate-campaign.md)

---

## Overview

Phase A closes the "QA gate says why → show me the artifact" loop: when `/runs/2026-01-25` says
the gaincal gate failed with LOW_SNR 44% > 30%, the operator must be able to click through to the
actual `.g` table and see its solutions, flags, SNR, and acquisition provenance. This plan adds
three read-only artifact detail pages on the existing routed qa_server substrate:

- `/artifacts/caltable/{name}` — per-calibration-table view (#56, lands first)
- `/artifacts/tile/{name}` — per-single-tile-FITS view (#55)
- `/artifacts/ms/{name}` — per-Measurement-Set view (#54)

Each page = summary metrics (cached JSON) + a grid of lazily rendered, mtime-cache-keyed PNG
plots + lifecycle/provenance card + cross-links (MS ↔ caltables ↔ tile ↔ hourly-epoch mosaic ↔
`/runs/{date}`). Landing includes systemd adoption of the dashboard service from a dedicated
`dashboard-production` worktree, fixing the stale-process gap documented in the walkthrough.

**Goal:** #54/#55/#56 acceptance criteria met; three PRs merged to `dashboard-production`; the
dashboard running under systemd on H17 serving the new pages against real stage data.

**Motivation:** Ship-gate row A of the campaign roadmap; highest operator value, pure read-only,
no new state.

## Current State Analysis

**Substrate (identical on `hardening-bugfixes-2026-07-15` and `dashboard-production @ b6a303e` —
verified empty `git diff` across `scripts/qa_server.py`, `dsa110_continuum/observability/`,
`tests/`, `ops/systemd/`):**

- `scripts/qa_server.py:740-743` — routed APIRouters (`mosaic_router` at `/artifacts/mosaic`,
  `ops_router`, `control_router`, `legacy_router`); `create_app` at `:1098-1119` registers six
  routers; config on `app.state.dashboard_config` (`:1103`), accessor `_config` at `:71-72`.
- `scripts/qa_server.py:37-54` — `DashboardConfig` frozen dataclass with env-default fields
  (`stage`, `products`, `thumb_dir`, …).
- `scripts/qa_server.py:247-291` — `make_thumbnail`: the established mtime-keyed PNG cache
  pattern (md5 of path+mtime, stale-key cleanup) this plan generalizes.
- `scripts/qa_server.py:18-20` — `matplotlib.use("Agg")` before any pyplot import; tests rely on
  importing qa_server first.
- `tests/test_qa_server.py:55-64` — `_make_config(tmp_path)`; `:98-149` — path-traversal suite
  pattern (payload list, assert `400 ≤ code < 500`, no `root:` leak).
- `scripts/run_cloud_safe_tests.py:16-39` — `CLOUD_SAFE_TESTS` fixed tuple; **does not include**
  `test_qa_server.py` → CI currently never exercises dashboard routes.
- `ops/systemd/dsa110-dashboard.service:7-10` — `WorkingDirectory=/data/dsa110-continuum`,
  `ExecStart=… uvicorn scripts.qa_server:app --port 8767`; **not installed** in
  `/etc/systemd/system` (verified). Port 8767 held by stale manual pid 1224700 (started 01:08:31
  PT, cwd `/data/dsa110-continuum`, token in env). Token env file
  `~/.config/dsa110/dashboard.env` exists (mode 600). Passwordless sudo available.

**QA/visualization backends (surveyed 2026-07-15; details per module):**

- Caltable (all caltable-only, lazy-CASA, metrics-dict producers):
  `qa/calibration_quality.py` — `validate_caltable_quality:209` (→ `CalibrationQualityMetrics`,
  `.to_dict():160`; filename type-inference at `:243-251` expects `kcal/bpcal/gpcal` tokens, so
  production names `*_0~23.b` infer UNKNOWN → generic CPARAM path, acceptable),
  `analyze_per_spw_flagging:721`, `compute_flag_statistics:1798`, `extract_gain_snr:1871`,
  `extract_dterms:1954`.
- Caltable plot producers (save PNGs into an output **directory**, return `list[Path]`):
  `visualization/calibration_plots.py` — `plot_gains:289` (amp/phase toggles),
  `plot_bandpass:41` (casatasks.plotbandpass + matplotlib fallback `_plot_bandpass_fallback:176`),
  `plot_flagging_diagnostics:695`, `plot_gain_snr:848`, `plot_dterm_scatter:975`;
  `visualization/kcal_delay_plots.py` — `plot_kcal_delays:165` (writes `<name>_delay.png`,
  `<name>_delay_hist.png`; MS optional).
- Stability: `qa/calibration_stability_tracker.py` — `CalibrationStabilityTracker(persist=False)`
  (`:163`; persist=True writes SQLite — must NOT be used by the dashboard),
  `update_from_caltable:336`, `generate_report:621` → `CalibrationStabilityReport.to_dict():128`
  with `antenna_details` per antenna.
- Provenance: `calibration/ensure.py` — `load_provenance_sidecar:113` (reads
  `<bp_table>.cal_provenance.json`, follows borrow symlinks); keys from `_build_provenance:136`:
  `selection_mode`, `selection_pool`, `flux_anchor`, `calibrator_*`, `transit_time_iso`,
  `source`, `cal_date`, `bp_table`, `g_table` (+ `borrowed_from` on the borrow path). Older
  sidecars (e.g. 2026-01-25) lack `selection_pool`/`flux_anchor` — display must tolerate absence.
- Tile: `qa/image_gate.py::check_image_quality_for_source_finding:48` (per-FITS gate →
  `ImageQAResult`), `qa/image_metrics.py` (`calculate_psf_correlation:31`,
  `calculate_residual_stats:143`), `visualization/fits_plots.py::plot_fits_image:108` (→ Figure),
  `visualization/beam_plots.py` (`plot_psf_radial_profile:151`, `plot_psf_2d:314`,
  `plot_sidelobe_analysis:569` — take **numpy arrays**, caller loads the FITS plane),
  `visualization/residual_diagnostics.py` (`extract_residuals_from_ms:136` needs
  `CORRECTED_DATA`+`MODEL_DATA` — both present in real meridian MS, verified;
  `plot_residual_histogram:816`).
- MS: `qa/pipeline_quality.py::check_ms_after_conversion:27` (cheap, degrades gracefully),
  `qa/uvw_validation.py::validate_uvw_geometry:98` (`sample_size` bounds cost),
  `qa/rfi_metrics.py` (`calculate_rfi_occupancy:23` full-FLAG read ≈170 MB,
  `get_rfi_waterfall_data:98`), `visualization/uv_plots.py::plot_uv_coverage:38` (takes arrays),
  `visualization/elevation_plots.py::extract_geometry_from_ms:816` (cheap: TIME+PHASE_DIR only;
  keys `times`, `elevation_deg`, `parallactic_angle_deg`, …) + `plot_elevation_vs_time:187`,
  `plot_parallactic_angle_vs_time:308`, `visualization/tsys_plots.py::extract_tsys_from_ms:64`
  (autocorr-amplitude proxy; returns `times`/`tsys`/`antenna_names`) + `plot_tsys_heatmap:534`,
  `visualization/closure_phase_plots.py::extract_closure_phases_from_ms:573` (decimation knobs)
  → `compute_closure_phases:53` → `plot_closure_phase_histogram:168`.
- Cross-linking (tile ⇄ MS ⇄ mosaic): tile stem == MS stem
  (`scripts/batch_pipeline.py:180-187 timestamp_from_fits`); products
  `{ts}-{image,image-pb,residual,residual-pb,psf,dirty,model}.fits` under
  `images/mosaic_{date}/`; hourly mosaic `{date}T{hh}00_mosaic.fits`.

**On-disk reality (H17, 2026-07-15):** 251 `.b`, 263 `.g`, **0 `.k`** under
`/stage/dsa110-contimg/ms/`; canonical name shape `{ts}_0~23.{b,g}` (one stray `verify_*.g`);
caltables are CASA table *directories* (~1872 rows ≈ small/fast); MS ≈1.79 M rows (full DATA
column read ≈1.4 GB — MS-page renders must be lazy, cached, and decimated).

**Runtime deps in casa6:** `casatasks`, `torch`, `healpy`, `httpx`, `fastapi` present;
**`mocpy` and `scattering` MISSING** → coverage-MOC and scattering cards must degrade with an
explicit message (tested), not 500.

**Current Limitations:**
- No per-artifact pages; gate failures name artifacts the dashboard cannot show.
- Dashboard process is stale and manually launched; systemd unit exists but is not installed.
- CI gate never runs the dashboard tests.

## Desired End State

- `/artifacts/caltable/`, `/artifacts/tile/`, `/artifacts/ms/` index pages + detail pages render
  live H17 data with metrics, plots, provenance, lifecycle, and cross-links.
- Every new path-taking route survives the traversal payload suite (400-class, no leak).
- `dsa110-dashboard.service` active under systemd, serving `dashboard-production` from
  `/data/dsa110-continuum-dashboard`, stale pid gone, control API still fail-closed.
- `make test-cloud` runs the new test files (and `test_qa_server.py`) and is green.
- #56, #55, #54 closed with evidence; #51 closed (criteria met by shipped substrate); deviation
  comments posted for the three unwirable modules (below).

**Success Looks Like:** from `/runs/2026-01-25`, an operator clicks the epoch, opens
`2026-01-25T22:26:05_0~23.g`, sees `selection_pool`/`flux_anchor`/calibrator, per-antenna SNR and
flag fractions, gain amp/phase plots — and can jump to the MS and tile pages for the same
timestamp.

## What We're NOT Doing

- [ ] **Wiring `pipeline_hooks.extract_calibration_metrics` (#56 lists it).** It is orphaned
  (zero callers repo-wide) and latently broken: it calls `.get()` on the
  `CalibrationMetrics` **dataclass** returned by `calibration/qa.py::compute_calibration_metrics:200`
  with mismatched field names (`mean_amp` vs `mean_amplitude`, missing delay fields) —
  `AttributeError` is not in its caught-exception tuple. The page uses
  `validate_caltable_quality` + the `extract_*` backends instead. **Landing files a bug issue**
  and comments the deviation on #56.
- [ ] **Wiring `calibration_stability_plots.plot_calibration_stability` (#56 lists it).** Its
  required nested input schema (`antenna_results[i]["amplitude"]["fractional_std_percent"]`, …)
  has **no producer anywhere in the repo**. The stability card instead renders
  `CalibrationStabilityTracker.generate_report()` directly (persist=False). Deviation comment on
  #56.
- [ ] **Wiring `convergence_plots` (#56 lists it).** Nothing in that module consumes a
  `.b/.g/.k` table — it plots self-cal iteration histories / MS residual convergence. Deferred
  to a future self-cal surface. Deviation comment on #56.
- [ ] **Installing `mocpy` / `scattering` into casa6.** Shared-env change; the coverage and
  scattering cards degrade with an explicit 424 message instead. Optional installs noted for
  Jakob in the landing comment.
- [ ] Pre-rendered/event-driven plot pipelines (stage-event tracer is Phase C, #52); WebSockets;
  auth changes; `verify_*.g`-style non-canonical table names (strict allowlist only);
  autopipeline **timer** adoption (only the dashboard service is adopted here — the timer touches
  pipeline scheduling, a separate decision).
- [ ] Retiring `monitor_server.py` (#62 — Phase B).

**Rationale:** grounded scope corrections from the module survey; everything else is later
campaign phases.

## Implementation Approach

**Technical Strategy:** keep HTML+routing in a new `scripts/artifact_pages.py` (three routers,
registered by `create_app`), and put all data/plot glue in three new HTML-free modules under
`dsa110_continuum/observability/` plus one shared substrate module. All CASA/matplotlib imports
stay function-scoped so every new module imports cloud-safe. Every expensive computation
(summary JSON, plot PNG) goes through one mtime-keyed cache helper in `thumb_dir`.

**Key Architectural Decisions:**
1. **Decision:** new module `scripts/artifact_pages.py` for routers/HTML instead of growing
   `qa_server.py` (already 1128 lines → would exceed ~2500).
   - **Trade-off:** one small helper (`_badge`) is duplicated to avoid a circular import
     (`qa_server` imports `artifact_pages`; `artifact_pages` must not import `qa_server` — it
     reads config via `request.app.state.dashboard_config`).
   - **Alternative considered:** everything inline in qa_server (rejected: unreviewable PRs).
2. **Decision:** glue modules `observability/artifacts.py` (shared discovery/validation/cache),
   `caltable_qa.py`, `tile_qa.py`, `ms_qa.py` — repo pattern: importable, unit-testable, no
   FastAPI dependency (mirrors `observability/control.py` split from its router).
3. **Decision:** strict name allowlists (`{ts}_0~23.{b,g,k}`, `{ts}[_meridian].ms`, tile = bare
   `{ts}`) + resolved-path containment check; malformed → 404-class. Follows the
   `_validate_date_epoch` pattern (`qa_server.py:75-77`) and the existing traversal suite.
4. **Decision:** render failures surface as **HTTP 424** with a reason string
   (`ArtifactRenderError`), so missing optional deps (`scattering`, `mocpy`), missing
   `MODEL_DATA`, or casacore-absent environments degrade per-card, never 500, and are testable.
5. **Decision:** systemd unit repointed to a dedicated worktree
   `/data/dsa110-continuum-dashboard` (checked out to `dashboard-production`); the live checkout
   keeps `hardening-bugfixes-2026-07-15` (handoff constraint). Pipeline launches keep executing
   from `/data/dsa110-continuum` via explicit `DSA110_REPO_ROOT` (matches
   `control.py:30-32` default — the scheduled auto-cal path depends on the live checkout's tree).
6. **Decision:** add the new test files **and** `tests/test_qa_server.py` to `CLOUD_SAFE_TESTS`
   (all are tmp_path-based, no CASA import at module scope). If a cloud-CI dependency gap
   surfaces on the PR run, drop only `test_qa_server.py` from the tuple in that PR (new files
   must stay).

**Patterns to Follow:**
- Config dataclass access via `request.app.state` — `scripts/qa_server.py:71-72`.
- mtime-keyed cache with stale-key cleanup — `scripts/qa_server.py:247-291`.
- Traversal test suite — `tests/test_qa_server.py:98-149`.
- Lazy CASA imports at function scope — `qa/rfi_metrics.py`, `qa/calibration_quality.py:25-32`.
- Per-test `TestClient(create_app(config))` context manager — `tests/test_qa_server.py:39`.

**Git mechanics (all phases):** implementation happens in a worktree; the H17 live checkout is
never switched.

```bash
git -C /data/dsa110-continuum worktree add -b phase-a1-caltable-view \
  /data/dsa110-continuum-worktrees/phase-a dashboard-production
export WT=/data/dsa110-continuum-worktrees/phase-a
export PY=/opt/miniforge/envs/casa6/bin/python
cd $WT   # all commands below run here with PYTHONPATH=$WT
```

PR flow: PR 1 = Phases 1+2 (branch `phase-a1-caltable-view`, closes #56, carries both plan docs
+ CLOUD_SAFE_TESTS edit + systemd unit/doc edits); PR 2 = Phase 3 (`phase-a2-tile-view`, closes
#55); PR 3 = Phase 4 (`phase-a3-ms-view`, closes #54). After each merge:
`git fetch origin && git checkout -b <next> origin/dashboard-production`. Before opening each
PR, check for a parallel lane: `gh pr list --state open` + `git reflog` (standing memory:
duplicate-PR incident #109/#110).

## Implementation Phases

### Phase 1: Shared artifact substrate (`observability/artifacts.py`)

**Objective:** name validation, discovery, cross-linking, and the generic mtime-keyed
file cache — everything the three views share, fully cloud-safe.

**Tasks:**

- [ ] **Write the failing tests** — File: `tests/test_artifact_substrate.py` (new)

  ```python
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
          ["..", "../x.b", "a/b.b", f"{TS}_0~23", "x.b", f"{TS}_0~23.q",
           "%2e%2e%2fetc%2fpasswd.b", f"{TS}_0~23.b\n", "verify_2026-01-25T22:26:05.g"],
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
          assert [r["name"] for r in records] == [new.name]

      def test_list_ignores_noncanonical(self, tmp_path):
          _make_caltable(tmp_path, "verify_2026-01-25T22:26:05.g")
          assert artifacts.list_caltables(tmp_path) == []

      def test_list_tiles_dedupes_pb_and_plain(self, tmp_path):
          tile_dir = tmp_path / "mosaic_2026-01-25"
          tile_dir.mkdir()
          (tile_dir / f"{TS}-image.fits").write_bytes(b"F")
          (tile_dir / f"{TS}-image-pb.fits").write_bytes(b"F")
          assert [r["name"] for r in artifacts.list_tiles(tmp_path)] == [TS]


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


  class TestCachedArtifactFile:
      def test_builder_called_once_per_mtime(self, tmp_path):
          calls = []

          def build(target: Path) -> None:
              calls.append(1)
              target.write_bytes(b"PNG")

          for _ in range(2):
              out = artifacts.cached_artifact_file(
                  tmp_path, "caltable", "x.b", "snr", 111.0, ".png", build)
          assert out.read_bytes() == b"PNG" and len(calls) == 1

      def test_new_mtime_rerenders_and_cleans_stale(self, tmp_path):
          build = lambda target: target.write_bytes(b"P")
          first = artifacts.cached_artifact_file(tmp_path, "c", "x.b", "k", 1.0, ".png", build)
          second = artifacts.cached_artifact_file(tmp_path, "c", "x.b", "k", 2.0, ".png", build)
          assert first != second and not first.exists() and second.exists()

      def test_builder_writing_nothing_raises(self, tmp_path):
          with pytest.raises(artifacts.ArtifactRenderError):
              artifacts.cached_artifact_file(
                  tmp_path, "c", "x.b", "k", 1.0, ".png", lambda target: None)
  ```

- [ ] **Run, watch it fail:**
  `PYTHONPATH=$WT $PY -m pytest tests/test_artifact_substrate.py -q` → collection error
  (`ModuleNotFoundError: dsa110_continuum.observability.artifacts`).

- [ ] **Implement** — File: `dsa110_continuum/observability/artifacts.py` (new)

  ```python
  """Shared discovery, validation, and caching for per-artifact dashboard views."""

  from __future__ import annotations

  import hashlib
  import re
  from datetime import datetime, timezone
  from pathlib import Path
  from typing import Callable

  TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
  CALTABLE_NAME_RE = re.compile(rf"({TIMESTAMP})_0~23\.(b|g|k)")
  MS_NAME_RE = re.compile(rf"({TIMESTAMP})(_meridian)?\.ms")
  TILE_TS_RE = re.compile(TIMESTAMP)

  TILE_PRODUCT_SUFFIXES = ("image-pb", "image", "residual-pb", "residual", "psf", "dirty", "model")


  class ArtifactNotFound(Exception):
      """Requested artifact name is malformed or absent from stage."""


  class ArtifactRenderError(Exception):
      """A summary or plot renderer failed for a stated, user-displayable reason."""


  def file_record(path: Path | None) -> dict | None:
      if path is None or not path.exists():
          return None
      stat = path.stat()
      return {
          "path": str(path),
          "size_bytes": stat.st_size,
          "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
      }


  def _contained(root: Path, candidate: Path) -> bool:
      try:
          candidate.resolve().relative_to(root.resolve())
      except ValueError:
          return False
      return True


  def resolve_caltable(ms_dir: Path, name: str) -> Path:
      if not CALTABLE_NAME_RE.fullmatch(name):
          raise ArtifactNotFound(f"invalid caltable name: {name!r}")
      path = ms_dir / name
      if not _contained(ms_dir, path) or not path.is_dir():
          raise ArtifactNotFound(f"no such caltable: {name!r}")
      return path


  def resolve_ms(ms_dir: Path, name: str) -> Path:
      if not MS_NAME_RE.fullmatch(name):
          raise ArtifactNotFound(f"invalid MS name: {name!r}")
      path = ms_dir / name
      if not _contained(ms_dir, path) or not path.is_dir():
          raise ArtifactNotFound(f"no such MS: {name!r}")
      return path


  def tile_products(images_dir: Path, ts: str) -> dict[str, Path | None]:
      """Map product suffix -> existing path (or None) for one tile timestamp."""
      if not TILE_TS_RE.fullmatch(ts):
          raise ArtifactNotFound(f"invalid tile timestamp: {ts!r}")
      tile_dir = images_dir / f"mosaic_{ts[:10]}"
      if not _contained(images_dir, tile_dir):
          raise ArtifactNotFound(f"invalid tile timestamp: {ts!r}")
      found = {
          suffix: (tile_dir / f"{ts}-{suffix}.fits" if (tile_dir / f"{ts}-{suffix}.fits").is_file() else None)
          for suffix in TILE_PRODUCT_SUFFIXES
      }
      if not any(found.values()):
          raise ArtifactNotFound(f"no tile products for {ts!r}")
      return found


  def list_caltables(ms_dir: Path, limit: int = 40) -> list[dict]:
      if not ms_dir.is_dir():
          return []
      found = [
          (path.stat().st_mtime, path)
          for path in ms_dir.iterdir()
          if CALTABLE_NAME_RE.fullmatch(path.name) and path.is_dir()
      ]
      found.sort(reverse=True)
      return [dict(file_record(path), name=path.name) for _, path in found[:limit]]


  def list_ms(ms_dir: Path, limit: int = 48) -> list[dict]:
      if not ms_dir.is_dir():
          return []
      found = [
          (path.stat().st_mtime, path)
          for path in ms_dir.iterdir()
          if MS_NAME_RE.fullmatch(path.name) and path.is_dir()
      ]
      found.sort(reverse=True)
      return [dict(file_record(path), name=path.name) for _, path in found[:limit]]


  def list_tiles(images_dir: Path, limit: int = 48) -> list[dict]:
      if not images_dir.is_dir():
          return []
      by_ts: dict[str, Path] = {}
      for path in images_dir.glob("mosaic_*/*-image*.fits"):
          ts = path.name.split("-image")[0]
          if TILE_TS_RE.fullmatch(ts):
              by_ts.setdefault(ts, path)
      records = sorted(by_ts.items(), key=lambda item: item[1].stat().st_mtime, reverse=True)
      return [dict(file_record(path), name=ts) for ts, path in records[:limit]]


  def related_artifacts(stage: Path, ts: str) -> dict:
      """Cross-links between the artifacts sharing one observation timestamp."""
      if not TILE_TS_RE.fullmatch(ts):
          raise ArtifactNotFound(f"invalid timestamp: {ts!r}")
      ms_dir = stage / "ms"
      date, hour = ts[:10], ts[11:13]
      tile_dir = stage / "images" / f"mosaic_{date}"

      def _existing(name: str) -> str | None:
          return name if (ms_dir / name).exists() else None

      return {
          "ms": _existing(f"{ts}.ms"),
          "ms_meridian": _existing(f"{ts}_meridian.ms"),
          "caltables": [
              name for name in (f"{ts}_0~23.b", f"{ts}_0~23.g", f"{ts}_0~23.k")
              if (ms_dir / name).exists()
          ],
          "tile": ts if any(tile_dir.glob(f"{ts}-image*.fits")) else None,
          "date": date,
          "epoch_token": f"T{hour}00",
          "mosaic_exists": (tile_dir / f"{date}T{hour}00_mosaic.fits").is_file(),
      }


  def cached_artifact_file(
      cache_dir: Path,
      category: str,
      name: str,
      kind: str,
      source_mtime: float,
      suffix: str,
      builder: Callable[[Path], None],
  ) -> Path:
      """Build-once file cache keyed on the source artifact's mtime."""
      key = hashlib.md5(f"{category}{name}{kind}{source_mtime}".encode()).hexdigest()[:10]
      safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{category}_{name}_{kind}")
      target = cache_dir / f"{safe}_{key}{suffix}"
      if target.exists():
          return target
      cache_dir.mkdir(parents=True, exist_ok=True)
      for stale in cache_dir.glob(f"{safe}_*{suffix}"):
          stale.unlink(missing_ok=True)
      tmp = target.with_name(target.name + ".tmp")
      builder(tmp)
      if not tmp.exists():
          raise ArtifactRenderError(f"renderer produced no output for {kind!r}")
      tmp.replace(target)
      return target
  ```

- [ ] **Run, watch it pass:** `PYTHONPATH=$WT $PY -m pytest tests/test_artifact_substrate.py -q`
  → all pass.
- [ ] **Lint:** `ruff check dsa110_continuum/observability/artifacts.py tests/test_artifact_substrate.py`
  and `ruff format --check` the same files.
- [ ] **Commit:** `git commit -m "Add shared artifact discovery, validation, and cache substrate"`

**Verification:**
- [ ] `PYTHONPATH=$WT $PY -m pytest tests/test_artifact_substrate.py -q` → `0 failed`.

### Phase 2: Per-caltable view (#56) → PR 1

**Objective:** `/artifacts/caltable/` index + `/artifacts/caltable/{name}` page with quality
metrics, per-SPW flagging, SNR, provenance sidecar, stability card, and lazy plot routes.

**Tasks:**

- [ ] **Write the failing glue tests** — File: `tests/test_caltable_pages.py` (new; part 1)

  ```python
  """Caltable view: glue units + routed pages (cloud-safe; H17 integration guarded)."""

  from __future__ import annotations

  import base64
  import json
  from pathlib import Path

  import pytest
  from fastapi.testclient import TestClient

  from scripts.qa_server import DashboardConfig, create_app
  from dsa110_continuum.observability import artifacts, caltable_qa

  TS = "2026-01-25T22:26:05"
  TABLE = f"{TS}_0~23.g"
  # 1x1 transparent PNG
  TINY_PNG = base64.b64decode(
      b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
      b"AAAABQABh6FO1AAAAABJRU5ErkJggg=="
  )


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
          sidecar.write_text(json.dumps({"selection_pool": "bright_fallback",
                                         "flux_anchor": "vla_catalog"}))
          prov = caltable_qa.provenance(gtable)
          assert prov["selection_pool"] == "bright_fallback"

      def test_provenance_absent_returns_none(self, tmp_path):
          gtable = tmp_path / TABLE
          gtable.mkdir()
          assert caltable_qa.provenance(gtable) is None
  ```

- [ ] **Run, watch it fail:**
  `PYTHONPATH=$WT $PY -m pytest tests/test_caltable_pages.py -q` → `ModuleNotFoundError:
  dsa110_continuum.observability.caltable_qa`.

- [ ] **Implement the glue** — File: `dsa110_continuum/observability/caltable_qa.py` (new)

  ```python
  """Per-calibration-table QA glue for the dashboard (CASA imports stay function-scoped)."""

  from __future__ import annotations

  import shutil
  from pathlib import Path

  from dsa110_continuum.observability.artifacts import ArtifactRenderError, CALTABLE_NAME_RE

  BASE_KINDS = ("gain_amp", "gain_phase", "flagging", "snr", "dterm", "stability")
  BP_KINDS = ("bandpass_amp", "bandpass_phase")
  K_KINDS = ("delay", "delay_hist")
  _TYPES = {"b": "BP", "g": "G", "k": "K"}


  def caltable_type(name: str) -> str:
      return _TYPES[name.rsplit(".", 1)[1]]


  def plot_kinds(name: str) -> tuple[str, ...]:
      ext = name.rsplit(".", 1)[1]
      if ext == "b":
          return BASE_KINDS + BP_KINDS
      if ext == "k":
          return BASE_KINDS + K_KINDS
      return BASE_KINDS


  def provenance(table_path: Path) -> dict | None:
      """Acquisition provenance from the sidecar written next to the sibling .b table."""
      from dsa110_continuum.calibration.ensure import load_provenance_sidecar

      return load_provenance_sidecar(str(table_path.with_suffix(".b")))


  def summary(table_path: Path) -> dict:
      """Quality metrics + per-SPW flagging + SNR summary + provenance for one table."""
      from dsa110_continuum.qa.calibration_quality import (
          analyze_per_spw_flagging,
          extract_gain_snr,
          validate_caltable_quality,
      )

      quality = validate_caltable_quality(str(table_path)).to_dict()
      per_spw = [
          {
              "spw_id": stat.spw_id,
              "fraction_flagged": stat.fraction_flagged,
              "is_problematic": stat.is_problematic,
          }
          for stat in analyze_per_spw_flagging(str(table_path))
      ]
      try:
          snr_summary = extract_gain_snr(str(table_path)).get("summary")
      except Exception as exc:  # SNR/WEIGHT columns are optional
          snr_summary = {"error": str(exc)}
      return {
          "name": table_path.name,
          "cal_type": caltable_type(table_path.name),
          "quality": quality,
          "per_spw": per_spw,
          "snr_summary": snr_summary,
          "provenance": provenance(table_path),
      }


  def stability_report(table_path: Path, limit: int = 8) -> dict:
      """In-memory trend report over the newest same-type tables (never touches the DB)."""
      from dsa110_continuum.qa.calibration_stability_tracker import CalibrationStabilityTracker

      suffix = "." + table_path.name.rsplit(".", 1)[1]
      siblings = sorted(
          (
              path
              for path in table_path.parent.iterdir()
              if CALTABLE_NAME_RE.fullmatch(path.name) and path.name.endswith(suffix)
          ),
          key=lambda path: path.stat().st_mtime,
      )[-limit:]
      tracker = CalibrationStabilityTracker(persist=False)
      for sibling in siblings:
          tracker.update_from_caltable(str(sibling))
      report = tracker.generate_report().to_dict()
      report["n_tables"] = len(siblings)
      return report


  def render_plot(table_path: Path, kind: str, target: Path) -> None:
      """Render one plot kind to `target`; raise ArtifactRenderError with a reason on failure."""
      workdir = target.parent / f"{target.name}.work"
      shutil.rmtree(workdir, ignore_errors=True)
      workdir.mkdir(parents=True)
      try:
          produced = _render_into(table_path, kind, workdir)
          shutil.move(str(produced), str(target))
      except (ImportError, RuntimeError, OSError, ValueError, KeyError) as exc:
          raise ArtifactRenderError(f"{kind}: {exc}") from exc
      finally:
          shutil.rmtree(workdir, ignore_errors=True)


  def _first(paths) -> Path:
      paths = [Path(p) for p in paths]
      if not paths:
          raise ArtifactRenderError("plot function produced no figure")
      return paths[0]


  def _render_into(table_path: Path, kind: str, workdir: Path) -> Path:
      table = str(table_path)
      if kind in ("gain_amp", "gain_phase"):
          from dsa110_continuum.visualization.calibration_plots import plot_gains

          return _first(
              plot_gains(table, output=workdir,
                         plot_amplitude=kind == "gain_amp", plot_phase=kind == "gain_phase")
          )
      if kind == "flagging":
          from dsa110_continuum.visualization.calibration_plots import plot_flagging_diagnostics

          return _first(plot_flagging_diagnostics(table, output=workdir))
      if kind == "snr":
          from dsa110_continuum.visualization.calibration_plots import plot_gain_snr

          return _first(plot_gain_snr(table, output=workdir))
      if kind == "dterm":
          from dsa110_continuum.visualization.calibration_plots import plot_dterm_scatter

          return _first(plot_dterm_scatter(table, output=workdir))
      if kind in ("bandpass_amp", "bandpass_phase"):
          from dsa110_continuum.visualization.calibration_plots import plot_bandpass

          return _first(
              plot_bandpass(table, output=workdir,
                            plot_amplitude=kind == "bandpass_amp",
                            plot_phase=kind == "bandpass_phase")
          )
      if kind in ("delay", "delay_hist"):
          from dsa110_continuum.visualization.kcal_delay_plots import plot_kcal_delays

          produced = [Path(p) for p in plot_kcal_delays(table, output=workdir)]
          wanted = [p for p in produced if ("_delay_hist" in p.name) == (kind == "delay_hist")]
          return _first(wanted)
      if kind == "stability":
          return _render_stability(table_path, workdir)
      raise ArtifactRenderError(f"unknown plot kind {kind!r}")


  def _render_stability(table_path: Path, workdir: Path) -> Path:
      import matplotlib

      matplotlib.use("Agg")
      import matplotlib.pyplot as plt

      report = stability_report(table_path)
      details = report.get("antenna_details") or {}
      if not details:
          raise ArtifactRenderError("no stability history available on stage")
      antennas = sorted(details, key=int)
      amp = [details[a]["amp_trend_per_obs"] for a in antennas]
      phase = [details[a]["phase_trend_deg_per_obs"] for a in antennas]
      flagged = [
          details[a]["is_drifting_amplitude"] or details[a]["is_drifting_phase"]
          or details[a]["is_outlier"]
          for a in antennas
      ]
      colors = ["#ff6470" if bad else "#4eb8ff" for bad in flagged]
      figure, (ax_amp, ax_phase) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
      ax_amp.bar(range(len(antennas)), amp, color=colors)
      ax_amp.set_ylabel("amp trend / obs")
      ax_phase.bar(range(len(antennas)), phase, color=colors)
      ax_phase.set_ylabel("phase trend (deg/obs)")
      ax_phase.set_xlabel(f"antenna (over {report['n_tables']} tables; red = drifting/outlier)")
      figure.suptitle(f"Gain stability · {table_path.name}")
      out = workdir / "stability.png"
      figure.savefig(out, dpi=110, bbox_inches="tight")
      plt.close(figure)
      return out
  ```

- [ ] **Run, watch the glue tests pass:**
  `PYTHONPATH=$WT $PY -m pytest tests/test_caltable_pages.py::TestCaltableGlue -q`
- [ ] **Commit:** `git commit -m "Add caltable QA glue: summary, provenance, plot renderers"`

- [ ] **Write the failing route tests** — append to `tests/test_caltable_pages.py`:

  ```python
  BAD_PARAMS = ["..", "....", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "passwd",
                "../../../etc/passwd", "%2e%2e", "2026-01-25%0A", "x_0~23.b"]


  class TestCaltableRouteSafety:
      def test_traversal_payloads_rejected(self, tmp_path):
          config = _make_config(tmp_path)
          with TestClient(create_app(config)) as client:
              for payload in BAD_PARAMS:
                  for route in (f"/artifacts/caltable/{payload}",
                                f"/artifacts/caltable/{payload}/status",
                                f"/artifacts/caltable/{payload}/plot/snr.png"):
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
          monkeypatch.setattr(caltable_qa, "summary", lambda path: {
              "name": TABLE, "cal_type": "G",
              "quality": {"fraction_flagged": 0.44, "median_snr": 3.1,
                          "issues": ["<script>alert(1)</script>"], "warnings": []},
              "per_spw": [{"spw_id": 0, "fraction_flagged": 0.44, "is_problematic": True}],
              "snr_summary": {"median": 3.1},
              "provenance": {"selection_pool": "bright_fallback",
                             "flux_anchor": "vla_catalog",
                             "calibrator_name": "2253+161", "source": "generated",
                             "cal_date": "2026-01-25"},
          })
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
          monkeypatch.setattr(caltable_qa, "summary", lambda path: {
              "name": TABLE, "cal_type": "G", "quality": {}, "per_spw": [],
              "snr_summary": None, "provenance": None,
          })
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
              assert client.get(
                  f"/artifacts/caltable/{TABLE}/plot/nope.png").status_code == 404

      def test_bandpass_kind_rejected_for_gain_table(self, tmp_path):
          config = _make_config(tmp_path)
          _make_caltable(config)
          with TestClient(create_app(config)) as client:
              assert client.get(
                  f"/artifacts/caltable/{TABLE}/plot/bandpass_amp.png").status_code == 404

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
  requires_stage = pytest.mark.skipif(
      not STAGE_MS.is_dir(), reason="H17 stage volume not present")


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
          gains = [r for r in artifacts.list_caltables(STAGE_MS, limit=40)
                   if r["name"].endswith(".g")]
          assert gains, "no gain tables on stage"
          with TestClient(create_app(self._live_config(tmp_path))) as client:
              response = client.get(
                  f"/artifacts/caltable/{gains[0]['name']}/plot/gain_amp.png")
          assert response.status_code == 200
          assert response.content[:4] == b"\x89PNG"
  ```

- [ ] **Run, watch route tests fail:** `PYTHONPATH=$WT $PY -m pytest
  tests/test_caltable_pages.py -q -k "not Live"` → 404s on every `/artifacts/caltable` route
  (router not registered).

- [ ] **Implement the router + pages** — File: `scripts/artifact_pages.py` (new)

  ```python
  """Per-artifact QA detail pages: caltable (#56), tile (#55), MS (#54)."""

  from __future__ import annotations

  import html
  import json
  from pathlib import Path

  from fastapi import APIRouter, HTTPException, Request, Response
  from fastapi.responses import HTMLResponse

  from dsa110_continuum.observability import artifacts, caltable_qa

  _STYLE = """<style>
  body{background:#0d1014;color:#e8edf2;font-family:Inter,-apple-system,sans-serif;margin:0}
  .shell{max-width:1250px;margin:auto;padding:22px} a{color:#4eb8ff}
  h2{font-size:1rem;text-transform:uppercase;letter-spacing:.1em;color:#bcc6d0}
  table{border-collapse:collapse;font-size:.83rem;margin:12px 0}
  td,th{padding:8px 12px;border-bottom:1px solid #2a3038;text-align:left}
  th{background:#171b20;color:#9da8b4}
  .badge{display:inline-block;background:var(--badge);color:#081014;padding:3px 8px;
  border-radius:999px;font-size:.68rem;font-weight:800}
  .plot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}
  .plot-grid figure{margin:0;background:#171b20;border:1px solid #2a3038;border-radius:8px;
  padding:10px}
  .plot-grid img{width:100%;background:#090b0e;border-radius:4px;min-height:80px}
  .plot-grid figcaption{font-size:.75rem;color:#87919d;margin-top:6px}
  .muted{color:#87919d}</style>"""


  def _badge(state: str, label: str) -> str:
      colors = {"pass": "#41c97a", "warn": "#d99b35", "fail": "#ff6470", "info": "#4eb8ff"}
      return (f'<span class="badge" style="--badge:{colors.get(state, "#69717d")}">'
              f"{html.escape(label.upper())}</span>")


  def _config(request: Request):
      return request.app.state.dashboard_config


  def _page(title: str, body: str) -> HTMLResponse:
      return HTMLResponse(
          f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)}</title>{_STYLE}</head>
  <body><main class="shell"><p><a href="/">← Dashboard</a> ·
  <a href="/artifacts/caltable/">caltables</a> · <a href="/artifacts/tile/">tiles</a> ·
  <a href="/artifacts/ms/">measurement sets</a></p>{body}</main></body></html>""")


  def _plot_grid(base_url: str, kinds: tuple[str, ...]) -> str:
      figures = "".join(
          f'<figure><img src="{base_url}/plot/{kind}.png" loading="lazy" '
          f'alt="{html.escape(kind)}"><figcaption>{html.escape(kind)}</figcaption></figure>'
          for kind in kinds
      )
      return f'<div class="plot-grid">{figures}</div>'


  def _related_row(related: dict) -> str:
      links = []
      for name in filter(None, (related.get("ms"), related.get("ms_meridian"))):
          links.append(f'<a href="/artifacts/ms/{html.escape(name)}">{html.escape(name)}</a>')
      for name in related.get("caltables", []):
          links.append(
              f'<a href="/artifacts/caltable/{html.escape(name)}">{html.escape(name)}</a>')
      if related.get("tile"):
          links.append(f'<a href="/artifacts/tile/{related["tile"]}">tile {related["tile"]}</a>')
      if related.get("mosaic_exists"):
          links.append(
              f'<a href="/artifacts/mosaic/{related["date"]}/{related["epoch_token"]}/status">'
              f"hourly-epoch mosaic {related['date']}{related['epoch_token']}</a>")
      links.append(f'<a href="/runs/{related["date"]}">run {related["date"]}</a>')
      return " · ".join(links)


  def _cached_summary(config, category: str, name: str, source: Path, builder) -> dict:
      cached = artifacts.cached_artifact_file(
          Path(config.thumb_dir), category, name, "summary",
          source.stat().st_mtime, ".json",
          lambda tmp: tmp.write_text(json.dumps(builder(), default=str)),
      )
      return json.loads(cached.read_text())


  # ---------------------------------------------------------------- caltable (#56)

  caltable_router = APIRouter(prefix="/artifacts/caltable", tags=["caltable artifacts"])


  def _resolve_caltable_or_404(config, name: str) -> Path:
      try:
          return artifacts.resolve_caltable(Path(config.stage) / "ms", name)
      except artifacts.ArtifactNotFound as exc:
          raise HTTPException(status_code=404, detail=str(exc)) from None


  @caltable_router.get("/", response_class=HTMLResponse)
  def caltable_index(request: Request):
      """List the newest calibration tables on stage."""
      config = _config(request)
      records = artifacts.list_caltables(Path(config.stage) / "ms")
      rows = "".join(
          f'<tr><td><a href="/artifacts/caltable/{html.escape(r["name"])}">'
          f'{html.escape(r["name"])}</a></td>'
          f"<td>{caltable_qa.caltable_type(r['name'])}</td>"
          f"<td>{html.escape(r['modified'][:19])}</td></tr>"
          for r in records
      ) or '<tr><td colspan="3" class="muted">No calibration tables on stage</td></tr>'
      return _page("Calibration tables", f"""<h1>Calibration tables</h1>
  <table><thead><tr><th>Table</th><th>Type</th><th>Modified (UTC)</th></tr></thead>
  <tbody>{rows}</tbody></table>""")


  @caltable_router.get("/{name}/status")
  def caltable_status(name: str, request: Request):
      """Machine-readable summary for one calibration table."""
      config = _config(request)
      path = _resolve_caltable_or_404(config, name)
      try:
          summary = _cached_summary(config, "caltable", name, path,
                                    lambda: caltable_qa.summary(path))
      except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
          raise HTTPException(status_code=424, detail=str(exc)) from None
      timestamp = name[:19]
      return {
          "file": artifacts.file_record(path),
          "summary": summary,
          "related": artifacts.related_artifacts(Path(config.stage), timestamp),
          "plot_kinds": list(caltable_qa.plot_kinds(name)),
      }


  @caltable_router.get("/{name}/plot/{kind}.png")
  def caltable_plot(name: str, kind: str, request: Request):
      """Lazily render (and cache) one diagnostic plot for a calibration table."""
      config = _config(request)
      path = _resolve_caltable_or_404(config, name)
      if kind not in caltable_qa.plot_kinds(name):
          raise HTTPException(status_code=404, detail=f"unknown plot kind {kind!r}")
      try:
          png = artifacts.cached_artifact_file(
              Path(config.thumb_dir), "caltable", name, kind,
              path.stat().st_mtime, ".png",
              lambda tmp: caltable_qa.render_plot(path, kind, tmp),
          )
      except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
          raise HTTPException(status_code=424, detail=str(exc)) from None
      return Response(content=png.read_bytes(), media_type="image/png",
                      headers={"Cache-Control": "max-age=300"})


  @caltable_router.get("/{name}", response_class=HTMLResponse)
  def caltable_page(name: str, request: Request):
      """Human-readable per-caltable QA page."""
      config = _config(request)
      path = _resolve_caltable_or_404(config, name)
      record = artifacts.file_record(path)
      try:
          summary = _cached_summary(config, "caltable", name, path,
                                    lambda: caltable_qa.summary(path))
          summary_note = ""
      except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
          summary = {"quality": {}, "per_spw": [], "snr_summary": None, "provenance": None}
          summary_note = f'<p class="muted">metrics unavailable: {html.escape(str(exc))}</p>'
      provenance = summary.get("provenance")
      if provenance:
          prov_rows = "".join(
              f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
              for key, value in provenance.items()
          )
          prov_html = f"<table><tbody>{prov_rows}</tbody></table>"
      else:
          prov_html = ('<p class="muted">No provenance sidecar '
                       "(pre-provenance table or borrowed original missing)</p>")
      quality = summary.get("quality") or {}
      quality_rows = "".join(
          f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
          for key, value in quality.items()
      ) or '<tr><td colspan="2" class="muted">—</td></tr>'
      spw_rows = "".join(
          f"<tr><td>{s['spw_id']}</td><td>{s['fraction_flagged']:.3f}</td>"
          f"<td>{_badge('fail' if s['is_problematic'] else 'pass', 'problem' if s['is_problematic'] else 'ok')}</td></tr>"
          for s in summary.get("per_spw", [])
      ) or '<tr><td colspan="3" class="muted">—</td></tr>'
      timestamp = name[:19]
      related = artifacts.related_artifacts(Path(config.stage), timestamp)
      cal_type = caltable_qa.caltable_type(name)
      return _page(f"Caltable {name}", f"""
  <h1>Calibration table · {html.escape(name)} {_badge('info', cal_type)}</h1>
  <p class="muted">{html.escape(record['path'])} · {record['size_bytes']:,} bytes ·
  modified {html.escape(record['modified'][:19])} ·
  <a href="/artifacts/caltable/{html.escape(name)}/status">JSON</a></p>
  <p>{_related_row(related)}</p>{summary_note}
  <h2>Provenance</h2>{prov_html}
  <h2>Quality metrics</h2><table><tbody>{quality_rows}</tbody></table>
  <h2>Per-SPW flagging</h2>
  <table><thead><tr><th>SPW</th><th>Flagged</th><th></th></tr></thead>
  <tbody>{spw_rows}</tbody></table>
  <h2>Diagnostics</h2>{_plot_grid(f"/artifacts/caltable/{html.escape(name)}",
                                  caltable_qa.plot_kinds(name))}""")
  ```

- [ ] **Register the router** — File: `scripts/qa_server.py:1104-1109`, add one import at the
  top of `create_app` (function scope avoids import-order surprises) and one include:

  ```python
  def create_app(config: DashboardConfig | None = None) -> FastAPI:
      """Create a routed dashboard application."""
      from scripts.artifact_pages import caltable_router

      dashboard_config = config or DashboardConfig()
      dashboard_config.thumb_dir.mkdir(parents=True, exist_ok=True)
      application = FastAPI(title="DSA-110 Continuum Observatory", version="2.0")
      application.state.dashboard_config = dashboard_config
      application.include_router(mosaic_router)
      application.include_router(caltable_router)
      ...
  ```

  And add the artifact nav row to the dashboard subtitle (`scripts/qa_server.py:717`):

  ```python
  <div class="subtitle">Updated {now} · auto-refresh 30s · read-only ·
  <a href="/artifacts/caltable/">caltables</a></div>
  ```

  (Phase 3/4 extend this row with tile/MS links.)

- [ ] **Run, watch route tests pass:**
  `PYTHONPATH=$WT $PY -m pytest tests/test_caltable_pages.py tests/test_qa_server.py -q -k "not Live"`
- [ ] **Run the H17 integration tests:**
  `PYTHONPATH=$WT $PY -m pytest tests/test_caltable_pages.py -q -k "Live"` → 2 passed
  (real caltable page + real gain plot).
- [ ] **Add to the CI gate** — File: `scripts/run_cloud_safe_tests.py:16-39`, extend
  `CLOUD_SAFE_TESTS` (alphabetical position irrelevant — append before the closing paren):

  ```python
      "tests/test_artifact_substrate.py",
      "tests/test_caltable_pages.py",
      "tests/test_qa_server.py",
  ```

  Then run `make test-cloud PYTHON=$PY` from `$WT` → green.
- [ ] **Update the systemd unit for the worktree move** — File:
  `ops/systemd/dsa110-dashboard.service:7-10`:

  ```ini
  WorkingDirectory=/data/dsa110-continuum-dashboard
  Environment=PYTHONPATH=/data/dsa110-continuum-dashboard
  Environment=DSA110_REPO_ROOT=/data/dsa110-continuum
  EnvironmentFile=-/home/ubuntu/.config/dsa110/dashboard.env
  ExecStart=/opt/miniforge/envs/casa6/bin/python -m uvicorn scripts.qa_server:app --host 0.0.0.0 --port 8767 --log-level warning
  ```

  And document the worktree topology in `docs/operations/dashboard.md` (new subsection after
  "Service installation"): the service serves `dashboard-production` from
  `/data/dsa110-continuum-dashboard`; `DSA110_REPO_ROOT` keeps pipeline launches on the live
  checkout; upgrade procedure = `git -C /data/dsa110-continuum-dashboard pull --ff-only && sudo
  systemctl restart dsa110-dashboard`.
- [ ] **Lint:** `ruff check dsa110_continuum/observability/ scripts/artifact_pages.py
  scripts/qa_server.py tests/test_caltable_pages.py tests/test_artifact_substrate.py` +
  `ruff format --check` the same.
- [ ] **Commit + PR 1:** include both plan docs:

  ```bash
  git add dsa110_continuum/observability/ scripts/artifact_pages.py scripts/qa_server.py \
    tests/test_artifact_substrate.py tests/test_caltable_pages.py \
    scripts/run_cloud_safe_tests.py ops/systemd/dsa110-dashboard.service \
    docs/operations/dashboard.md \
    docs/rse/specs/plan-dashboard-feature-campaign-2026-07-15.md \
    docs/rse/specs/plan-phase-a-artifact-qa-views-2026-07-15.md
  git commit -m "Add per-caltable QA view with provenance, plots, and traversal-safe routes (#56)"
  gh pr list --state open   # parallel-lane check BEFORE creating the PR
  gh pr create --base dashboard-production --title "Per-caltable QA view (#56)" \
    --body "Phase A / PR 1 of the dashboard feature campaign. Closes #56. ..."
  ```

**Dependencies:** Phase 1.

**Verification:**
- [ ] `PYTHONPATH=$WT $PY -m pytest tests/test_artifact_substrate.py tests/test_caltable_pages.py tests/test_qa_server.py -q` → 0 failed on H17 (Live tests included).
- [ ] `make test-cloud PYTHON=$PY` green.
- [ ] PR CI green; PR merged to `dashboard-production`.

### Phase 3: Per-tile view (#55) → PR 2

**Objective:** `/artifacts/tile/{ts}` page: image-gate result, DR/residual/PSF metrics, image +
residual + PSF plots, bounded scattering card, MS-domain residual histogram, up/downstream links.

**Tasks:**

- [ ] **Write the failing tests** — File: `tests/test_tile_pages.py` (new)

  ```python
  """Tile view: glue units + routed pages (cloud-safe; H17 integration guarded)."""

  from __future__ import annotations

  import base64
  from pathlib import Path

  import numpy as np
  import pytest
  from astropy.io import fits
  from fastapi.testclient import TestClient

  from scripts.qa_server import DashboardConfig, create_app
  from dsa110_continuum.observability import artifacts, tile_qa

  TS = "2026-01-25T02:01:43"
  TINY_PNG = base64.b64decode(
      b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
      b"AAAABQABh6FO1AAAAABJRU5ErkJggg==")

  BAD_PARAMS = ["..", "....", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "passwd",
                "../../../etc/passwd", "%2e%2e", "2026-01-25%0A", "2026-01-25"]


  def _make_config(tmp_path: Path) -> DashboardConfig:
      return DashboardConfig(
          stage=tmp_path / "stage", products=tmp_path / "products",
          incoming=tmp_path / "incoming", thumb_dir=tmp_path / "thumbs",
          campaign_outputs=tmp_path / "campaign",
          campaign_date="2026-07-13", campaign_hour=11)


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
          assert "residual" not in kinds       # product absent
          assert "residual_hist" not in kinds  # no MS


  class TestTileRoutes:
      def test_traversal_payloads_rejected(self, tmp_path):
          config = _make_config(tmp_path)
          with TestClient(create_app(config)) as client:
              for payload in BAD_PARAMS:
                  for route in (f"/artifacts/tile/{payload}",
                                f"/artifacts/tile/{payload}/status",
                                f"/artifacts/tile/{payload}/plot/image.png"):
                      response = client.get(route)
                      assert 400 <= response.status_code < 500, route
                      assert "root:" not in response.text

      def test_index_and_page_render(self, tmp_path, monkeypatch):
          config = _make_config(tmp_path)
          tile_dir = _make_tile(config)
          # downstream hourly-epoch mosaic must exist for the link to render
          fits.writeto(tile_dir / f"{TS[:10]}T0200_mosaic.fits",
                       np.zeros((4, 4), dtype=np.float32), overwrite=True)
          monkeypatch.setattr(tile_qa, "summary", lambda products, ms_path: {
              "gate": {"overall": "PASS", "dynamic_range": 500.0},
              "residual": {"rms": 0.01}, "psf_correlation": 0.9, "noise": None})
          with TestClient(create_app(config)) as client:
              index = client.get("/artifacts/tile/")
              page = client.get(f"/artifacts/tile/{TS}")
          assert f"/artifacts/tile/{TS}" in index.text
          assert page.status_code == 200
          assert "PASS" in page.text
          assert f"/artifacts/mosaic/{TS[:10]}/T0200/status" in page.text  # downstream link

      def test_plot_route_renders_real_image_plot(self, tmp_path):
          """plot kind 'image' uses fits_plots on the synthetic FITS — no CASA needed."""
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
  ```

- [ ] **Run, watch it fail:** `PYTHONPATH=$WT $PY -m pytest tests/test_tile_pages.py -q -k "not Live"`
  → `ModuleNotFoundError: … tile_qa`.

- [ ] **Implement the glue** — File: `dsa110_continuum/observability/tile_qa.py` (new)

  ```python
  """Per-tile (single-tile FITS) QA glue for the dashboard."""

  from __future__ import annotations

  from dataclasses import asdict
  from pathlib import Path

  from dsa110_continuum.observability.artifacts import ArtifactRenderError

  SCATTER_PATCH = 256
  SCATTER_GRID = 3  # central 3x3 patches only — full-grid scattering is offline QA


  def _best_image(products: dict[str, Path | None]) -> Path:
      image = products.get("image-pb") or products.get("image")
      if image is None:
          raise ArtifactRenderError("no image product for tile")
      return image


  def _load_plane(path: Path):
      import numpy as np
      from astropy.io import fits

      with fits.open(path, memmap=True) as hdus:
          return np.squeeze(hdus[0].data).astype("float32")


  def summary(products: dict[str, Path | None], ms_path: Path | None) -> dict:
      """Gate result + residual stats + PSF correlation for one tile."""
      from dsa110_continuum.qa.image_gate import check_image_quality_for_source_finding
      from dsa110_continuum.qa.image_metrics import (
          calculate_psf_correlation,
          calculate_residual_stats,
      )

      image = _best_image(products)
      gate_kwargs = {}
      if ms_path is not None:
          try:
              from dsa110_continuum.qa.noise_model import _extract_integration_time

              gate_kwargs["integration_time_s"] = _extract_integration_time(str(ms_path))
          except Exception:
              pass  # default 12.88 s stands
      gate = asdict(check_image_quality_for_source_finding(str(image), **gate_kwargs))

      residual_path = products.get("residual-pb") or products.get("residual")
      residual = None
      if residual_path is not None:
          try:
              residual = calculate_residual_stats(str(residual_path))
          except Exception as exc:
              residual = {"error": str(exc)}

      psf_correlation = None
      if products.get("dirty") is not None and products.get("psf") is not None:
          try:
              psf_correlation = float(
                  calculate_psf_correlation(str(products["dirty"]), str(products["psf"])))
          except Exception:
              psf_correlation = None

      return {"gate": gate, "residual": residual, "psf_correlation": psf_correlation,
              "noise": {"integration_time_s": gate_kwargs.get("integration_time_s")}}


  def plot_kinds(products: dict[str, Path | None], ms_available: bool) -> tuple[str, ...]:
      kinds = ["image"]
      if products.get("residual-pb") or products.get("residual"):
          kinds.append("residual")
      if products.get("psf"):
          kinds += ["psf_2d", "psf_radial", "sidelobe"]
      kinds.append("scattering")
      if ms_available:
          kinds.append("residual_hist")
      return tuple(kinds)


  def render_plot(products: dict[str, Path | None], kind: str, target: Path,
                  ms_path: Path | None = None) -> None:
      import matplotlib

      matplotlib.use("Agg")
      import matplotlib.pyplot as plt

      try:
          if kind == "image":
              from dsa110_continuum.visualization.fits_plots import plot_fits_image

              figure = plot_fits_image(str(_best_image(products)), output=str(target))
              plt.close(figure)
          elif kind == "residual":
              from dsa110_continuum.visualization.fits_plots import plot_fits_image

              residual = products.get("residual-pb") or products.get("residual")
              if residual is None:
                  raise ArtifactRenderError("no residual product")
              figure = plot_fits_image(str(residual), output=str(target),
                                       title=f"Residual {residual.name}")
              plt.close(figure)
          elif kind in ("psf_2d", "psf_radial", "sidelobe"):
              from dsa110_continuum.visualization import beam_plots

              if products.get("psf") is None:
                  raise ArtifactRenderError("no PSF product")
              psf = _load_plane(products["psf"])
              renderer = {"psf_2d": beam_plots.plot_psf_2d,
                          "psf_radial": beam_plots.plot_psf_radial_profile,
                          "sidelobe": beam_plots.plot_sidelobe_analysis}[kind]
              figure = renderer(psf, output=str(target))
              plt.close(figure)
          elif kind == "scattering":
              _render_scattering(_best_image(products), target)
          elif kind == "residual_hist":
              _render_residual_hist(ms_path, target)
          else:
              raise ArtifactRenderError(f"unknown plot kind {kind!r}")
      except ArtifactRenderError:
          raise
      except (ImportError, RuntimeError, OSError, ValueError, KeyError) as exc:
          raise ArtifactRenderError(f"{kind}: {exc}") from exc


  def _render_scattering(image_path: Path, target: Path) -> None:
      """Bounded scattering card: score the central 3x3 patch grid only."""
      try:
          import scattering  # noqa: F401
          import torch  # noqa: F401
      except ImportError as exc:
          raise ArtifactRenderError(
              f"scattering library unavailable in this environment: {exc}") from exc
      import matplotlib.pyplot as plt
      import numpy as np

      from dsa110_continuum.qa.scattering_qa import _get_scattering_calculator, score_patch

      data = _load_plane(image_path)
      ny, nx = data.shape
      half = SCATTER_GRID // 2
      cy, cx = ny // 2, nx // 2
      stc = _get_scattering_calculator(SCATTER_PATCH, 7, 4)  # J=7, L=4: check_tile_scattering defaults
      scores = np.full((SCATTER_GRID, SCATTER_GRID), np.nan)
      for row in range(SCATTER_GRID):
          for col in range(SCATTER_GRID):
              y0 = cy + (row - half) * SCATTER_PATCH - SCATTER_PATCH // 2
              x0 = cx + (col - half) * SCATTER_PATCH - SCATTER_PATCH // 2
              if y0 < 0 or x0 < 0 or y0 + SCATTER_PATCH > ny or x0 + SCATTER_PATCH > nx:
                  continue
              patch = data[y0:y0 + SCATTER_PATCH, x0:x0 + SCATTER_PATCH]
              scores[row, col] = score_patch(patch, stc)[0]
      figure, axis = plt.subplots(figsize=(6, 5))
      image = axis.imshow(scores, cmap="RdYlGn", vmin=0.5, vmax=1.0)
      figure.colorbar(image, label="scattering score (central patches)")
      axis.set_title("Scattering QA — central 3×3 patches (bounded; full grid is offline QA)")
      figure.savefig(target, dpi=110, bbox_inches="tight")
      plt.close(figure)


  def _render_residual_hist(ms_path: Path | None, target: Path) -> None:
      import matplotlib.pyplot as plt

      if ms_path is None:
          raise ArtifactRenderError("parent MS not on stage")
      from dsa110_continuum.visualization.residual_diagnostics import (
          extract_residuals_from_ms,
          plot_residual_histogram,
      )

      data = extract_residuals_from_ms(str(ms_path), average_channels=True)
      figure = plot_residual_histogram(data, output=str(target))
      plt.close(figure)
  ```

  (`_get_scattering_calculator(npix, J, L)` is the cached filter-bank accessor at
  `qa/scattering_qa.py:88-96`; `score_patch(patch, stc)` at `:99`.)

- [ ] **Implement the router** — append to `scripts/artifact_pages.py`: `tile_router =
  APIRouter(prefix="/artifacts/tile", …)` with the same four routes as the caltable router,
  substituting: `artifacts.tile_products(Path(config.stage) / "images", name)` for resolution;
  page shows the gate as `_badge("pass"/"warn"/"fail", gate["overall"])` under an `<h2>QA
  gate</h2>` heading, a metrics table from `summary`, `_plot_grid` over
  `tile_qa.plot_kinds(products, ms_available)`, and `_related_row` for upstream MS / downstream
  mosaic. `ms_available` = `related["ms_meridian"] or related["ms"]`; the plot route passes
  `ms_path` through to `render_plot` for `residual_hist`. Full code mirrors the Phase 2 router
  with these substitutions:

  ```python
  tile_router = APIRouter(prefix="/artifacts/tile", tags=["tile artifacts"])


  def _resolve_tile_or_404(config, ts: str) -> dict:
      try:
          return artifacts.tile_products(Path(config.stage) / "images", ts)
      except artifacts.ArtifactNotFound as exc:
          raise HTTPException(status_code=404, detail=str(exc)) from None


  def _tile_ms_path(config, ts: str) -> Path | None:
      related = artifacts.related_artifacts(Path(config.stage), ts)
      name = related.get("ms_meridian") or related.get("ms")
      return (Path(config.stage) / "ms" / name) if name else None


  @tile_router.get("/", response_class=HTMLResponse)
  def tile_index(request: Request):
      """List the newest single-tile FITS products on stage."""
      config = _config(request)
      records = artifacts.list_tiles(Path(config.stage) / "images")
      rows = "".join(
          f'<tr><td><a href="/artifacts/tile/{r["name"]}">{r["name"]}</a></td>'
          f"<td>{html.escape(r['modified'][:19])}</td></tr>"
          for r in records
      ) or '<tr><td colspan="2" class="muted">No tiles on stage</td></tr>'
      return _page("Tiles", f"""<h1>Single-tile FITS</h1>
  <table><thead><tr><th>Tile</th><th>Modified (UTC)</th></tr></thead>
  <tbody>{rows}</tbody></table>""")


  @tile_router.get("/{name}/status")
  def tile_status(name: str, request: Request):
      """Machine-readable summary for one tile."""
      config = _config(request)
      products = _resolve_tile_or_404(config, name)
      source = next(path for path in products.values() if path is not None)
      ms_path = _tile_ms_path(config, name)
      try:
          summary = _cached_summary(config, "tile", name, source,
                                    lambda: tile_qa.summary(products, ms_path))
      except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
          raise HTTPException(status_code=424, detail=str(exc)) from None
      return {
          "products": {k: artifacts.file_record(p) for k, p in products.items()},
          "summary": summary,
          "related": artifacts.related_artifacts(Path(config.stage), name),
          "plot_kinds": list(tile_qa.plot_kinds(products, ms_path is not None)),
      }


  @tile_router.get("/{name}/plot/{kind}.png")
  def tile_plot(name: str, kind: str, request: Request):
      """Lazily render (and cache) one tile diagnostic plot."""
      config = _config(request)
      products = _resolve_tile_or_404(config, name)
      ms_path = _tile_ms_path(config, name)
      if kind not in tile_qa.plot_kinds(products, ms_path is not None):
          raise HTTPException(status_code=404, detail=f"unknown plot kind {kind!r}")
      source = next(path for path in products.values() if path is not None)
      try:
          png = artifacts.cached_artifact_file(
              Path(config.thumb_dir), "tile", name, kind, source.stat().st_mtime, ".png",
              lambda tmp: tile_qa.render_plot(products, kind, tmp, ms_path=ms_path))
      except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
          raise HTTPException(status_code=424, detail=str(exc)) from None
      return Response(content=png.read_bytes(), media_type="image/png",
                      headers={"Cache-Control": "max-age=300"})


  @tile_router.get("/{name}", response_class=HTMLResponse)
  def tile_page(name: str, request: Request):
      """Human-readable per-tile QA page."""
      config = _config(request)
      products = _resolve_tile_or_404(config, name)
      source = next(path for path in products.values() if path is not None)
      ms_path = _tile_ms_path(config, name)
      try:
          summary = _cached_summary(config, "tile", name, source,
                                    lambda: tile_qa.summary(products, ms_path))
          note = ""
      except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
          summary = {"gate": None, "residual": None, "psf_correlation": None}
          note = f'<p class="muted">metrics unavailable: {html.escape(str(exc))}</p>'
      gate = summary.get("gate") or {}
      overall = str(gate.get("overall", "—"))
      gate_badge = _badge({"PASS": "pass", "WARN": "warn"}.get(overall, "fail"), overall)
      gate_rows = "".join(
          f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>"
          for k, v in gate.items()) or '<tr><td colspan="2" class="muted">—</td></tr>'
      product_rows = "".join(
          f"<tr><th>{k}</th><td>{html.escape(p.name) if p else '—'}</td></tr>"
          for k, p in products.items())
      related = artifacts.related_artifacts(Path(config.stage), name)
      return _page(f"Tile {name}", f"""
  <h1>Single-tile FITS · {name} {gate_badge}</h1>
  <p class="muted"><a href="/artifacts/tile/{name}/status">JSON</a></p>
  <p>{_related_row(related)}</p>{note}
  <h2>QA gate</h2><table><tbody>{gate_rows}</tbody></table>
  <h2>Products</h2><table><tbody>{product_rows}</tbody></table>
  <h2>Diagnostics</h2>{_plot_grid(f"/artifacts/tile/{name}",
                                  tile_qa.plot_kinds(products, ms_path is not None))}""")
  ```

  Register in `create_app` (`from scripts.artifact_pages import caltable_router, tile_router`;
  `application.include_router(tile_router)`) and add `· <a href="/artifacts/tile/">tiles</a>` to
  the dashboard subtitle nav.

- [ ] **Run, watch tests pass:** `PYTHONPATH=$WT $PY -m pytest tests/test_tile_pages.py -q`
  (Live included on H17).
- [ ] **Add `"tests/test_tile_pages.py"` to `CLOUD_SAFE_TESTS`**; `make test-cloud PYTHON=$PY`.
- [ ] **Lint, commit, PR 2:**
  `git commit -m "Add per-tile QA view: gate, residual, PSF, bounded scattering (#55)"`;
  parallel-lane check; `gh pr create --base dashboard-production --title "Per-tile QA view (#55)" …`

**Dependencies:** Phase 2 merged (shares `artifact_pages.py` and nav row).

**Verification:**
- [ ] `PYTHONPATH=$WT $PY -m pytest tests/test_tile_pages.py tests/test_caltable_pages.py -q` → 0 failed on H17.
- [ ] Scattering card on a real tile returns 424 with "scattering library unavailable" (expected
  until the library is installed) — `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8767/artifacts/tile/<ts>/plot/scattering.png` → `424`.

### Phase 4: Per-MS view (#54) → PR 3

**Objective:** `/artifacts/ms/{name}` page: conversion/UVW/RFI summary, stage lifecycle, and
bounded lazy plots (UV coverage, elevation/parallactic, RFI waterfall, autocorr-amplitude
proxy, closure phases, bandpass diagnostics when a same-timestamp `.b` exists).

**Tasks:**

- [ ] **Write the failing tests** — File: `tests/test_ms_pages.py` (new)

  ```python
  """MS view: glue units + routed pages (cloud-safe; H17 integration guarded)."""

  from __future__ import annotations

  import base64
  from pathlib import Path

  import pytest
  from fastapi.testclient import TestClient

  from scripts.qa_server import DashboardConfig, create_app
  from dsa110_continuum.observability import artifacts, ms_qa

  TS = "2026-01-25T22:26:05"
  MS = f"{TS}.ms"
  TINY_PNG = base64.b64decode(
      b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
      b"AAAABQABh6FO1AAAAABJRU5ErkJggg==")

  BAD_PARAMS = ["..", "....", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "passwd.ms",
                "../../../etc/passwd", "%2e%2e", f"{TS}.MS"]


  def _make_config(tmp_path: Path) -> DashboardConfig:
      return DashboardConfig(
          stage=tmp_path / "stage", products=tmp_path / "products",
          incoming=tmp_path / "incoming", thumb_dir=tmp_path / "thumbs",
          campaign_outputs=tmp_path / "campaign",
          campaign_date="2026-07-13", campaign_hour=11)


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


  class TestMsRoutes:
      def test_traversal_payloads_rejected(self, tmp_path):
          config = _make_config(tmp_path)
          with TestClient(create_app(config)) as client:
              for payload in BAD_PARAMS:
                  for route in (f"/artifacts/ms/{payload}",
                                f"/artifacts/ms/{payload}/status",
                                f"/artifacts/ms/{payload}/plot/uv_coverage.png"):
                      response = client.get(route)
                      assert 400 <= response.status_code < 500, route
                      assert "root:" not in response.text

      def test_index_page_and_lifecycle(self, tmp_path, monkeypatch):
          config = _make_config(tmp_path)
          _make_ms(config)
          monkeypatch.setattr(ms_qa, "summary", lambda path: {
              "conversion": {"exists": True, "size_bytes": 1}, "conversion_passed": True,
              "uvw": {"is_valid": True}, "rfi": {"total_occupancy": 0.02}})
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
                  assert client.get(
                      f"/artifacts/ms/{MS}/plot/uv_coverage.png").status_code == 200
          assert calls == ["uv_coverage"]

      def test_render_error_maps_to_424(self, tmp_path, monkeypatch):
          config = _make_config(tmp_path)
          _make_ms(config)
          monkeypatch.setattr(ms_qa, "render_plot", lambda p, k, t: (_ for _ in ()).throw(
              artifacts.ArtifactRenderError("casacore unavailable")))
          with TestClient(create_app(config)) as client:
              response = client.get(f"/artifacts/ms/{MS}/plot/uv_coverage.png")
          assert response.status_code == 424


  STAGE_MS = Path("/stage/dsa110-contimg/ms")
  requires_stage = pytest.mark.skipif(
      not STAGE_MS.is_dir(), reason="H17 stage volume not present")


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
              response = client.get(
                  f"/artifacts/ms/{records[0]['name']}/plot/uv_coverage.png")
          assert response.status_code == 200
          assert response.content[:4] == b"\x89PNG"
  ```

- [ ] **Run, watch it fail** → `ModuleNotFoundError: … ms_qa`.

- [ ] **Implement the glue** — File: `dsa110_continuum/observability/ms_qa.py` (new)

  ```python
  """Per-Measurement-Set QA glue for the dashboard (bounded, lazy, cached by the caller)."""

  from __future__ import annotations

  from pathlib import Path

  from dsa110_continuum.observability.artifacts import ArtifactRenderError

  UVW_SAMPLE = 2000
  CLOSURE_LIMITS = {"max_rows": 20000, "row_stride": 8, "max_channels": 4}

  BASE_KINDS = ("uv_coverage", "elevation", "parallactic", "rfi_waterfall",
                "autocorr_heatmap", "closure_hist")


  def _bp_table(ms_path: Path) -> Path | None:
      candidate = ms_path.parent / f"{ms_path.name.split('.')[0].replace('_meridian', '')}_0~23.b"
      return candidate if candidate.is_dir() else None


  def plot_kinds(ms_path: Path) -> tuple[str, ...]:
      kinds = list(BASE_KINDS)
      if _bp_table(ms_path) is not None:
          kinds.append("bandpass_diag")
      return tuple(kinds)


  def summary(ms_path: Path) -> dict:
      """Conversion + UVW + RFI occupancy summary; every section degrades independently."""
      from dsa110_continuum.qa.pipeline_quality import check_ms_after_conversion

      passed, conversion = check_ms_after_conversion(str(ms_path))
      result: dict = {"conversion": conversion, "conversion_passed": bool(passed)}
      try:
          from dsa110_continuum.qa.uvw_validation import validate_uvw_geometry

          uvw = validate_uvw_geometry(str(ms_path), sample_size=UVW_SAMPLE)
          result["uvw"] = {
              "is_valid": uvw.is_valid,
              "n_violations": uvw.n_violations,
              "violation_fraction": uvw.violation_fraction,
              "max_uvw_distance_m": uvw.max_uvw_distance_m,
          }
      except Exception as exc:
          result["uvw"] = {"error": str(exc)}
      try:
          from dsa110_continuum.qa.rfi_metrics import calculate_rfi_occupancy

          occupancy = calculate_rfi_occupancy(ms_path)
          result["rfi"] = {
              "total_occupancy": float(occupancy["total_occupancy"]),
              "n_channels": int(occupancy["n_channels"]),
              "n_rows": int(occupancy["n_rows"]),
          }
      except Exception as exc:
          result["rfi"] = {"error": str(exc)}
      return result


  def _uv_lambda(ms_path: Path):
      import numpy as np

      from dsa110_continuum.adapters.casa_tables import table

      with table(str(ms_path)) as ms:
          uvw = ms.getcol("UVW")
      with table(str(ms_path) + "/SPECTRAL_WINDOW") as spw:
          freq_hz = float(np.mean(spw.getcol("CHAN_FREQ")))
      wavelength_m = 299792458.0 / freq_hz
      return uvw[:, 0] / wavelength_m, uvw[:, 1] / wavelength_m


  def render_plot(ms_path: Path, kind: str, target: Path) -> None:
      import matplotlib

      matplotlib.use("Agg")
      import matplotlib.pyplot as plt

      try:
          if kind == "uv_coverage":
              from dsa110_continuum.visualization.uv_plots import plot_uv_coverage

              u_lambda, v_lambda = _uv_lambda(ms_path)
              figure = plot_uv_coverage(u_lambda, v_lambda, output=str(target),
                                        title=f"UV coverage · {ms_path.name}")
              plt.close(figure)
          elif kind in ("elevation", "parallactic"):
              from dsa110_continuum.visualization.elevation_plots import (
                  extract_geometry_from_ms,
                  plot_elevation_vs_time,
                  plot_parallactic_angle_vs_time,
              )

              geometry = extract_geometry_from_ms(str(ms_path))
              if kind == "elevation":
                  figure = plot_elevation_vs_time(
                      geometry["times"], geometry["elevation_deg"], output=str(target))
              else:
                  figure = plot_parallactic_angle_vs_time(
                      geometry["times"], geometry["parallactic_angle_deg"],
                      output=str(target))
              plt.close(figure)
          elif kind == "rfi_waterfall":
              import numpy as np

              from dsa110_continuum.qa.rfi_metrics import get_rfi_waterfall_data

              waterfall, times, freqs = get_rfi_waterfall_data(ms_path)
              figure, axis = plt.subplots(figsize=(10, 4))
              mesh = axis.imshow(
                  waterfall, origin="lower", aspect="auto", cmap="inferno",
                  extent=[freqs.min() / 1e6, freqs.max() / 1e6, 0, len(np.atleast_1d(times))])
              figure.colorbar(mesh, label="flag occupancy")
              axis.set_xlabel("frequency (MHz)")
              axis.set_ylabel("time bin")
              axis.set_title(f"RFI flag waterfall · {ms_path.name}")
              figure.savefig(target, dpi=110, bbox_inches="tight")
              plt.close(figure)
          elif kind == "autocorr_heatmap":
              from dsa110_continuum.visualization.tsys_plots import (
                  extract_tsys_from_ms,
                  plot_tsys_heatmap,
              )

              data = extract_tsys_from_ms(str(ms_path))
              figure = plot_tsys_heatmap(
                  data["times"], data["tsys"], output=str(target),
                  antenna_names=data.get("antenna_names"),
                  title="Autocorrelation amplitude (uncalibrated Tsys proxy)")
              plt.close(figure)
          elif kind == "closure_hist":
              from dsa110_continuum.visualization.closure_phase_plots import (
                  compute_closure_phases,
                  extract_closure_phases_from_ms,
                  plot_closure_phase_histogram,
              )

              raw = extract_closure_phases_from_ms(str(ms_path), **CLOSURE_LIMITS)
              closure = compute_closure_phases(
                  raw["visibility"], raw["antenna1"], raw["antenna2"])
              figure = plot_closure_phase_histogram(closure, output=str(target))
              plt.close(figure)
          elif kind == "bandpass_diag":
              _render_bandpass_diag(ms_path, target)
          else:
              raise ArtifactRenderError(f"unknown plot kind {kind!r}")
      except ArtifactRenderError:
          raise
      except (ImportError, RuntimeError, OSError, ValueError, KeyError) as exc:
          raise ArtifactRenderError(f"{kind}: {exc}") from exc


  def _render_bandpass_diag(ms_path: Path, target: Path) -> None:
      """Figure 1 of the bandpass diagnostic set (per-antenna amplitude overview)."""
      import shutil
      import tempfile

      from dsa110_continuum.visualization.bandpass_diagnostics import load_data, plot_figure1

      bp_table = _bp_table(ms_path)
      if bp_table is None:
          raise ArtifactRenderError("no same-timestamp bandpass table on stage")
      workdir = Path(tempfile.mkdtemp(prefix="bpdiag_", dir=str(target.parent)))
      try:
          data = load_data(str(ms_path), str(bp_table))
          plot_figure1(data, workdir, ms_path.name)
          pngs = sorted(workdir.glob("*.png"))
          if not pngs:
              raise ArtifactRenderError("bandpass diagnostics produced no figure")
          shutil.move(str(pngs[0]), str(target))
      finally:
          shutil.rmtree(workdir, ignore_errors=True)
  ```

  Deviation notes to carry into the #54 close-out comment: `coverage_moc` is not a per-MS
  module (it reads the pointing-history SQLite DB) **and** `mocpy` is not installed in casa6 —
  the MS page therefore shows no MOC card in v1; `uv_plots.extract_uv_from_ms` is bypassed for
  coverage (it reads the full DATA column; the page reads UVW only); `tsys` values are labeled
  autocorrelation-amplitude proxies, not Kelvin.

- [ ] **Implement the router** — append `ms_router` to `scripts/artifact_pages.py`: same four
  routes with `artifacts.resolve_ms`, `ms_qa.summary` (cached — this one matters: full-FLAG
  read), `ms_qa.plot_kinds(path)`, `ms_qa.render_plot`. The page adds a **Lifecycle** section
  built from `artifacts.related_artifacts(stage, ts)` where `ts = name[:19]`:

  ```python
  @ms_router.get("/{name}", response_class=HTMLResponse)
  def ms_page(name: str, request: Request):
      """Human-readable per-MS QA page with lifecycle state."""
      config = _config(request)
      path = _resolve_ms_or_404(config, name)
      try:
          summary = _cached_summary(config, "ms", name, path,
                                    lambda: ms_qa.summary(path))
          note = ""
      except (artifacts.ArtifactRenderError, RuntimeError, ImportError) as exc:
          summary = {}
          note = f'<p class="muted">metrics unavailable: {html.escape(str(exc))}</p>'
      related = artifacts.related_artifacts(Path(config.stage), name[:19])
      lifecycle = [
          ("Calibration tables", bool(related["caltables"])),
          ("Tile image", related["tile"] is not None),
          ("Hourly-epoch mosaic", related["mosaic_exists"]),
      ]
      lifecycle_rows = "".join(
          f"<tr><th>{stage_name}</th>"
          f"<td>{_badge('pass' if done else 'warn', 'ready' if done else 'not yet')}</td></tr>"
          for stage_name, done in lifecycle)
      summary_rows = "".join(
          f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>"
          for k, v in summary.items()) or '<tr><td colspan="2" class="muted">—</td></tr>'
      return _page(f"MS {name}", f"""
  <h1>Measurement Set · {html.escape(name)}</h1>
  <p class="muted"><a href="/artifacts/ms/{html.escape(name)}/status">JSON</a></p>
  <p>{_related_row(related)}</p>{note}
  <h2>Lifecycle</h2><table><tbody>{lifecycle_rows}</tbody></table>
  <h2>Summary</h2><table><tbody>{summary_rows}</tbody></table>
  <h2>Diagnostics</h2>{_plot_grid(f"/artifacts/ms/{html.escape(name)}",
                                  ms_qa.plot_kinds(path))}""")
  ```

  (index/status/plot routes follow the caltable router shape exactly, with
  `_resolve_ms_or_404` = `artifacts.resolve_ms` wrapped in the 404 handler.) Register
  `ms_router` in `create_app`; extend the nav row with
  `· <a href="/artifacts/ms/">measurement sets</a>`.

- [ ] **Run, watch tests pass:** `PYTHONPATH=$WT $PY -m pytest tests/test_ms_pages.py -q`
  (Live tests on H17 exercise real UVW/geometry extraction; first uv_coverage render on a
  1.79 M-row MS reads a ~40 MB UVW column — seconds, then cached).
- [ ] **Add `"tests/test_ms_pages.py"` to `CLOUD_SAFE_TESTS`**; `make test-cloud PYTHON=$PY`.
- [ ] **Lint, commit, PR 3:**
  `git commit -m "Add per-MS QA view: conversion, UVW, RFI, geometry, closure (#54)"`;
  parallel-lane check; `gh pr create --base dashboard-production --title "Per-MS QA view (#54)" …`

**Dependencies:** Phase 3 merged.

**Verification:**
- [ ] `PYTHONPATH=$WT $PY -m pytest tests/test_ms_pages.py tests/test_tile_pages.py tests/test_caltable_pages.py tests/test_artifact_substrate.py tests/test_qa_server.py -q` → 0 failed on H17.
- [ ] `make test-cloud PYTHON=$PY` green.

### Phase 5: Landing — systemd adoption, restart, live smoke, issue hygiene

**Objective:** dashboard runs under systemd from the `dashboard-production` worktree with all
Phase A pages live; evidence captured; issues updated.

**Tasks:**

- [ ] **Create the service worktree** (after PR 3 merges):

  ```bash
  git -C /data/dsa110-continuum fetch origin
  git -C /data/dsa110-continuum branch -f dashboard-production origin/dashboard-production
  git -C /data/dsa110-continuum worktree add /data/dsa110-continuum-dashboard dashboard-production
  ```

  (If `branch -f` is refused because a prior worktree holds the branch, `git worktree list`
  first — the branch must live only in the new service worktree.)
- [ ] **Verify the stale process before killing it** (evidence: same pid/cmdline as the
  walkthrough): `ps -o pid,lstart,args -p 1224700` → uvicorn `scripts.qa_server`, started
  Jul 15 01:08:31. Then `kill 1224700` and confirm port free: `ss -tlnp | grep 8767` → empty.
- [ ] **Install and start the unit:**

  ```bash
  sudo cp /data/dsa110-continuum-dashboard/ops/systemd/dsa110-dashboard.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now dsa110-dashboard
  systemctl is-active dsa110-dashboard   # → active
  ```

  (Token env file `~/.config/dsa110/dashboard.env` already exists, mode 600 — the unit loads it
  via `EnvironmentFile`.) Rollback if unhealthy: `sudo systemctl stop dsa110-dashboard`, then
  relaunch manually from the live checkout per the pre-Phase-A pattern
  (`docs/operations/dashboard.md`).
- [ ] **Live smoke + evidence capture** into `outputs/dashboard-phase-a-2026-07-15/` (or the
  actual landing date), mirroring `outputs/dashboard-walkthrough-2026-07-15/`:

  ```bash
  EV=/data/dsa110-continuum/outputs/dashboard-phase-a-$(date -u +%F)
  mkdir -p $EV
  curl -fsS http://127.0.0.1:8767/health | tee $EV/health.json
  curl -fsS http://127.0.0.1:8767/artifacts/caltable/ -o $EV/caltable_index.html
  NAME=$(ls /stage/dsa110-contimg/ms/ | grep '_0~23\.g$' | tail -1)
  curl -fsS "http://127.0.0.1:8767/artifacts/caltable/$NAME" -o $EV/caltable_page.html
  curl -fsS "http://127.0.0.1:8767/artifacts/caltable/$NAME/plot/gain_amp.png" -o $EV/gain_amp.png
  TILE=$(basename $(ls /stage/dsa110-contimg/images/mosaic_*/*-image-pb.fits | tail -1) | sed 's/-image-pb.fits//')
  curl -fsS "http://127.0.0.1:8767/artifacts/tile/$TILE" -o $EV/tile_page.html
  MS=$(ls -d /stage/dsa110-contimg/ms/*_meridian.ms | tail -1 | xargs basename)
  curl -fsS "http://127.0.0.1:8767/artifacts/ms/$MS" -o $EV/ms_page.html
  # control surface still fail-closed:
  curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8767/api/runs \
    -H 'Content-Type: application/json' -d '{"date":"2026-01-25","dry_run":true}' | tee $EV/no_token_403.txt   # expect 403
  ```

- [ ] **Issue hygiene** (each with one-line evidence links to the PR + `$EV` files):
  - Close #56, #55, #54 as each PR merges; the #56 comment records the three deviations from
    its module list (`extract_calibration_metrics` orphaned+buggy,
    `plot_calibration_stability` schema has no producer, `convergence_plots` is
    selfcal-domain) and the #54/#55 comments record the `mocpy`/`scattering` degrades with the
    optional-install note for Jakob.
  - File the `pipeline_hooks.extract_calibration_metrics` dataclass/`.get()` bug issue
    (points at `pipeline_hooks.py:167-200` vs `calibration/qa.py:200`).
  - Close #51: acceptance criteria met by the shipped substrate (routers `qa_server.py:740-743`,
    `/artifacts/mosaic` routes, smoke tests) — link this plan.
  - Re-scope or close #59/#60 (shipped "minimal" in the readiness plan; handoff action).
- [ ] **Update the campaign roadmap** — tick row A in
  `docs/rse/specs/plan-dashboard-feature-campaign-2026-07-15.md` (committed in PR 1; the tick is
  a one-line follow-up commit on `dashboard-production`).

**Dependencies:** Phases 2–4 merged.

**Verification:**
- [ ] `systemctl is-active dsa110-dashboard` → `active`; survives `sudo systemctl restart`.
- [ ] All curl checks above return 200 (403 for the token check) with non-empty bodies.
- [ ] `git -C /data/dsa110-continuum-dashboard log -1 --format=%H` equals
  `origin/dashboard-production` HEAD.

## Success Criteria

### Automated Verification

- [ ] `PYTHONPATH=$WT $PY -m pytest tests/test_artifact_substrate.py tests/test_caltable_pages.py tests/test_tile_pages.py tests/test_ms_pages.py tests/test_qa_server.py tests/test_observability_control.py tests/test_observability_hour_state.py -q` → 0 failed on H17.
- [ ] `make test-cloud PYTHON=$PY` green (now includes the four new files + `test_qa_server.py`).
- [ ] `ruff check dsa110_continuum/observability/ scripts/artifact_pages.py scripts/qa_server.py tests/test_artifact_substrate.py tests/test_caltable_pages.py tests/test_tile_pages.py tests/test_ms_pages.py` → clean.
- [ ] Phase 5 curl suite: `/health` 200; three index pages 200; one real caltable/tile/MS page
  each 200; one real PNG per artifact type with `\x89PNG` magic; POST `/api/runs` without token
  → 403.
- [ ] `systemctl is-active dsa110-dashboard` → `active`.

### Manual Verification

- [ ] From `/runs/2026-01-25`, click through epoch → caltable page for the LOW_SNR `.g` — the
  gate reason is explainable from the SNR plot + per-SPW table (the walkthrough's motivating
  case).
- [ ] Provenance card on a 2026-07-13 table shows `bright_fallback` / `vla_catalog`; on the
  2026-01-25 table shows the pre-provenance fallback text, not an error.
- [ ] Plot grid degrades per-card (scattering 424 message; no page-level failure).
- [ ] Jakob reviews the three #56 deviations and the `mocpy`/`scattering` install question
  (async; blocking only for the issue close-outs, not for merges).

### Reproducibility & Correctness

- [ ] Evidence directory `outputs/dashboard-phase-a-<date>/` contains raw curl responses + PNGs
  with the commands recorded above (pattern: `outputs/dashboard-walkthrough-2026-07-15/`).
- [ ] All rendered metrics come from the surveyed, already-validated QA modules — no new
  numerical formulas are introduced by this plan (the only new computation is the bounded 3×3
  scattering grid, which reuses `score_patch` unchanged).

## Testing Strategy

**Unit (in-phase, cloud-safe):** name validation + containment, listings, cache
single-build/mtime-invalidation, provenance sidecar reading (pure JSON), plot-kind gating by
extension/products, route traversal suites for all 12 new path-taking routes, 404/424 mapping,
XSS escaping, per-card degrade paths. External deps mocked by monkeypatching the glue functions
(`caltable_qa.summary`, `*.render_plot`) — never by importing CASA.

**Integration (H17-only, `skipif` guarded):** real caltable page + gain plot (#56 acceptance),
real tile page (#55), real MS page + UV coverage PNG (#54) — these run in the full suite on H17
and skip cleanly on cloud CI.

**Manual:** Phase 5 smoke + walkthrough click-path.

**Test Data:** synthetic 16×16 FITS via `astropy.io.fits.writeto` (existing
`test_qa_server.py` pattern); fake CASA-table directories are bare dirs with `table.dat` (name
validation never opens tables); real data only in guarded tests.

## Migration Strategy

**Migration:** the stale manual uvicorn (pid 1224700) is replaced by the systemd unit in Phase
5; same port, same token env file, same read-only default. The service moves from the live
checkout to `/data/dsa110-continuum-dashboard` (dashboard-production); pipeline launches keep
running from `/data/dsa110-continuum` via `DSA110_REPO_ROOT`.

**Rollback:** `sudo systemctl disable --now dsa110-dashboard`, then manual relaunch from the
live checkout exactly as before (env from `~/.config/dsa110/dashboard.env`). Each PR is
independently revertable on `dashboard-production`; the new routes are additive (no existing
route or template is modified beyond the one-line nav row and router registration).

**Backward Compatibility:** all existing URLs unchanged; `EPOCHS` fallback, legacy `/thumb/...`
route, and control API untouched.

## Risk Assessment

1. **Risk:** first-hit render latency on heavy MS plots (closure/autocorr read visibility
   columns) makes pages feel broken.
   - Likelihood: Medium; Impact: Low.
   - Mitigation: decimation defaults (`CLOSURE_LIMITS`, autocorr rows only), per-plot lazy
     `<img>` loading, mtime cache; summary JSON cached so the page itself is fast.
2. **Risk:** `casatasks.plotbandpass` misbehaves on `_0~23.b` naming or writes CASA logs to cwd.
   - Likelihood: Medium; Impact: Low.
   - Mitigation: `plot_bandpass` has a pure-matplotlib fallback (`calibration_plots.py:176`);
     failures map to a per-card 424; casa log pollution is cosmetic in the worktree.
3. **Risk:** adding `test_qa_server.py` to `CLOUD_SAFE_TESTS` breaks cloud CI on a missing dep.
   - Likelihood: Low; Impact: Low.
   - Mitigation: PR CI shows it pre-merge; drop that one line if red (new files stay).
4. **Risk:** parallel session lands a competing PR (repeat of #109/#110).
   - Likelihood: Low; Impact: Medium.
   - Mitigation: `gh pr list` + `git reflog` check is an explicit task before every PR.
5. **Risk:** systemd unit fails on H17 (port race, env file perms).
   - Likelihood: Low; Impact: Medium (dashboard outage).
   - Mitigation: kill-then-start ordering with `ss` check; documented rollback to manual launch;
     `Restart=on-failure` in the unit.

## Edge Cases and Error Handling

1. **Case:** caltable with no provenance sidecar (all pre-2026-07 tables).
   - Expected: page renders with explicit "No provenance sidecar" text. Tested.
2. **Case:** borrowed (symlinked) `.b` table — `load_provenance_sidecar` follows the symlink
   (`ensure.py:126-131`); `source`/`borrowed_from` display as stored.
3. **Case:** `.k` requested but none exist on stage (current reality) — index lists none; a
   fabricated `.k` URL → 404; kinds `delay`/`delay_hist` only ever offered for `.k` names.
4. **Case:** tile with only `-image.fits` (no `-pb`) — `_best_image` falls back
   (mirrors `mosaic_day.py:488-489`); PSF/residual cards absent from `plot_kinds`, not erroring.
5. **Case:** MS without `MODEL_DATA` / `CORRECTED_DATA` — `residual_hist` and extractor errors
   map to 424 with the underlying reason; summary sections carry `{"error": …}` instead of
   raising.
6. **Case:** casacore-free environment (cloud) — every glue import stays lazy; routes return
   424 "casacore …" per card; all cloud tests monkeypatch above that layer.
7. **Error:** renderer writes nothing — `cached_artifact_file` raises `ArtifactRenderError`
   instead of caching an empty file. Tested.
8. **Case:** two concurrent requests render the same plot — both build to distinct `.tmp` names?
   No: same tmp name; acceptable — worst case is a duplicate render, and `Path.replace` is
   atomic on the same filesystem, so readers never see partial files.

## Performance Considerations

- Caltable pages: table reads are ~MB-scale (1872 rows) — interactive.
- Tile pages: single 4800×4800 float32 plane ≈ 92 MB per FITS read; image/residual/psf renders
  each read one plane, cached thereafter.
- MS pages: summary reads FLAG (~170 MB) once per mtime (cached JSON); UV coverage reads UVW
  only (~40 MB); the two visibility-reading kinds (autocorr, closure) are decimated and cached.
- Cache lives in `thumb_dir` (`/tmp/qa_thumbs` default — tmpfs, wiped on reboot; acceptable:
  everything re-renders lazily; this is cache, not artifact storage).

## Documentation Updates

- [ ] `docs/operations/dashboard.md` — worktree topology, upgrade procedure, new page URLs
  (Phase 2 task).
- [ ] NumPy-style docstrings on every new public function (ruff D-rules for new code).
- [ ] Campaign roadmap row A ticked at landing.

## Timeline Estimate

- Phase 1+2 (PR 1): one focused session.
- Phase 3 (PR 2): ~half session. Phase 4 (PR 3): one session (heaviest glue).
- Phase 5: ~half session including evidence capture.

## Open Questions

*(none — the three unwirable #56 modules and the two missing libraries are resolved as
documented deviations with async review at close-out; everything else was settled by the
2026-07-15 code survey)*

---

## References

**Research inputs (this plan):** module surveys of 2026-07-15 (three parallel code audits:
caltable modules, MS/tile modules, test/systemd substrate) — findings inlined in Current State
Analysis with `file:line` citations.

**Plan/Research Documents:**
- [plan-dashboard-feature-campaign-2026-07-15.md](plan-dashboard-feature-campaign-2026-07-15.md)
- [plan-dashboard-production-readiness.md](plan-dashboard-production-readiness.md)
- [research-dashboard-production-readiness.md](research-dashboard-production-readiness.md)
- [handoff-2026-07-15-09-34-dashboard-ship-gate-campaign.md](handoff-2026-07-15-09-34-dashboard-ship-gate-campaign.md)

**Files Analyzed:** `scripts/qa_server.py`, `tests/test_qa_server.py`,
`scripts/run_cloud_safe_tests.py`, `ops/systemd/*`, `dsa110_continuum/observability/*`,
`dsa110_continuum/qa/{calibration_quality,calibration_stability_tracker,pipeline_hooks,image_gate,image_metrics,scattering_qa,noise_model,uvw_validation,rfi_metrics,pipeline_quality}.py`,
`dsa110_continuum/visualization/{calibration_plots,kcal_delay_plots,calibration_stability_plots,convergence_plots,fits_plots,beam_plots,residual_diagnostics,scattering_diagnostics,uv_plots,elevation_plots,tsys_plots,closure_phase_plots,coverage_moc,bandpass_diagnostics}.py`,
`dsa110_continuum/calibration/ensure.py`, `scripts/{batch_pipeline,mosaic_day}.py`.

**External:** GitHub issues #51, #54, #55, #56 (dsa110/dsa110-continuum).

---

## Review History

### Version 1.0 — 2026-07-15
- Initial plan, authored autonomously from the 2026-07-15 handoff (Direct mode — Jakob away).
  Pending async review items: the three #56 module deviations, the `mocpy`/`scattering`
  optional installs, and the `CLOUD_SAFE_TESTS` expansion including `test_qa_server.py`.
