# Implementation Summary: dsa110_contimg Import Retirement

---
**Date:** 2026-07-03
**Author:** AI Assistant (Claude Code)
**Status:** Complete (pending manual verification)
**Plan Reference:** [plan-contimg-import-retirement.md](plan-contimg-import-retirement.md)
**Branch:** `agent/contimg-import-retirement` (based on `main` @ `82e3d96`)

---

## Overview

Fully retired the deprecated `dsa110_contimg` namespace from `dsa110_continuum`.
The package is now **self-contained**: zero legacy imports (AST-verified), the
nine `__init__` re-export layers import their own siblings, the cloud compat
shim and `_compat.py` stub layer are deleted, the distribution is renamed
`dsa110-continuum`, and CI gates any regression. The vendor-then-flip strategy
(Phases 1–5 make the new namespace satisfy every consumed symbol; Phases 6–7
flip and delete interop; Phase 8 hardens packaging/CI/docs) landed without a
single baseline test regression, and the co-load proof on H17 confirms the old
and new packages coexist in one process with no double job-registration.

**Implementation Duration:** 2026-07-03 (single session, phases 0–8 consecutive)

**Final Status:** ✅ Complete — all automated verification passing; manual
verification steps listed below.

## Plan Adherence

**Plan Followed:** [plan-contimg-import-retirement.md](plan-contimg-import-retirement.md)

**Deviations from Plan:**

- **Deviation 1 (Phase 6):** `calibration/catalogs.py` (7 sites) and
  `simulation/make_synthetic_uvh5.py:41` disk-layout probes for the old
  `src/dsa110_contimg` tree were left as-is instead of adding a new-repo path
  first.
  - **Reason:** no `state/` tree exists in the new repo anywhere (verified on
    Mac and H17); the `CONTIMG_BASE_DIR` env candidate already targets the live
    H17 catalog data. A new-repo probe would be dead code.
  - **Impact:** none at runtime; probes are checker-invisible string paths.
- **Deviation 2 (Phase 6, no-op):** the plan asked the evidence smoke-script
  provenance check (`evidence/hdf5_calibrator_tile_smoke.py:380`) to "accept
  both" namespaces. The check already accepts the new namespace (default owner
  `dsa110_continuum.config.PathConfig`) and *deliberately rejects* legacy
  ownership — the plan misread its polarity. Left intact.
- **Deviation 3 (Phase 7):** `tests/test_no_compat_layer.py` uses a
  word-boundary regex (`\b_compat\b`) instead of the plan's plain substring
  scan.
  - **Reason:** identifiers like `_validate_strip_compatibility` contain
    `_compat` as a substring; the plan's sketch would false-positive forever.
  - **Impact:** the test is strictly more precise.
- **Deviation 4 (Phase 8):** instead of weakening
  `test_init_reexports_new_namespace.py` with `pytest.importorskip`, the CI
  dependency set was extended (pyyaml, pydantic, pydantic-settings, caskade,
  sqlalchemy, fastapi, matplotlib, httpx, h5py + `CASKADE_BACKEND=numpy`).
  - **Reason:** the exact set was verified green (193 passed) in a local
    CI-replica venv; full-strength re-export verification in CI is worth the
    ~1 min of extra pip install. CI job timeout raised 5 → 10 minutes.
  - **Impact:** CI now exercises the visualization/database/simulation
    re-export layers it previously skipped. This replica run also surfaced a
    real circular-import bug (Issue 2 below) that importorskip would have
    hidden.
