# Implementation Plan: Retire all `dsa110_contimg` imports — full migration to `dsa110_continuum`

---
**Date:** 2026-07-03
**Author:** AI Assistant (Claude Code)
**Status:** Draft
**Related Documents:**
- [Handoff: caskade simulation control](handoff-2026-07-03-03-16-caskade-simulation-control.md) *(fixed the first latent-NameError instance of the bug class this plan eliminates)*
- [Validation: caskade simulation control](validation-caskade-simulation-control.md)

---

## Overview

`dsa110_continuum` is the pipeline codebase in active development; `dsa110_contimg` is
deprecated and obsolete everywhere. Yet the new package still contains **349 real import
statements** (AST-counted, docstrings excluded) across **126 files** that reach into the old
namespace, held together by three interop layers: per-module `try/except ImportError` guards,
the in-tree `_compat.py`/`_lazy_init.py` stubs, and an out-of-tree cloud `.pth` shim
(`dsa110_contimg_shim.py`). This plan removes every one of them: after completion,
`grep -r "dsa110_contimg" dsa110_continuum/ --include='*.py'` matched by the AST checker
returns **zero real imports**, the package is fully self-contained, and the shim and
`_compat` machinery are deleted.

The migration is **not a rename**. Four symbol families the new package consumes were never
ported — the old `common.utils` toolbox, the `unified_config` settings singleton, the
`infrastructure.database` layer, and the `workflow.pipeline` job framework. These get
**selectively vendored** (only the consumed modules, not the 22k/18k/33k-line source dirs)
from the old source of truth at `/data/dsa110-contimg/backend/src/dsa110_contimg` (H17).
Everything else is a mechanical retarget to ports that already exist.

**Goal:** zero `dsa110_contimg` imports under `dsa110_continuum/`; package imports and full
test suite pass with the old package **absent** (Mac, cloud/CI, no shim) and with it
**present** (H17, old services co-resident) — including no double job-registration.

**Motivation:** the old namespace is unmaintained; the interop layers hide a live bug class
(20 latent import-time `NameError` sites — one already bit in `visibility_models.py`,
fixed in `448d4da`); the cloud shim is uncommitted machine state; and CLAUDE.md currently has
to *forbid* touching nine `__init__.py` files to avoid a registry collision.

## Current State Analysis

Inventory (AST-parsed, this session; raw slices in scratchpad `records.json`/`usage.json`):

| Family | Import stmts | Files | Disposition |
|---|---|---|---|
| `common.*` | 200 | 100 | mostly NONE/STUB — **vendor + retarget** |
<!-- per-family file counts overlap; distinct total = 126 files -->

| `core.*` | 78 | 10 | 9 are `__init__` re-export layers with existing new-path siblings — **flip**; 1 dead runtime import |
| `infrastructure.*` | 51 | 29 | DB layer, never ported — **vendor subset** |
| `workflow.*` | 14 | 8 | job framework, never ported — **vendor**; Dagster bridge — **retire** |
| `interfaces.*` | 4 | 4 | old FastAPI/CLI glue — **retire with clear errors / retarget** |
| bare `dsa110_contimg` | 2 | 2 | health probe + `__version__` — **retarget** |

`scripts/` and `tests/` contain **zero** real old-namespace imports (only checker patterns,
monkeypatch strings, and meta-path blockers).

**Key mechanics (all verified this session):**

- **Registry collision (the CLAUDE.md hazard):** old
  `workflow/pipeline/registry.py:78-81` (H17 source) keys jobs on the `job_type` string and
  raises `ValueError "Job type '<x>' already registered by <name>"` on duplicates.
  `dsa110_continuum/calibration/jobs.py:96,258,411` registers `calibration_solve/apply/validate`
  — the same three `job_type`s the old bootstrap registers. The collision exists **only
  because both packages share the old registry object** via re-exported `register_job`.
  A vendored registry in `dsa110_continuum.workflow` is a distinct object → collision
  impossible by construction.
- **One-way dependency:** the old package has **0** references to `dsa110_continuum`
  (verified on H17). Old production services (uvicorn `dsa110_contimg.interfaces.api.app`
  :8000, Dagster :3000/:3100, gallery watchers — running since March) are structurally
  unaffected by anything this plan changes.
- **`dsa110_continuum` is not installed in casa6** — H17 runs reach it via
  `PYTHONPATH=/data/dsa110-continuum` only.
- **Latent-NameError bug class:** 20 Category-A sites (module-scope use of a soft-imported
  name; module cannot import where the symbol is unresolvable) + 42 Category-B (call-time)
  — full list in the inventory tables embedded per phase below.
- **Re-export layers:** `conversion/`, `rfi/`, `evaluation/` `__init__.py` are already
  migrated (old paths are docstring-only). Nine remain executable:
  `calibration/__init__.py` (19 import lines), `calibration/hardening/`, `calibration/precompute/`,
  `imaging/`, `photometry/`, `qa/`, `visualization/` (~40 symbols in 2 try-blocks),
  `validation/`, `simulation/` (partial). Ported siblings exist for all of them.
- **Dead code found:** `imaging/worker.py:332` imports
  `dsa110_contimg.core.imaging.fast_imaging::run_fast_imaging` — that module does not exist
  in the old package either (verified by `find` on H17). Dead in every environment.
- **Package data:** `dsa110_continuum/simulation/pyuvsim/` already contains
  `telescope.yaml`, `beams.yaml`, `antennas.csv` — `imaging/fov.py:35,39`'s
  `resources.files("dsa110_contimg.simulation.pyuvsim")` is a string retarget.
- **pyproject.toml:** `name = "dsa110_contimg"` (`:6`); all three `[project.scripts]`
  (`:152-154`) target old namespace; `[tool.dg.project] root_module` (`:273`) pins Dagster
  tooling to the old workflow package.
- **Old-source sizes (H17):** `common/utils` 72 files/22,048 lines; `infrastructure/database`
  40/18,310; `workflow` 80/32,588; `interfaces/cli` 29/9,586; `common/unified_config.py`
  1,492 lines (single file). Full-directory ports are off the table; vendoring is selective.

## Desired End State

- `scripts/check_import_migration.py --fail-on-any` (new flag, AST-based) exits 0.
- `dsa110_continuum` imports cleanly and the full suite passes on the Mac (no old package,
  no shim), in CI (cloud subset), and on H17 (old package co-resident, PYTHONPATH mode).
- Importing `dsa110_continuum.calibration` on H17 **with the old bootstrap loaded** raises
  nothing — jobs register into the new package's own registry.
