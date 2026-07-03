# Validation Report: dsa110_contimg Import Retirement

> Validated against `plan-contimg-import-retirement.md` /
> `implement-contimg-import-retirement.md` at commit `aaa721c`
> (branch `agent/contimg-import-retirement`, PR
> [dsa110/dsa110-continuum#93](https://github.com/dsa110/dsa110-continuum/pull/93))
> on 2026-07-03. All command output below was produced fresh by the validator
> in this session; H17 results were produced at `cdd0eaa`, whose diff to
> `aaa721c` is docs-only (`git diff --stat cdd0eaa..HEAD` → 2 files, both under
> `docs/rse/specs/`), with the co-load proof re-run fresh at validation time.

**Verdict: ✅ PASS** — every automated success criterion met with fresh
evidence; zero regressions (the 8 Mac-baseline failures reproduce identically
on `main` @ `82e3d96`); manual verification items remain for the human
(operational sign-offs, merge decision).

## 1. Implementation Status

| Phase | Claim | Verified |
| --- | --- | --- |
| 0 — AST checker + gate | `- [x]` | ✅ checker runs AST-based, exits 1 on any hit (pinned by `tests/test_import_migration_checker.py`, ran green in all three suites) |
| 1 — retargets + dead code | `- [x]` | ✅ covered by checker=0 + `test_no_latent_nameerror_imports.py` + `test_imaging_worker_no_fast_imaging.py` (green) |
| 2 — vendor utils | `- [x]` | ✅ `test_vendored_utils.py` green (Mac, CI-replica, H17, GitHub CI) |
| 3 — vendor unified_config | `- [x]` | ✅ `test_unified_config.py` green; `photometry/forced.py` hard-imports settings (read) |
| 4 — vendor workflow | `- [x]` | ✅ `test_workflow_registry.py` green; distinct-registry co-load proven live on H17 |
| 5 — vendor database | `- [x]` | ✅ `test_vendored_database.py` green |
| 6 — flip re-export layers + interfaces | `- [x]` | ✅ `test_init_reexports_new_namespace.py` 9/9; retirement RuntimeErrors read in `photometry/manager.py` / `worker.py`; checker total = 0 |
| 7 — delete interop | `- [x]` | ✅ `_compat.py` absent; `test_no_compat_layer.py` 3/3; `_lazy_init.py` has no guards (read) |
| 8 — packaging/CI/docs/H17 | `- [x]` | ✅ pyproject read (name, scripts, banned-api, no `[tool.dg]`); CI job steps confirmed via GitHub API; docs read; H17 commands below |

All 9 phases: checkmarks match reality. One plan box intentionally reworded
in-place (checker hard mode was already default since Phase 0).

## 2. Automated Verification Results (fresh runs at `aaa721c`)

- ✅ `scripts/check_import_migration.py --fail-on-any` — `[OK] No stale
  'dsa110_contimg' imports found.` `CHECKER_EXIT=0`.
- ✅ Legacy-string grep under `dsa110_continuum/` — 12 textual hits, all
  docstrings/comments/disk-path probes (AST checker confirms 0 real imports;
  each hit classified during Phase 8's docstring scrub).
- ✅ Mac py312 full suite, no shim, no old package —
  `8 failed, 1070 passed, 9 skipped` in 37.7 s.
- ✅ **Regression oracle:** the same 3 test files run on `main` @ `82e3d96`
  (temp worktree) → `8 failed, 13 passed` — the identical 8 node IDs
  (`test_detect_bad_polarizations` ×3, `test_skymodels_vlass` ×2,
  `test_two_stage_photometry` ×3). The baseline pre-dates the branch; the
  branch introduces **zero regressions**.
- ✅ `pytest tests/test_workflow_registry.py
  tests/test_init_reexports_new_namespace.py tests/test_no_compat_layer.py -q`
  — `20 passed in 2.93s`.
- ✅ `python -c "import dsa110_continuum; print(dsa110_continuum.__version__)"`
  — `0.0.0+unknown` (documented source-checkout fallback; real version once
  the renamed dist is installed).
- ✅ CI (`python-tests.yml`) at head `aaa721cc46bf` — GitHub API job
  85076918963: `No legacy imports: success`, `Run focused cloud-safe subset:
  success` (43 s; run 28685356344). Same result on the prior head's run
  28685268908.
- ✅ H17 co-load — **re-run fresh at validation time**: `co-load OK
  (revalidation)` (old `dsa110_contimg` bootstrap + new
  `dsa110_continuum.calibration` + `calibration_solve` in the vendored
  registry, one process).
- ✅ H17 casa6 full suite (`/tmp/contimg-migration-validate` @ `cdd0eaa`,
  `PYTHONPATH` mode) — 1228 collected, **no `.pytest_cache/v/cache/lastfailed`
  file ⇒ 0 failures** (plan bar: ≥ 1168 passed). Code content identical to
  `aaa721c` (docs-only diff).
- ✅ H17 `scripts/batch_pipeline.py --date 2026-01-25 --start-hour 22
  --end-hour 23 --dry-run --quarantine-after-failures 3` — `EXIT=0`, full
  execution plan (11 tiles, BP/G tables `[exists]`, Dec 16.128°, resume plan,
  strict QA gating), `Pipeline NOT executed (--dry-run set)`.
- ✅ Local CI-replica venv (exact workflow dep set + 20-file list + gate) —
  `193 passed`.
- ✅ Order-permutation import sweep (12 packages × import-first) — all `CLEAN`.
- ✅ Adversarial ruff banned-api probe — a file containing
  `import dsa110_contimg` produces
  `TID251 dsa110_contimg is banned: dsa110_contimg is retired; import from
  dsa110_continuum`.

## 3. Code Review Findings

**Matches plan:** vendor-then-flip executed as specified; provenance headers on
vendored files; retirement errors use the plan's exact RuntimeError text;
`[tool.ruff.lint.flake8-tidy-imports.banned-api]` matches the plan's TOML
verbatim; CI gate step matches the plan's YAML.

**Deviations (all documented in the implement doc, none regressive):**

1. Disk-layout probes in `calibration/catalogs.py` / `make_synthetic_uvh5.py`
   left as legacy-layout fallbacks (no `state/` tree exists in the new repo).
2. Evidence smoke-script provenance check left intact — plan misread its
   polarity (it already accepts the new namespace and deliberately flags
   legacy ownership).
3. `test_no_compat_layer.py` uses `\b_compat\b` word-boundary regex (plan's
   substring sketch would false-positive on `strip_compatibility`).
4. CI dep set extended instead of `importorskip`-weakening the re-exports test
   — strictly stronger CI coverage; verified in a replica venv before push.
5. Phase 6 landed as one commit instead of per-package (shared regression
   test).

**Issue found and fixed during implementation (validator confirmed the fix):**
the Phase 6 flip created a qa ↔ calibration circular import that silently
dropped `compare_caltables` under qa-first import order (guard-swallowed
ImportError). Fixed by function-scope import in `calibration/qa_compare.py`;
the permutation sweep (fresh, 12×CLEAN) is the regression check; hazard
documented in CLAUDE.md.

**Residual observations (not blockers):**

- `pyproject.toml` still lists Dagster/Django/redis-era runtime dependencies
  and `[project.urls]` still points at `dsa110/dsa110-contimg` — candidates
  for a separate dependency-diet pass (recorded as follow-up in the implement
  doc).
- `photometry/worker.py`'s poll loop now raises the retirement RuntimeError
  per dequeued job (uncaught → worker thread dies loudly on first legacy job).
  Intentional loud-retirement semantics; noted for the operational sign-off
  below.

## 4. Manual Testing Required

- [ ] Confirm no live H17 service submits batch photometry via
  `PhotometryManager.submit_batch` / `PhotometryBatchWorker` (both now raise;
  `scripts/qa_server.py` and `scripts/monitor_server.py` untouched).
- [ ] Confirm `dsa110 index add …` on H17 still works via the old install
  (this repo no longer ships the `dsa110` entry point).
- [ ] Approve deleting the obsolete `.pth` shim from cloud-VM images
  (`~/.local/lib/python3.12/site-packages/dsa110_contimg_shim.py` + loader).
- [ ] Check no deploy tooling pins the old dist name `dsa110_contimg`.
- [ ] Merge decision on PR #93; after merge, remove the H17 review worktree:
  `cd /data/dsa110-continuum && git worktree remove /tmp/contimg-migration-validate`.

## 5. Recommendations

**Critical:** none — all automated criteria pass.

**Important:**
- Complete the operational sign-off (manual items 1–2) before merging: the
  retirement RuntimeErrors convert any surviving legacy batch-photometry
  caller from silent-legacy behavior to a loud failure.

**Nice to Have:**
- Dependency-diet pass on `pyproject.toml` (drop Dagster/Django-era deps;
  update `[project.urls]` to the canonical repo).
- Consider a CI job that runs the order-permutation import sweep to guard
  against future guard-swallowed circular imports.

**Follow-Up:**
- Delete the cloud-VM `.pth` shim (manual item 3).
- After merge, retire the H17 temp worktree and the fetched branch ref in
  `/data/dsa110-continuum` per normal lane hygiene.

## References

- Plan: [plan-contimg-import-retirement.md](plan-contimg-import-retirement.md)
- Implementation: [implement-contimg-import-retirement.md](implement-contimg-import-retirement.md)
- PR: https://github.com/dsa110/dsa110-continuum/pull/93