- **Deviation 5 (Phase 8, scope extension):** the commit-per-package cadence of
  Phase 6 was collapsed into one commit (`871c4b1`) because the nine flips
  share one regression test; Phase 7 additionally swept seven stub-fallback
  guards targeting vendored modules and deleted `tests/test_compat.py` (the
  shim's signature-pin suite, not in the CI file list).

## Phases Completed

All phases completed 2026-07-03, each gated on its plan verification before
advancing. Checker ratchet (stale imports / files):
**349/126 → 328/121 (P1) → 165/60 (P2) → 148/48 (P3) → 134/41 (P4) → 83/14 (P5) → 0/0 (P6)**.

### Phase 0: AST checker + `--fail-on-any` ratchet — ✅ (`76156fd`)
`scripts/check_import_migration.py` rewritten AST-based; true baseline 349
imports / 126 files. Exit-1-on-any-hit is the default from day one.

### Phase 1: Retargets to existing ports + dead code — ✅ (`9122e27`)
16-file `get_env_path` cluster and other symbols with existing new-namespace
ports; removed the phantom `fast_imaging` import (module absent even from the
old package — proven by ssh grep on H17).

### Phase 2: Vendor `common.utils` subset → `utils/` — ✅ (`43b5913`, `f4733f7`)
Vendored from the H17 source of truth with provenance headers; `_compat`
consumers flipped to direct vendored imports across 11 files.

### Phase 3: Vendor `unified_config` (settings singleton) — ✅ (`2286444`, `3c22bea`)
1.5k-line settings layer with no prior port; consumers retargeted; B4 hygiene
test updated for real settings.

### Phase 4: Vendor workflow job framework → `workflow/` — ✅ (`05c22e2`)
`base/registry/executor/structured_logging/metrics`; the vendored `JobRegistry`
is a distinct object from the old package's — this dissolves the
double-registration hazard structurally. `ScienceMosaicBridgeJob.execute()`
retired with a RuntimeError naming `scripts/batch_pipeline.py`.

### Phase 5: Vendor database subset → `database/` — ✅ (`27964cc`)
`unified/schema_guard/schema.sql/session/models/hdf5_index/…`; QueryBuilder
inlined as parameterized SQL in `hdf5_index`; Mac baseline improved 16 → 8
pre-existing failures via the LEGACY_SELECTOR retarget.

### Phase 6: Flip the nine `__init__` re-export layers + interfaces — ✅ (`871c4b1`)
All nine layers now import `dsa110_continuum.*` siblings
(`tests/test_init_reexports_new_namespace.py`, 9/9). Legacy batch-photometry
API retired with RuntimeError in `photometry/manager.py::submit_batch` and
`photometry/worker.py` run loop; `qa/pipeline_hooks.py` QueryBuilder inlined;
`package_health.py` probes rewritten to new modules; `__version__` added to
`dsa110_continuum/__init__.py` via `importlib.metadata`; 7 logger namespaces
renamed. Checker total: **0**.

### Phase 7: Delete the interop machinery — ✅ (`64fb4ee`)
`_compat.py` (266 lines) deleted; `_lazy_init.py` guards unwrapped to hard
vendored imports; seven remaining stub-fallback guards unwrapped
(mosaic/{builder,orchestrator,qa}, conversion/{helpers_telescope,
file_validator,conversion_orchestrator}, photometry/forced);
`tests/test_no_compat_layer.py` added. Full suite green vs baseline with the
no-shim guard.

### Phase 8: Packaging, docs, CI gate, H17 validation — ✅ (`cdd0eaa`)
Dist renamed `dsa110-continuum`; legacy `dsa110` CLI entry dropped (H17's
working binary comes from the old install, untouched); ruff `banned-api` bans
`dsa110_contimg`; CI "No legacy imports" gate + 9 invariant test files + dep
extension; CLAUDE.md / WORKSPACE_GUIDE.md scrubbed; instructional docstrings
repointed; H17 co-load + full suite + dry-run (results below).

## Files Modified

**Created:** `dsa110_continuum/workflow/` (6 modules),
`dsa110_continuum/database/` (12 modules + `schema.sql`),
`dsa110_continuum/utils/` additions (incl. `naming.py`),
`dsa110_continuum/calibration/solve_orchestration.py`,
`dsa110_continuum/search/fts_setup.py` local `get_db_connection`, and tests:
`test_import_migration_checker.py`, `test_no_latent_nameerror_imports.py`,
`test_vendored_utils.py`, `test_unified_config.py`, `test_workflow_registry.py`,
`test_vendored_database.py`, `test_init_reexports_new_namespace.py`,
`test_no_compat_layer.py`, `test_imaging_worker_no_fast_imaging.py`.

**Modified:** ~90 files across `dsa110_continuum/` (import retargets, guard
unwraps, the nine `__init__` flips), `pyproject.toml`,
`.github/workflows/python-tests.yml`, `CLAUDE.md`, `WORKSPACE_GUIDE.md`,
`scripts/check_import_migration.py`.

**Deleted:** `dsa110_continuum/_compat.py` (stub layer obsolete),
`tests/test_compat.py` (pinned the deleted shim's signatures).

## Key Changes Summary

1. **Registry-collision dissolution** — `dsa110_continuum/workflow/registry.py`
   keys jobs in its own instance dict; co-loading old + new packages cannot
   double-register. Proven live on H17 (co-load OK).
2. **Namespace ban with three enforcement layers** — AST checker CI gate
   (`scripts/check_import_migration.py`), ruff `banned-api`
   (`pyproject.toml`), and the invariant test files in CI.
3. **Loud retirement over silent fallback** — legacy batch-photometry API and
   Dagster science bridge raise RuntimeError naming their replacements
   (`photometry/manager.py:645`, `photometry/worker.py:155`,
   `mosaic/science_jobs.py`).
4. **Circular-import fix** — `calibration/qa_compare.py` defers its
   `qa.calibration_quality` import to function scope; the qa-first import
   order silently dropped `compare_caltables` from
   `dsa110_continuum.calibration` (guard-swallowed). Order-permutation sweep
   of all 12 packages now clean; hazard documented in CLAUDE.md.

## Verification Results

### Automated Verification

- ✅ `scripts/check_import_migration.py --fail-on-any` — exit 0, **0 stale imports** (from 349).
- ✅ `pytest tests/test_init_reexports_new_namespace.py -q` — 9/9 (Mac).
- ✅ `pytest tests/test_no_compat_layer.py -q` — 3/3 (failing-first verified).
- ✅ Mac py312 full suite, no shim, no old package —
  **8 failed = pre-existing baseline** (detect_bad_polarizations ×3,
  skymodels_vlass ×2, two_stage_photometry ×3), 1070 passed, 9 skipped.
  No-shim guard asserted in the run env.
- ✅ CI-replica venv (exact new CI dep set + test list) — **193 passed, 0 failed**.
- ✅ Order-permutation import sweep (12 packages × import-first) — all CLEAN.
- ✅ H17 co-load proof — `co-load OK` (old bootstrap + new calibration +
  `calibration_solve` in the vendored registry; caskade 1.1.1 present in casa6).
- ✅ H17 `scripts/batch_pipeline.py --date 2026-01-25 --start-hour 22
  --end-hour 23 --dry-run --quarantine-after-failures 3` — exit 0, full
  execution plan printed, BP/G cal tables resolved `[exists]`, Dec strip
  16.13°, 11 tiles planned.
- ✅ H17 casa6 full suite (`PYTHONPATH=/tmp/contimg-migration-validate`) —
  **1228 passed, 0 failed** (no pytest `lastfailed` cache; plan bar was ≥1168).
- ⏳ CI green including the gate — requires push; part of the PR step.

### Manual Verification (human required)

- [ ] Review the retirement RuntimeErrors' operational impact: any cron/service
  on H17 that still submits photometry batch jobs through
  `PhotometryManager.submit_batch` / `PhotometryBatchWorker` will now fail
  loudly instead of using the legacy API. Confirm no live service depends on it
  (`scripts/qa_server.py` and `scripts/monitor_server.py` were not modified).
- [ ] Confirm the `dsa110` CLI on H17 (from the old install) still works for
  `dsa110 index add …` operations, per the NOT-doing scope.
- [ ] Approve deleting the cloud-VM `.pth` shim from
  `~/.local/lib/python3.12/site-packages/` on any cloud VM images in use.
- [ ] Review the pyproject dist rename (`dsa110-continuum`) for any deploy
  tooling that pins the old dist name.
- [ ] Merge decision: PR from `agent/contimg-import-retirement` → `main`.

## Issues Encountered

### Issue 1: Stale test asserting the legacy blocker (Phase 3)
- **Impact:** `test_forced_convolve_works_without_dsa110_contimg` asserted
  `settings is None`; vendoring made settings real.
- **Resolution:** test updated to assert the vendored behavior (separate
  commit `3c22bea` with explanation).

### Issue 2: qa ↔ calibration circular import (Phase 8, latent since Phase 6)
- **Impact:** importing `dsa110_continuum.qa` before
  `dsa110_continuum.calibration` silently dropped `compare_caltables` — the
  package guard swallowed the ImportError; symptom was order-dependent and
  invisible to the solo test run.
- **Resolution:** function-scope import in `calibration/qa_compare.py`;
  hazard documented in CLAUDE.md; permutation sweep added to verification.
- **Files Affected:** `dsa110_continuum/calibration/qa_compare.py`.

### Issue 3: CI dep set insufficient for the new hard imports (Phase 8)
- **Impact:** Phase 7's guard unwraps made `unified_config`
  (pydantic/pydantic-settings/yaml) and `simulation.control` (caskade) hard
  imports; the existing CI list (`test_forced_photometry_parallel.py`) would
  have broken on push.
- **Resolution:** CI dep set extended and the exact set verified in a local
  replica venv before committing (Deviation 4).

### Issue 4: Conversion-orchestrator stub never worked (Phase 5)
- **Impact:** the stub `parse_subband_filename` returned an attribute-object
  while call sites tuple-index — the fallback path was dead on arrival.
- **Resolution:** stub deleted with its guard; real function verified
  signature-compatible.

## Testing Summary

**Tests Added:** nine invariant test files (listed under Files Modified), all
appended to the CI workflow. **Tests Deleted:** `tests/test_compat.py` (11
pins of the deleted shim). **All Tests Passing:** ✅ vs the documented 8-failure
pre-existing Mac baseline; 193/193 in the CI replica.

## Performance Observations

Not a concern for this migration; suite wall-times unchanged (~44 s Mac
py312).

## Documentation Updated

- ✅ `CLAUDE.md` — re-export-layer protection block replaced with the
  self-contained invariant + CI enforcement; cloud-shim section replaced with
  deletion note; mosaic/Dagster block updated to the retired bridge;
  circular-import hazard documented.
- ✅ `WORKSPACE_GUIDE.md` — legacy-migration/shim Known Issues entries updated
  (both copies) and the compat-layer "area for attention" removed.
- ✅ Instructional docstrings repointed (conversion/rfi/evaluation/calibration
  `__init__`s, two `python -m` CLI examples, `catalog/grouping.py` deprecation
  target). Provenance headers and real `/data/dsa110-contimg` disk paths
  intentionally retained.

## Remaining Work

- [ ] Push branch; open PR; confirm CI green including the "No legacy imports"
  gate (one-way door — awaiting go-ahead).
- [ ] Follow-up (out of scope, noted): `pyproject.toml` still carries Dagster
  and other legacy-era runtime dependencies; `[project.urls]` still points at
  `dsa110/dsa110-contimg`. Candidates for a separate dependency-diet pass.
- [ ] Follow-up: delete the `.pth` shim from cloud VM images (manual step
  above).

## Next Steps

1. Human manual verification (checklist above).
2. `ai-research-workflows:validating-implementations` against the plan.
3. PR review and merge; delete the H17 temp worktree
   (`git worktree remove /tmp/contimg-migration-validate` in
   `/data/dsa110-continuum`) after merge.

## Lessons Learned

**What Went Well:** the vendor-then-flip ordering meant every phase was
individually green — no big-bang breakage; the checker ratchet made progress
measurable and regression impossible to miss.

**Technical Insights:**
- Guarded re-export layers convert circular imports into *silently missing
  attributes* — the failure is order-dependent and only surfaces under a
  different import sequence (here: pytest collection order in a replica venv).
  Permutation import sweeps are cheap insurance.
- `except ImportError: pass` guards with stub fallbacks rot invisibly: two
  stubs found here (conversion orchestrator's `parse_subband_filename`,
  validate.py's wrong-name imports) had never worked.

## References

**Plan Document:** [plan-contimg-import-retirement.md](plan-contimg-import-retirement.md)

**Commits** (branch `agent/contimg-import-retirement`):
- `76156fd` — Phase 0: AST checker + gate
- `9122e27` — Phase 1: retargets
- `43b5913` / `f4733f7` — Phase 2: vendor utils / guard swap
- `2286444` / `3c22bea` — Phase 3: unified_config
- `05c22e2` — Phase 4: workflow framework
- `27964cc` — Phase 5: database subset
- `871c4b1` / `1fbbd54` — Phase 6: re-export flip + plan ticks
- `64fb4ee` / `487e342` — Phase 7: interop deletion + plan ticks
- `cdd0eaa` — Phase 8: packaging/CI/docs/circular-import fix

---

**Implementation completed by AI Assistant on 2026-07-03**