- `_compat.py`, `_lazy_init.py` deleted; every consumer imports real vendored modules directly.
- The cloud `.pth` shim is unnecessary; setup docs no longer mention it.
- `pyproject.toml` has no `dsa110_contimg` references; distribution renamed `dsa110-continuum`.
- CLAUDE.md's "do NOT change the `__init__` re-export layers" block is replaced by the new
  invariant (own registry, no legacy imports).
- CI gains a gate that fails on any new `dsa110_contimg` import.

**Success looks like:** a fresh clone on any machine runs
`python -c "import dsa110_continuum.calibration, dsa110_continuum.photometry, dsa110_continuum.imaging"`
with no old package, no shim, no PYTHONPATH tricks beyond the repo itself.

## What We're NOT Doing

- [ ] **Not porting the old Dagster workflow package** (80 files / 32.6k lines).
      `ScienceMosaicBridgeJob.execute()`'s visibility-domain exec path is retired with a
      clear error pointing at `scripts/batch_pipeline.py` (the production path per
      CLAUDE.md). `SciencePlanningJob` (DB-only) survives.
- [ ] **Not porting the 29-file `dsa110` CLI.** This repo's `dsa110 = dsa110_contimg...`
      entry point never resolved from this package anyway (CLAUDE.md: editable install
      broken for exactly this reason). H17 operators keep the working `dsa110` binary from
      the old install at `/data/dsa110-contimg` — untouched. The entry is dropped from this
      repo's pyproject; `scripts/*.py` cover the documented operations. A native
      `dsa110-continuum` CLI is a separate follow-up.
- [ ] **Not decommissioning old H17 services** (uvicorn :8000, Dagster :3000/:3100,
      watchers). Separate deployment, one-way dependency proven; out of scope.
- [ ] **Not resurrecting the legacy batch-photometry API glue** (`interfaces.api.batch`,
      `job_adapters`): those queue work through the old :8000 service. Call sites get
      explicit `RuntimeError`s naming the replacement (`--photometry-workers`).
- [ ] **Not bulk-fixing pre-existing ruff violations** touched files may contain (repo policy).
- [ ] **Not uninstalling `dsa110_contimg` from casa6 or deleting `/data/dsa110-contimg`.**

**Rationale:** the mandate is import migration — making `dsa110_continuum` self-contained —
not porting every legacy subsystem or operating the old deployment's retirement.

## Implementation Approach

**Technical strategy:** vendor-then-flip, in dependency order. First make the new package
*able* to satisfy every consumed symbol from its own namespace (Phases 1–5), then flip the
re-export layers and delete the interop machinery (Phases 6–7), then rename/retag the
packaging and docs and gate CI (Phase 8). Every phase ends with the AST checker count
strictly decreasing and the test suite green; the count is monotone ratchet state.

**Key architectural decisions:**

1. **Selective vendoring from the H17 source of truth**, preserving public symbol names
   verbatim (e.g. `get_env_path`, `DSA110_LOCATION`, `ensure_pipeline_db`) to keep the
   retargets mechanical.
   - *Rationale:* 349 import sites; changing names multiplies risk.
   - *Alternatives:* full-directory ports (rejected: 70k+ lines of mostly unconsumed code);
     rewriting consumers (rejected: behavioral risk across calibration/imaging paths).
   - Vendored modules keep their own internal graceful degradation (GPU absent, CASA absent)
     — that behavior already exists in the old modules because H17 itself runs without GPU
     on some paths; verified per-module during port (each phase's tests run on the Mac,
     which has neither CASA nor GPU).
2. **Own job/pipeline framework** (`dsa110_continuum/workflow/`): vendor
   `workflow/pipeline/registry.py` + `Job`/`JobResult`/`Pipeline`/`RetryPolicy` bases.
   Kills the double-registration hazard structurally; the mosaic modules' no-op fallbacks
   become real imports.
3. **Namespace layout mirrors the old one** under new roots:
   `dsa110_continuum/utils/` (from `common/utils`), `dsa110_continuum/unified_config.py`
   (from `common/unified_config.py`), `dsa110_continuum/database/` (from
   `infrastructure/database`), `dsa110_continuum/workflow/` (from `workflow/pipeline`).
   One-to-one mapping keeps `git log`/review legible and the retarget table trivial.
4. **Retarget-then-delete guards:** after a symbol is vendored, its consumers switch to a
   **direct, unguarded import** — try/except only survives where the dependency is genuinely
   optional (GPU, caskade). The 174 `except ImportError: pass` blocks are the disease vector;
   they do not outlive the migration.
5. **Vendoring transport:** `git bundle`/`rsync` the needed old-source files from H17 into a
   scratch dir, then commit them (with header comment noting provenance:
   `# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src, 2026-07-03`).
   Intra-old-package imports inside vendored files are rewritten to the new namespace in the
   same commit (the vendored set is closed under the consumed dependency graph; closure is
   checked by importing the module on the Mac).

**Patterns to follow:**
- Soft-import for *genuinely* optional deps: `dsa110_continuum/simulation/__init__.py:101`
  (caskade block).
- No-op decorator fallback shape (only where optionality survives): `visibility_models.py:22-38`.
- Inline `CREATE TABLE IF NOT EXISTS` DB pattern: `calibration/jobs.py`, `photometry/ese_detection.py`.

## Implementation Phases

Phases 2–5 are independent of each other where noted and can be separate PRs; 6 depends on
2–5; 7 on 6; 8 last. Every phase: run `scripts/check_import_migration.py` before/after and
record the delta in the PR body.

### Phase 0: Tooling — make the checker a trustworthy ratchet

**Objective:** AST-accurate stale-import counting + a fail-fast flag, so every later phase
has a mechanical exit criterion.

**Tasks:**
- [x] **Failing test first** — `tests/test_import_migration_checker.py` (new):

  ```python
  import subprocess, sys, textwrap
  from pathlib import Path

  CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "check_import_migration.py"

  def _run(tmp_path, source, *flags):
      pkg = tmp_path / "dsa110_continuum"
      pkg.mkdir()
      (pkg / "mod.py").write_text(textwrap.dedent(source))
      return subprocess.run(
          [sys.executable, str(CHECKER), "--root", str(tmp_path), *flags],
          capture_output=True, text=True,
      )

  def test_docstring_mention_is_not_stale(tmp_path):
      # bare (non-doctest) docstring line: the current line-prefix checker
      # false-positives on this; only an AST implementation passes it
      r = _run(tmp_path, '''
          """Migration note:
          from dsa110_contimg.core.qa import x
          """
          VALUE = 1
      ''', "--fail-on-any")
      assert r.returncode == 0, r.stdout + r.stderr

  def test_real_import_fails_gate(tmp_path):
      r = _run(tmp_path, "from dsa110_contimg.common.utils import get_env_path\n",
               "--fail-on-any")
      assert r.returncode == 1
  ```

- [x] **Run, watch fail:** `pytest tests/test_import_migration_checker.py -v` → first test
      FAILS today (checker is line-prefix based, `scripts/check_import_migration.py:31`,
      and flags docstrings; `--fail-on-any` doesn't exist).
- [x] **Implement** in `scripts/check_import_migration.py`: replace `scan_stale_imports`
      (`:37-52`) with an `ast.walk` visitor collecting `ast.Import`/`ast.ImportFrom` whose
      module root is `dsa110_contimg`; add `--fail-on-any` (exit 1 if count > 0); keep the
      existing per-file report format and `--check-imports`.
- [x] **Run, watch pass**, then record the authoritative baseline:
      `python scripts/check_import_migration.py | tail -3` → expect **349 stale imports /
      126 distinct files** (not the old line-grep 361/129).
- [x] **Commit.**

**Verification:**
- [x] `pytest tests/test_import_migration_checker.py -q` → 2 passed.
- [x] Checker on repo prints 349/126.

### Phase 1: Quick wins — retargets to ports that already exist + dead code

**Objective:** burn down every import whose new-namespace port already exists; delete dead
paths. Kills 6 of the 20 Category-A NameError sites.

Retarget table (old → new, verified REAL this session):

| Old import | New import | Sites |
|---|---|---|
| `get_env_path` — imported from BOTH `common.utils` (e.g. `evaluation/database.py:33`) and `common.utils.env_utils` (`calibration/precompute/precompute.py:45`); one `sd` pattern misses the other | `dsa110_continuum.config::get_env_path` (`config.py:73`) | 16 files, incl. Category-A: `calibration/precompute/precompute.py:45`, `calibration/rfi_adaptive_enhanced.py:51`, `evaluation/database.py:33`, `evaluation/harness.py:39`, `evaluation/stages.py:27`, `pointing/monitor.py:32`, `qa/calibration_stability_tracker.py:48`, `qa/pipeline_hooks.py:39`. Two cautions: (a) `config.py:73` returns `Path` where the old util returned `str` — grep shows no call site depends on str methods, but verify per file during the edit; (b) `qa/pipeline_hooks.py:39` shares its try-block with the `QueryBuilder` import handled in Phase 6 — split the block, retarget only `get_env_path` here, leave the `QueryBuilder` guard intact until Phase 6 |
| `common.utils.exceptions::CalibrationError` | `dsa110_continuum.calibration.ensure::CalibrationError` (`ensure.py:68`) | consumers per inventory |
| `common.utils.validation::validate_ms` | `dsa110_continuum.validation.ms_validator::validate_ms` (`ms_validator.py:666`) | " |
| `common.utils.wsclean_utils::run_chgcentre` | `dsa110_continuum.mosaic.wsclean_mosaic::run_chgcentre` (`wsclean_mosaic.py:381`) | " |
| `workflow...::get_cached_variability_stats` | `dsa110_continuum.photometry.caching::get_cached_variability_stats` (`caching.py:46`) | " |
| `resources.files("dsa110_contimg.simulation.pyuvsim")` | `resources.files("dsa110_continuum.simulation.pyuvsim")` | `imaging/fov.py:35,39` |

**Tasks:**
- [x] **Failing test first** — `tests/test_no_latent_nameerror_imports.py` (new; grows over
      later phases; start with the modules fixed here):

  ```python
  import importlib, pytest

  PHASE1_MODULES = [
      "dsa110_continuum.calibration.precompute.precompute",
      "dsa110_continuum.calibration.rfi_adaptive_enhanced",
      "dsa110_continuum.evaluation.database",
      "dsa110_continuum.evaluation.harness",
      "dsa110_continuum.evaluation.stages",
      "dsa110_continuum.pointing.monitor",
      "dsa110_continuum.qa.calibration_stability_tracker",
      "dsa110_continuum.qa.pipeline_hooks",
      "dsa110_continuum.imaging.fov",
  ]

  @pytest.mark.parametrize("mod", PHASE1_MODULES)
  def test_module_imports_without_legacy_package(mod):
      importlib.import_module(mod)  # NameError/ImportError = fail
  ```

- [x] **Run, watch fail** on the Mac (no old package): the Category-A modules raise
      `NameError: name 'get_env_path' is not defined`.
- [x] **Implement:** per file, replace the try/except import block with the direct new
      import (e.g. in `evaluation/database.py:33`:
      `from dsa110_continuum.config import get_env_path` — delete the try/except entirely).
      For `fov.py`, change the two `resources.files(...)` strings and retarget
      `load_yaml_with_env` later (Phase 2) — leave its guard in place until then.
- [x] **Delete dead code — surgically.** `imaging/worker.py:325-380` is the whole body of
      `_submit_imaging_tasks` (live deep-imaging + GPU submission included), consumed at
      `worker.py:458` as the 3-tuple `future_deep, future_fast, future_gpu` and by
      `_wait_for_imaging_results`. Only the fast-imaging leg is dead (import at `:332`
      targets `core.imaging.fast_imaging`, absent even from the old package — verified on
      H17). Remove just the `run_fast_imaging` import and the `future_fast` submission,
      fix the tuple unpack at `:458` and `_wait_for_imaging_results` accordingly; leave
      deep/GPU legs untouched. (`worker.py:285`'s `resolve_paths` is a separate live import
      — Phase 2 vendors `utils.paths`.) Add
      `tests/test_imaging_worker_no_fast_imaging.py` asserting the legacy string is gone:

  ```python
  from pathlib import Path

  def test_worker_has_no_legacy_fast_imaging():
      src = Path("dsa110_continuum/imaging/worker.py").read_text()
      assert "dsa110_contimg.core.imaging.fast_imaging" not in src
  ```

- [x] **Run, watch pass; commit** (one commit per retarget group is fine).

**Verification:**
- [x] `pytest tests/test_no_latent_nameerror_imports.py tests/test_imaging_worker_no_fast_imaging.py -q` → all pass.
- [x] Checker count drops by ≥ 25.

### Phase 2: Vendor `common.utils` subset → `dsa110_continuum/utils/`

**Objective:** the dominant family (200 imports / 100 files) gets real in-package homes.

**Vendored module set** (from `/data/dsa110-contimg/backend/src/dsa110_contimg/common/utils/`,
only these files): `constants.py` (DSA110_LOCATION/LAT/LON/ALT, OUTRIGGER_ANTENNAS,
CASA_LOG_DIR, CONTIMG_TMPFS_DIR, DEFAULT_YEAR_RANGE), `time_utils.py`, `coordinates.py`,
`fits_utils.py`, `yaml_loader.py`, `templates.py` + `template_styles.py` + the
`common/templates/` asset dir → `dsa110_continuum/templates/`, `antpos_local.py` (+ its CSV
data), `ms_locking.py`, `fuse_lock.py`, `run_isolation.py`, `progress.py`, `plotting.py`,
`numba_accel.py`, `casa_init.py`, `gpu_utils.py`, `gpu_safety.py`, `ms_permissions.py`,
`runtime_safeguards.py`, `decorators.py` (timed/timed_debug/track_performance),
`exceptions.py`, logging helpers (log_context/log_exception/configure_logging_from_args),
uvh5 helpers (open_uvh5_metadata/peek_uvh5_phase_and_midtime), wsclean helpers
(build_wsclean_native_env/check_chgcentre_available), `paths.py` (get_repo_root,
resolve_paths — live at `imaging/worker.py:285`, `calibration/catalog_registry.py:34`,
`simulation/make_synthetic_uvh5.py:38`, `visibility_models.py:19`),
`antenna_classification.py` (get_outrigger_antennas, select_outrigger_refant —
`calibration/validate.py:18`), `env_utils.py` symbols (fold into `config.py` alongside
get_env_path/get_env_int/get_env_list), misc singletons the inventory lists
(get_ms_metadata/get_ms_mid_mjd, wrap_phase_deg, get_temp_subdir, ensure_scratch_dirs,
format_ms_error_with_suggestions, BandpassChannelMonitor, StageProgressMonitor,
estimate_calibration_time/estimate_imaging_time, get_2d_data_and_wcs lives in fits_utils,
get_itrf in antpos_local, get_env_int/get_env_list → extend `config.py`).

**Tasks (repeat this unit per vendored module; concrete example shown for `constants`):**
- [x] **Failing test first** — extend `tests/test_vendored_utils.py` (new):

  ```python
  def test_constants_present_and_sane():
      from dsa110_continuum.utils.constants import (
          DSA110_LOCATION, DSA110_LATITUDE, DSA110_LONGITUDE, OUTRIGGER_ANTENNAS,
      )
      assert 36.0 < DSA110_LATITUDE < 38.0      # OVRO ~37.23°N
      assert -119.0 < DSA110_LONGITUDE < -117.0  # ~-118.28°E
      assert len(OUTRIGGER_ANTENNAS) > 0

  def test_time_utils_roundtrip():
      from dsa110_continuum.utils.time_utils import jd_to_mjd
      assert jd_to_mjd(2400000.5) == 0.0  # MJD epoch definition (oracle)

  def test_coordinates_oracle():
      from dsa110_continuum.utils.coordinates import hms_to_deg, dms_to_deg
      assert abs(hms_to_deg("12:00:00") - 180.0) < 1e-9
      assert abs(dms_to_deg("-30:00:00") + 30.0) < 1e-9
  ```

- [x] **Run, watch fail** (`ModuleNotFoundError: dsa110_continuum.utils`).
- [x] **Vendor:** copy the module from H17 (`scp h17:$OLD/common/utils/constants.py dsa110_continuum/utils/`),
      add the provenance header, rewrite any internal `dsa110_contimg.*` imports to already-
      vendored peers (dependency-order the copies; `constants` first — it has none).
- [x] **Run, watch pass.**
- [x] **Retarget consumers in one mechanical batch per module** — from the inventory mapping,
      e.g. for constants:

  ```bash
  rtk grep -rl 'dsa110_contimg.common.utils.constants' dsa110_continuum/ --include='*.py' \
    | xargs sd 'from dsa110_contimg\.common\.utils\.constants import' \
               'from dsa110_continuum.utils.constants import'
  ```

      then hand-delete each site's now-dead `try/except` wrapper (the import becomes
      unconditional) — including the remaining Category-A sites this phase owns:
      `visualization/bandpass_diagnostics.py:27` (OUTRIGGER_ANTENNAS),
      `visualization/elevation_plots.py:48` (DSA110_LATITUDE/LONGITUDE),
      `photometry/ese_detection_enhanced.py:19` (get_logger — see Phase 4 note; if its
      logger comes from workflow structured_logging, that site moves to Phase 4).
- [x] **Extend `tests/test_no_latent_nameerror_imports.py`** with this phase's modules;
      run; commit per module batch: `git commit -m "Vendor utils.constants; retarget N sites"`.
- [x] Special case `visibility_models.py:18-38` + `_compat` consumers: once
      `utils/gpu_safety.py`, `utils/decorators.py`, `utils/ms_permissions.py` are vendored,
      swap each `try old / except: from _compat import ...` block for the direct vendored
      import (e.g. `calibration/applycal.py:28-47` becomes
      `from dsa110_continuum.utils.gpu_safety import gpu_safe, is_gpu_available, ...`).
      `visibility_models.py`'s local `Stability`/`stability` fallback is replaced by
      `from dsa110_continuum.utils.runtime_safeguards import Stability, stability` (vendor
      the real one; it is annotation-only metadata).

**Dependencies:** Phase 0 (ratchet), Phase 1 recommended first (removes overlap).

**Verification:**
- [x] `pytest tests/test_vendored_utils.py tests/test_no_latent_nameerror_imports.py -q` green on Mac.
- [x] Checker: `common.*` count ≤ 30 (residual = unified_config + templates stragglers mid-phase; 0 at phase end except `unified_config`).
- [x] `pytest tests/ -q` full local suite green.

### Phase 3: Vendor `unified_config` (the `settings` singleton)

**Objective:** unblock the 13 `settings`/`get_settings` importers — 4 of them Category-A
(`calibration/rfi_adaptive_thresholds.py:54→61`, `catalog/flux_monitoring.py:17→23`,
`catalog/spectral_index.py:22→28`, `catalog/variable_source_detection.py:17→23`; all use
`settings.paths.pipeline_db` at module scope).

**Tasks:**
- [x] **Failing test first** — `tests/test_unified_config.py` (new):

  ```python
  def test_settings_singleton_paths():
      from dsa110_continuum.unified_config import settings, get_settings
      assert settings is get_settings()
      # attribute used at module scope by the four Category-A consumers:
      assert settings.paths.pipeline_db  # non-empty path-like
  ```

- [x] **Run, watch fail.**
- [x] **Vendor** `common/unified_config.py` (1,492 lines, single file) →
      `dsa110_continuum/unified_config.py`; rewrite its internal imports to vendored
      utils/constants; default paths must respect the same env vars the old one read (the
      module is env-driven; the test above must pass on a machine with none of the H17
      paths present — if the old module hard-fails without env, wrap defaults with the repo
      convention from `config.py:73`'s `get_env_path`).
- [x] **Run, watch pass.**
- [x] **Retarget the 13 importers**; delete their guards; extend the NameError test module
      list with the 4 Category-A files.
- [x] **Commit.**

**Dependencies:** Phase 2 (utils/constants for path defaults).

**Verification:**
- [x] `pytest tests/test_unified_config.py -q` green on Mac; the 4 catalog/calibration
      modules import cleanly.
- [x] Checker: `common.*` = 0.

### Phase 4: Vendor the job/pipeline framework → `dsa110_continuum/workflow/`

**Objective:** own registry; flip `calibration/{jobs,pipeline}.py` to hard imports; remove
the mosaic no-op fallbacks; make the calibration `__init__` flip (Phase 6) safe.

**Vendored set** (from old `workflow/pipeline/`): `registry.py` (JobRegistry +
`register_job`, `registry.py:78-84` semantics kept verbatim — including the duplicate
`ValueError`; the test's API surface is confirmed in the old source: `unregister` `:86`,
`get` `:97`, `__contains__` `:146`), the `Job`/`JobResult` base module, `Pipeline`/`register_pipeline`,
`RetryPolicy`/`RetryBackoff`/`NotificationConfig`, `PipelineExecutor`, and
`workflow/structured_logging.py` (`get_logger`, `set_correlation_id`, `log_ese_detection`)
→ `dsa110_continuum/workflow/{registry,base,pipeline,executor,structured_logging}.py`
mirroring old file boundaries.

**Tasks:**
- [x] **Failing test first** — `tests/test_workflow_registry.py` (new):

  ```python
  import pytest

  def test_register_and_duplicate_rejection():
      from dsa110_continuum.workflow import Job, register_job, job_registry

      class _T(Job):
          job_type = "test_job_xyz"

      register_job(_T)
      assert job_registry.get("test_job_xyz") is _T
      with pytest.raises(ValueError, match="already registered"):
          register_job(_T)
      job_registry.unregister("test_job_xyz")

  def test_calibration_jobs_register_into_own_registry():
      import dsa110_continuum.calibration.jobs  # noqa: F401  (module-scope @register_job)
      from dsa110_continuum.workflow import job_registry
      assert "calibration_solve" in job_registry
      assert "calibration_apply" in job_registry
      assert "calibration_validate" in job_registry
  ```

- [x] **Run, watch fail** (no `dsa110_continuum.workflow`; today
      `calibration/jobs.py` doesn't even import on the Mac — `register_job` undefined at
      `jobs.py:96` after the `except: pass` at `:24`).
- [x] **Vendor + `workflow/__init__.py`** exporting `Job, JobResult, register_job,
      job_registry, Pipeline, register_pipeline, RetryPolicy, RetryBackoff,
      NotificationConfig, PipelineExecutor`.
- [x] **Flip consumers to hard imports:** `calibration/jobs.py:24`,
      `calibration/pipeline.py:21` → `from dsa110_continuum.workflow import ...` (no
      guard); delete the no-op fallbacks in `mosaic/jobs.py:28`, `mosaic/jobs_wsclean.py:34`,
      `mosaic/science_jobs.py:21` and their try/excepts.
- [x] **Run, watch pass.**
- [x] **Retire the Dagster bridge exec path:** in `mosaic/science_jobs.py:157-212`, replace
      the lazy `from dsa110_contimg.workflow.dagster...` block with

  ```python
  raise RuntimeError(
      "Science-mosaic Dagster bridge retired with dsa110_contimg; "
      "run the visibility-domain coadd via scripts/batch_pipeline.py "
      "(see docs/skills/mosaicking.md)."
  )
  ```

      keeping `SciencePlanningJob` intact. Update `tests/test_mosaic_import_no_dagster.py`
      expectations if it asserts on the bridge; the poison-finder guard stays valid.
- [x] **Commit.**

**Dependencies:** Phase 2 (structured_logging may use utils exceptions/logging helpers).

**Verification:**
- [x] `pytest tests/test_workflow_registry.py tests/test_mosaic_import_no_dagster.py -q` green.
- [x] Checker: `workflow.*` = 0.

### Phase 5: Vendor the database layer subset → `dsa110_continuum/database/`

**Objective:** the 51 `infrastructure.*` imports across 29 files get real homes.

**Vendored set** (from old `infrastructure/database/`, consumed-only): `unified.py`
(`Database`, `get_pipeline_db_path`, `get_db`, `ensure_pipeline_db`), `session.py`
(`get_session`), `models.py` (only the consumed models: `QAPlot`, `MSIndex`,
`ImageComparison`, `SelfCalIteration`, `SpectralIndex`, …per inventory), `hdf5_index.py`
(`select_hdf5_groups_by_position` — also a test monkeypatch target,
`tests/test_calibrator_ms_generator.py:28`), registration/timing helpers
(`register_image_with_metadata`, `parse_subband_filename`, `query_subband_groups`),
`connection.py` (`get_db_connection` — `search/fts_setup.py:9`), `data_registry.py`
(symbols per `photometry/manager.py:30`), and provenance: **two candidate old modules
exist** (`infrastructure/database/provenance.py` — the `solver_common.py:298` import — and
`infrastructure/tracking/provenance.py`); at implementation time read both on H17, vendor
whichever defines each consumed symbol (`ProvenanceTracker`, `track_calibration_provenance`,
`record_ese_detection`) — both if consumption is split. Also vendor
`infrastructure/monitoring/pipeline_metrics.py` (`PipelineStage`, `record_stage_timing` —
`conversion/conversion_orchestrator.py:80`; note the module-local fallback `PipelineStage`
at `:100` gets deleted with the guard) → all under `dsa110_continuum/database/` (monitoring
piece as `dsa110_continuum/database/pipeline_metrics.py` or `utils/pipeline_metrics.py` —
keep with its sole consumer's import style).

**Tasks:**
- [ ] **Failing test first** — `tests/test_vendored_database.py` (new):

  ```python
  def test_pipeline_db_roundtrip(tmp_path, monkeypatch):
      monkeypatch.setenv("PIPELINE_DB", str(tmp_path / "pipeline.sqlite3"))
      from dsa110_continuum.database import ensure_pipeline_db, get_session
      ensure_pipeline_db()
      with get_session() as s:
          assert s is not None  # schema created, connection live

  def test_hdf5_index_selector_importable():
      from dsa110_continuum.database.hdf5_index import select_hdf5_groups_by_position
      assert callable(select_hdf5_groups_by_position)
  ```

      (exact env-var name per the vendored `unified.py` — read it during the port and pin
      the test to the real one).
- [ ] **Run, watch fail → vendor (provenance headers, internal import rewrite) → pass.**
- [ ] **Retarget the 29 files** (largest single-file cluster:
      `catalog/variable_source_detection.py`, 9 function-scope sites); delete guards;
      update `tests/test_calibrator_ms_generator.py:28`'s `LEGACY_SELECTOR` monkeypatch
      string to `"dsa110_continuum.database.hdf5_index.select_hdf5_groups_by_position"`.
- [ ] **Commit** in module batches.

**Dependencies:** Phases 2–3 (utils + settings paths).

**Verification:**
- [ ] `pytest tests/test_vendored_database.py tests/test_calibrator_ms_generator.py -q` green.
- [ ] Checker: `infrastructure.*` = 0.

### Phase 6: Flip the nine `__init__` re-export layers + interfaces + stragglers

**Objective:** `core.*` → 0; `interfaces.*` → 0; bare refs → 0.

**Tasks:**
- [ ] **Failing test first** — `tests/test_init_reexports_new_namespace.py` (new):

  ```python
  import importlib, pytest

  PACKAGES = [
      "dsa110_continuum.calibration", "dsa110_continuum.calibration.hardening",
      "dsa110_continuum.calibration.precompute", "dsa110_continuum.imaging",
      "dsa110_continuum.photometry", "dsa110_continuum.qa",
      "dsa110_continuum.simulation", "dsa110_continuum.validation",
      "dsa110_continuum.visualization",
  ]

  @pytest.mark.parametrize("pkg", PACKAGES)
  def test_package_exports_resolve_without_legacy(pkg):
      mod = importlib.import_module(pkg)
      missing = [n for n in getattr(mod, "__all__", []) if not hasattr(mod, n)]
      assert not missing, f"{pkg}: unresolved __all__ entries {missing}"
  ```

- [ ] **Run, watch fail** (today, on the Mac, many `__all__` names are absent because the
      old-path try-blocks silently passed).
- [ ] **Flip each `__init__.py`** to relative imports of the existing siblings —
      e.g. `calibration/__init__.py:145` block becomes `from .jobs import CalibrationSolveJob,
      CalibrationApplyJob, CalibrationValidateJob` and `from .pipeline import ...` (safe now:
      Phase 4 gave them their own registry); `photometry/__init__.py:4-48` →
      `from .forced import ...` etc.; `visualization/__init__.py:48-281` (two blocks, ~40
      symbols) → relative siblings; `simulation/__init__.py` old lines `:35-120` → relative;
      keep genuinely-optional guards only where the *new* module has heavy deps (matplotlib,
      pyuvdata) — mirror each package's existing soft-import style for those.
- [ ] **Interfaces sites:** `photometry/manager.py:25` + `photometry/worker.py:13` →
      replace legacy batch-API calls with

  ```python
  raise RuntimeError(
      "Legacy dsa110_contimg batch-photometry API retired; "
      "use scripts/batch_pipeline.py --photometry-workers N."
  )
  ```

      at the call sites (keep module importable); `qa/pipeline_hooks.py:40`
      (`QueryBuilder`) → vendor that one class into `dsa110_continuum/database/query_builder.py`
      if `pipeline_hooks` actually calls it (read at implementation; if unused at runtime,
      delete the import and the dependent branch); `validation/package_health.py:239` →
      probe `dsa110_continuum` CLI absence gracefully (drop the old-CLI probe).
- [ ] **Bare refs:** `validation/package_health.py:84,145-153` — probe list becomes new
      modules (`dsa110_continuum.config`, `.workflow.registry`, `.database.unified`,
      `.utils`, …); `visualization/report.py:62` → `from dsa110_continuum import __version__`
      (add `__version__ = "0.x.y"` to `dsa110_continuum/__init__.py` if absent, sourced from
      `importlib.metadata` with fallback).
- [ ] **Cosmetics with runtime effect:** 6 logger namespaces
      `getLogger("dsa110_contimg.conversion.helpers")` → `"dsa110_continuum.conversion..."`
      (`conversion/helpers*.py`, 6 files); `calibration/catalogs.py` 7 path probes and
      `simulation/make_synthetic_uvh5.py:51` — keep the old path probes as *fallback* disk
      locations (they point at real H17 catalog data dirs) but add the new-repo path first;
      `evidence/hdf5_calibrator_tile_smoke.py:380` provenance string check — accept both.
- [ ] **Run, watch pass; commit per package.**

**Dependencies:** Phases 2–5 complete.

**Verification:**
- [ ] `pytest tests/test_init_reexports_new_namespace.py -q` green on the Mac.
- [ ] Checker: `core.*` = 0, `interfaces.*` = 0, bare = 0 → **total = 0**.

### Phase 7: Delete the interop machinery

**Objective:** no `_compat.py`, no `_lazy_init.py` legacy branches, no shim dependence.

**Tasks:**
- [ ] **Failing test first** — `tests/test_no_compat_layer.py` (new):

  ```python
  from pathlib import Path

  def test_compat_modules_deleted():
      assert not Path("dsa110_continuum/_compat.py").exists()

  def test_no_source_references_compat():
      hits = [p for p in Path("dsa110_continuum").rglob("*.py")
              if "_compat" in p.read_text()]
      assert hits == []
  ```

- [ ] **Run, watch fail.**
- [ ] **Implement:** delete `dsa110_continuum/_compat.py`; in `_lazy_init.py`, keep
      `require_headless()` (`:73`, no legacy dep) and repoint `require_casa()`/
      `require_gpu_safety()` at the vendored `utils.casa_init`/`utils.gpu_safety`; every
      remaining `try: from dsa110_contimg... except ImportError: from dsa110_continuum._compat import ...`
      pattern was already collapsed in Phases 2–5 — this phase greps to prove it:
      `rtk grep -rn '_compat' dsa110_continuum/` → only `_lazy_init` history remains, then none.
- [ ] **Run, watch pass; commit.**

**Verification:**
- [ ] `pytest tests/ -q` full local suite green with zero shim (`python -c "import sys; assert not any('dsa110_contimg_shim' in str(p) for p in sys.path)"` guard in the test run env is optional but cheap).

### Phase 8: Packaging, docs, CI gate, H17 validation

**Objective:** packaging/dot-files stop lying; regression becomes impossible silently.

**Tasks:**
- [ ] **pyproject.toml:** `name = "dsa110-continuum"` (`:6`); `[project.scripts]` →
      `dsa110-health = "dsa110_continuum.validation.package_health:main"`,
      `dsa110-validate = "dsa110_continuum.validation.package_health:main"`, **drop**
      `dsa110 = "dsa110_contimg.interfaces.cli.main:cli"` (`:152`; see NOT-doing); delete
      `[tool.dg.project]` (`:273`); update ruff banned-API hints (`:255-256`) to ban
      `dsa110_contimg` imports outright:

  ```toml
  [tool.ruff.lint.flake8-tidy-imports.banned-api]
  "dsa110_contimg".msg = "dsa110_contimg is retired; import from dsa110_continuum"
  ```

- [ ] **CI gate:** in `.github/workflows/python-tests.yml`, add a step before tests:

  ```yaml
  - name: No legacy imports
    run: python scripts/check_import_migration.py --fail-on-any
  ```

- [ ] **CI test coverage for the new invariants:** the workflow runs a fixed 11-file list —
      append the cloud-safe new tests (`test_import_migration_checker.py`,
      `test_no_latent_nameerror_imports.py`, `test_vendored_utils.py`,
      `test_unified_config.py`, `test_workflow_registry.py`, `test_vendored_database.py`,
      `test_init_reexports_new_namespace.py`, `test_no_compat_layer.py`,
      `test_imaging_worker_no_fast_imaging.py`) to its pytest invocation. In
      `test_init_reexports_new_namespace.py`, guard the `visualization` (and any other
      heavy-dep) param with `pytest.importorskip("matplotlib")` so the CI dep set
      (numpy/scipy/pandas/astropy/uncertainties) stays sufficient.

- [ ] **Docs:** CLAUDE.md — remove the "re-export layers intentionally still reference old
      paths / do NOT change them" block and the cloud-shim instructions; replace with the
      new invariant ("dsa110_continuum is self-contained; the legacy dsa110_contimg
      namespace is banned — CI enforces"). Same sweep in `WORKSPACE_GUIDE.md:329-330,759-760,1056-1057`.
      Note in docs that the cloud VM `.pth` shim can be deleted from `~/.local/lib/python3.12/site-packages/`.
- [ ] **Checker hard mode:** flip `check_import_migration.py` default to `--fail-on-any`
      semantics (keep the flag for compat).
- [ ] **H17 validation (the co-load proof):** ship the branch to H17 (git bundle, temp
      worktree, as in the caskade validation) and run:

  ```bash
  # 1. targeted co-load: old bootstrap + new calibration in one process
  /opt/miniforge/envs/casa6/bin/python -c "
  import dsa110_contimg                      # old bootstrap registers old jobs
  import sys; sys.path.insert(0, '/tmp/contimg-migration-validate')
  import dsa110_continuum.calibration        # must NOT raise ValueError
  from dsa110_continuum.workflow import job_registry
  assert 'calibration_solve' in job_registry
  print('co-load OK')"
  # 2. full suite
  cd /tmp/contimg-migration-validate && PYTHONPATH=/tmp/contimg-migration-validate \
    /opt/miniforge/envs/casa6/bin/python -m pytest tests/ -q
  # 3. ops smoke
  PYTHONPATH=/tmp/contimg-migration-validate /opt/miniforge/envs/casa6/bin/python \
    scripts/batch_pipeline.py --date 2026-01-25 --start-hour 22 --end-hour 23 --dry-run
  ```

- [ ] **Commit; PR; validation report** via `ai-research-workflows:validating-implementations`.

**Verification:**
- [ ] All three H17 commands succeed (`co-load OK`; suite ≥ current 1168 passed; dry-run
      prints an execution plan and exits 0).
- [ ] CI green including the new gate.

## Success Criteria

### Automated Verification

- [ ] `python scripts/check_import_migration.py --fail-on-any` → exit 0 (count 0).
- [ ] `rtk grep -rn 'from dsa110_contimg\|import dsa110_contimg' dsa110_continuum/ --include='*.py'` → only docstring/comment hits (checker confirms 0 real).
- [ ] Mac/py312: `pytest tests/ -q` green with no shim and no old package.
- [ ] CI (`python-tests.yml`): green including the legacy-import gate step.
- [ ] H17 casa6: full suite green in PYTHONPATH mode; co-load script prints `co-load OK`.
- [ ] `pytest tests/test_workflow_registry.py tests/test_init_reexports_new_namespace.py tests/test_no_compat_layer.py -q` green.
- [ ] `scripts/batch_pipeline.py --dry-run` (H17) exits 0.
- [ ] `python -c "import dsa110_continuum; print(dsa110_continuum.__version__)"` works.

### Manual Verification

- [ ] QA dashboard (`scripts/qa_server.py`, live service) loads and renders after the branch
      lands on H17 — it has no legacy imports, but its templates route through the vendored
      `render_template`/`get_shared_css`; eyeball one report page.
- [ ] One real (non-dry-run) `batch_pipeline.py` hour on H17 produces a mosaic + manifest
      with verdict as before (compare against the validated 2026-01-25 hour-22 reference run).
- [ ] Old services on :8000/:3000 still healthy after the new branch is pulled on H17
      (they should be untouched — confirm, don't assume).

### Reproducibility & Correctness (research code)

- [ ] Vendored numeric helpers (`time_utils`, `coordinates`) are pinned by oracle tests
      (MJD epoch, hms/dms analytic values) — Phase 2.
- [ ] The one real (non-dry-run) H17 hour reproduces the reference mosaic within existing QA
      tolerances (QA gate itself is the tolerance authority).

## Testing Strategy

Unit tests are written test-first inside each phase (see above). Beyond those:

**Integration Tests:**
- [ ] `tests/test_batch_e2_hygiene.py`'s `_Block_dsa110_contimg` meta-path blocker suite —
      after migration this should pass trivially; keep it as the regression guard.
- [ ] `tests/test_mosaic_import_no_dagster.py` — poison-finder guard stays; assert bridge
      error message.
- [ ] H17 co-load test (Phase 8) — the only test that requires the old package present.

**Manual Testing:** the three items under Manual Verification.

**Test Data Requirements:** none new; H17 validation reuses the indexed 2026-01-25 date.

## Migration Strategy

**Migration steps** = Phases 0–8; each phase is a PR-sized, independently-green unit;
checker count is the ratchet (record before/after in each PR body).

**Rollback plan:** every phase is a revertable commit set; nothing mutates shared state
until Phase 8's H17 pull. The cloud shim and old package remain installed until the end, so
reverting any phase restores the status quo. H17 production checkout stays on `main` until
the PR merges.

**Backward compatibility:** none owed — the old namespace is declared obsolete. The old H17
deployment keeps its own private copies of everything (one-way dependency, verified).

## Risk Assessment

1. **Risk:** vendored modules behave differently off-H17 (GPU/CASA absent).
   - Likelihood: Medium / Impact: Medium.
   - Mitigation: every phase's tests run on the Mac first (no CASA, no GPU); vendored
     modules' internal availability guards are exercised by exactly that environment.
2. **Risk:** vendored-set closure misses a transitive old-package import.
   - Likelihood: Medium / Impact: Low (import error, caught instantly).
   - Mitigation: each vendored module is imported in its phase's test on the Mac; checker
     counts intra-vendored stragglers too.
3. **Risk:** hidden dynamic/string references to the old namespace survive the checker.
   - Likelihood: Low / Impact: Medium.
   - Mitigation: inventory already enumerated them (`fov.py` resources, `package_health`
     probe list, monkeypatch strings, logger names) — each has an explicit Phase 6 task;
     final `grep` sweep in Success Criteria.
4. **Risk:** `unified_config` vendoring drags in old-package-only deps or H17-only paths.
   - Likelihood: Medium / Impact: Medium.
   - Mitigation: Phase 3's Mac-first test pins env-free import; path defaults route through
     `get_env_path` conventions.
5. **Risk:** distribution rename (`dsa110_contimg` → `dsa110-continuum`) breaks an install
   somewhere.
   - Likelihood: Low / Impact: Low — editable installs were already broken (CLAUDE.md);
     casa6 never had it installed.
   - Mitigation: Phase 8 note in PR; H17 stays PYTHONPATH-mode.
6. **Risk:** the retired Dagster bridge / batch-photometry API is quietly load-bearing for
   someone.
   - Likelihood: Low / Impact: Medium.
   - Mitigation: both were reachable only with the old package importable, which the new
     package no longer guarantees anyway; errors name the replacement; grep of scripts/ and
     services showed no callers.

## Edge Cases and Error Handling

1. **Case:** old and new package co-imported (H17 PYTHONPATH runs).
   - Expected: both job registries populate independently; no `ValueError`.
   - Implementation: Phase 4 vendored registry; Phase 8 co-load test.
2. **Case:** module imported on a box with neither package installed properly (bare clone).
   - Expected: plain `ModuleNotFoundError: dsa110_continuum...` — never `NameError`.
   - Implementation: Phases 1–6 eliminate all 20 Category-A and 42 Category-B sites; the
     `test_no_latent_nameerror_imports.py` param list grows to cover every touched module.
3. **Case:** `render_template`/`get_shared_css` asset lookup after vendoring.
   - Expected: templates resolve via `importlib.resources` inside `dsa110_continuum`.
   - Implementation: Phase 2 vendors `common/templates/` as package data; add
     `[tool.setuptools.package-data]` entry in Phase 8.
4. **Error:** legacy batch-photometry API called.
   - Handling: `RuntimeError` naming `--photometry-workers` replacement (Phase 6).
5. **Error:** Science-mosaic Dagster bridge executed.
   - Handling: `RuntimeError` naming `scripts/batch_pipeline.py` (Phase 4).

## Documentation Updates

- [ ] CLAUDE.md: delete the re-export-protection block + cloud-shim fallback section;
      add the self-containment invariant and CI gate note.
- [ ] `WORKSPACE_GUIDE.md`: remove shim instructions (3 locations).
- [ ] `docs/agents/domain.md` / `docs/skills/*`: mentions of old-namespace loading, if any
      (grep sweep in Phase 8).
- [ ] Scrub stale docstring examples still showing `from dsa110_contimg...` (AST-invisible
      but misleading): `calibration/__init__.py:8`, `imaging/__init__.py:8`,
      `conversion/__init__.py:9,18,29`, `rfi/__init__.py:16`, `evaluation/__init__.py:18`,
      plus the `>>>` examples in `simulation/ground_truth.py:44`, `time_domain.py:40,170`,
      `simulate_tile_fits.py:18,376`, `qa/__init__.py:18`, `simulation/__init__.py:27`.
- [ ] Vendored modules carry provenance headers (source path + date).

## Timeline Estimate

- Phase 0–1: ~half a day (tooling + mechanical wins).
- Phase 2: the bulk — 2–3 days (20+ vendored modules, ~100 consumer files, batched).
- Phases 3–5: ~1 day each.
- Phases 6–7: ~1 day combined.
- Phase 8: ~half a day + H17 validation window.

**Note:** estimates assume the inventory's symbol lists are exhaustive (AST-derived; high
confidence) and no vendored module hides a deep old-package dependency web.

## Open Questions

*(none — all decisions resolved above; the two implementation-time reads flagged inline
(`QueryBuilder` usage in `pipeline_hooks`, exact env-var name in vendored `unified.py`)
have both branches specified.)*

---

## References

**Research inputs (this session, 2026-07-03):**
- AST inventory of all 349 stale imports (subagent report; raw dumps in session scratchpad
  `records.json` / `usage.json`).
- Legacy-interop mechanics report (subagent report: re-export layers, `_compat.py`,
  `_lazy_init.py`, pyproject, Dagster bridge, shim, CI).
- H17 probes: old `workflow/pipeline/registry.py:78-84` (registry keying);
  reverse-dependency count (0); old-source dir sizes; `fast_imaging` nonexistence;
  running old services.

**Files analyzed:** `dsa110_continuum/{_compat,_lazy_init}.py`,
`dsa110_continuum/calibration/{__init__,jobs,pipeline}.py`,
`dsa110_continuum/mosaic/{jobs,jobs_wsclean,science_jobs}.py`,
`dsa110_continuum/imaging/{worker,fov}.py`, `dsa110_continuum/validation/package_health.py`,
`scripts/check_import_migration.py`, `pyproject.toml`, `.github/workflows/python-tests.yml`,
`tests/test_mosaic_import_no_dagster.py`, `tests/test_calibrator_ms_generator.py`,
old-package sources on H17 under `/data/dsa110-contimg/backend/src/dsa110_contimg/`.

**Related specs:**
- [handoff-2026-07-03-03-16-caskade-simulation-control.md](handoff-2026-07-03-03-16-caskade-simulation-control.md)
- [validation-caskade-simulation-control.md](validation-caskade-simulation-control.md)

---

## Review History

### Version 1.0 — 2026-07-03
- Initial plan created from two-agent research sweep + H17 live probes.

### Version 1.1 — 2026-07-03
- Adversarial review (independent subagent; verdict SOUND-WITH-FIXES) folded in:
  added missed consumers to vendor sets (`utils.paths::resolve_paths/get_repo_root`,
  `antenna_classification`, `env_utils`, `monitoring.pipeline_metrics::PipelineStage/
  record_stage_timing`, `database.connection::get_db_connection`, `data_registry`,
  dual-provenance-module resolution); corrected distinct-file count 149→126; made the
  `worker.py` fast-imaging removal surgical (preserve deep/GPU legs + tuple unpack at
  `:458`); fixed the Phase 0 docstring test to actually discriminate AST vs line-prefix;
  enumerated both `get_env_path` source paths + Path-vs-str caution + shared-try-block
  split at `qa/pipeline_hooks.py:39`; added new tests + importorskip guard to the CI
  workflow; added docstring-example scrub list; cited old-registry API lines proving the
  Phase 4 test surface.
