# Validation Report: dsa110_contimg Import Retirement

> Validated against `plan-contimg-import-retirement.md` /
> `implement-contimg-import-retirement.md` at commit `aaa721c`
> (branch `agent/contimg-import-retirement`, PR
> [dsa110/dsa110-continuum#93](https://github.com/dsa110/dsa110-continuum/pull/93))
> on 2026-07-03. All command output below was produced fresh by the validator
> in this session. **Correction (post-merge, §6):** the H17 review-worktree
> runs cited in §2 exercised `487e342` (end-of-Phase-7) content, not
> `cdd0eaa` as originally stated — the second bundle fetch advanced the
> branch ref without refreshing the checkout. The gap is closed by a fresh
> full-suite run at the merge commit `baef485` on H17 main (§6).

**Verdict: ✅ PASS** — every automated success criterion met with fresh
evidence; zero regressions (the 8 Mac-baseline failures reproduce identically
on `main` @ `82e3d96`). Manual items 1, 2, and 4 were subsequently executed
live on H17 by the validator at the user's request (2026-07-03; evidence in
§4a) — all three pass. Remaining for the human: `.pth` shim approval and the
merge decision on PR #93.

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
- ✅ H17 casa6 full suite (review worktree, `PYTHONPATH` mode) — 1228
  collected, **no `.pytest_cache/v/cache/lastfailed` file ⇒ 0 failures**
  (plan bar: ≥ 1168 passed). ⚠️ Corrected: this run exercised `487e342`
  content, not `cdd0eaa` (see §6) — superseded by the post-merge run at
  `baef485` in §6, which is the authoritative H17 suite result.
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

- [x] Confirm no live H17 service submits batch photometry via
  `PhotometryManager.submit_batch` / `PhotometryBatchWorker` (both now raise;
  `scripts/qa_server.py` and `scripts/monitor_server.py` untouched).
  **Executed on H17 2026-07-03 — PASS (see §4a).**
- [x] Confirm `dsa110 index add …` on H17 still works via the old install
  (this repo no longer ships the `dsa110` entry point).
  **Executed on H17 2026-07-03 — PASS (see §4a).**
- [x] Approve deleting the obsolete `.pth` shim from cloud-VM images
  (`~/.local/lib/python3.12/site-packages/dsa110_contimg_shim.py` + loader).
  **Approved by user and executed 2026-07-03 — shim found NOWHERE (§6):
  h17/h23/calibration/dsacamera all clean; digocean and jakobtfaber-ai
  unreachable (apparently decommissioned); no repo provisioning installs it,
  so ephemeral cloud VMs are born clean. Nothing to delete.**
- [x] Check no deploy tooling pins the old dist name `dsa110_contimg`.
  **Executed on H17 2026-07-03 — PASS (see §4a).**
- [x] Merge decision on PR #93; after merge, remove the H17 review worktree:
  `cd /data/dsa110-continuum && git worktree remove /tmp/contimg-migration-validate`.
  **Merged 2026-07-04T00:38Z as `baef485` (merge commit, repo convention);
  worktree removed, H17 branch + bundle cleaned, H17 main fast-forwarded to
  `baef485` (§6).**

### 4a. H17 execution evidence (items 1, 2, 4 — run 2026-07-03)

**Item 1 — no live service uses the retired batch-photometry API: PASS.**

- Process table (`ps aux`): every running pipeline service is the **old**
  `dsa110_contimg` package — API (`dsa110_contimg.interfaces.api.app` :8000),
  GraphQL (`dsa110_contimg.graphql.app` :8001), Dagster code-server + gRPC
  (`dsa110_contimg.workflow.dagster`), Dagster subgraph (:3211), plus
  `gallery_watch.sh` inotify watchers. The only process touching the new repo
  is a static `python -m http.server 8765` serving
  `/data/dsa110-continuum/products/lightcurves` — no Python imports.
- systemd units (`dsa110-api`, `dsa110-dagster-webserver`, `dsa110-webui`,
  `dsa110-dagster-mux`, `dsa110-frontend`, `contimg-pointing-monitor`): all
  `ExecStart` old-package modules with
  `PYTHONPATH=/data/dsa110-contimg/backend/src` — none load `dsa110_continuum`.
- Grep of `/data/dsa110-continuum/{scripts,dsa110_continuum}` for
  `submit_batch|PhotometryBatchWorker` outside `photometry/{manager,worker}.py`
  and tests: **zero hits**.
- Grep of `/data/dsa110-contimg/backend/src` for `dsa110_continuum` imports:
  **zero hits** — the old package never calls into the new one.
- Crontabs (ubuntu + root): only a **disabled** old-package maintenance job and
  an unrelated hippo sync — no photometry submission.

**Item 2 — `dsa110 index add` works via the old install: PASS.**

- `/opt/miniforge/envs/casa6/bin/dsa110` exists; shebang wrapper imports
  `dsa110_contimg.interfaces.cli.main:cli` — owned by dist `dsa110_contimg
  0.1.0` (editable install from `/data/dsa110-contimg/backend`), confirming
  the entry point comes from the old install, not this repo.
- Live handshake, read-only: `dsa110 index status` → exit 0, 37 561 indexed
  files, per-date table.
- Live run: `dsa110 index add --start 2026-01-25 --end 2026-01-25 --directory
  /data/incoming` → exit 0; normalized 2894 files into 181 groups, "Indexed 0
  files successfully" — correct idempotent no-op on an already-indexed date.

**Item 4 — no deploy tooling pins the old dist name: PASS.**

- New repo (`/data/dsa110-continuum`): grep of all deploy-shaped files
  (`*.txt|toml|cfg|yml|yaml|sh|Dockerfile*|*.service`) for `dsa110_contimg` /
  `dsa110-contimg` pins or `pip install` references — only hit is a **comment**
  in `.github/workflows/python-tests.yml:33` naming the data path
  `/stage/dsa110-contimg/images` (filesystem path, intentionally unchanged;
  not a dist pin).
- `/etc/systemd/system/*.service`: no `pip install` anywhere; old-package
  units reference module paths/PYTHONPATH (their own package, expected).
- Old-repo deploy tooling (`ops/docker/*/Dockerfile*`, `scripts/ops`): all
  pip installs are third-party deps or **path-based editable installs**
  (`pip install -e ./backend`, `pip install -e .`) — never the dist name.
- casa6 env `pip list`: `dsa110_contimg 0.1.0` (editable, old repo — its own
  install) and `dsacamera-monitor 0.1.0` (from
  `/data/dsa110-continuum/tools/dsacamera-monitor`; its `pyproject.toml` has
  no `dsa110_contimg` reference). No index-based pin of the old dist exists.

## 5. Recommendations

**Critical:** none — all automated criteria pass.

**Important:**
- ~~Complete the operational sign-off (manual items 1–2) before merging~~
  **Done 2026-07-03 (§4a): no live consumer of the retired batch-photometry
  API exists on H17, and the `dsa110` CLI works via the old install.** No
  operational blocker to merging remains.

**Nice to Have:**
- Dependency-diet pass on `pyproject.toml` (drop Dagster/Django-era deps;
  update `[project.urls]` to the canonical repo).
- Consider a CI job that runs the order-permutation import sweep to guard
  against future guard-swallowed circular imports.

**Follow-Up:**
- ~~Delete the cloud-VM `.pth` shim (manual item 3)~~ — done 2026-07-03:
  shim absent on every reachable host; nothing provisions it (§6).
- ~~After merge, retire the H17 temp worktree and the fetched branch ref~~ —
  done 2026-07-03 (§6).

## 6. Post-merge addendum (2026-07-03/04)

PR #93 merged as `baef485` (2026-07-04T00:38Z). During post-merge worktree
removal, the validator discovered a provenance error in this report's H17
evidence and corrected it:

**Worktree content mismatch (correction).** `git worktree remove` refused on
"modified files"; the staged diff *reverted* the `qa_compare.py`
circular-import fix — proof the checkout was older than its `cdd0eaa` HEAD.
Reflog + timestamps confirm: the worktree was created 13:06:14 PDT from the
first bundle (tip `487e342`); `cdd0eaa` was committed 13:07:19 PDT and the
second bundle fetch fast-forwarded the branch ref in place without refreshing
the checkout (`fetch … -f: fast-forward` in the branch reflog); pytest cache
written 13:11:35 PDT. Therefore every H17 run in that worktree (1228-test
suite, co-load revalidation, batch dry-run) exercised **`487e342`**
(end-of-Phase-7) content. The Phase-8 tail deltas never exercised on casa6
were: the `qa_compare.py` function-scope-import fix, `calibration/__init__.py`
export additions, `utils/logging/pipeline.py` category-mapping rewrite,
`pyproject.toml` packaging rename, `scripts/check_import_migration.py` tweak,
CI workflow, and docstring touches — each already machine-verified on Mac
(full suite, 12× permutation sweep) and GitHub CI at `aaa721c`.

**Gap closure — authoritative H17 suite at the merge commit.** H17
`/data/dsa110-continuum` main fast-forwarded `82e3d96` → `baef485` (clean;
untracked `.emdash/` separate lane preserved) and the full casa6 suite re-run
there: **`1226 passed, 2 skipped` (0 failed) in 771.64 s** (12:51) —
`/opt/miniforge/envs/casa6/bin/python -m pytest tests/ -q` with
`PYTHONPATH=/data/dsa110-continuum`, log `/tmp/pytest-baef485.log` on H17.
This exceeds the plan bar (≥ 1168 passed) at the exact merged SHA, on the
production casa6 environment, covering every Phase-8 tail delta.

**Shim sweep (manual item 3, executed).** Probed every reachable host for
`dsa110_contimg_shim.py` / its `.pth` loader under `~/.local/lib/python*/…`:
h17 (full `find` across all python versions), h23, calibration, dsacamera —
**absent everywhere**. digocean (144.126.219.142) timed out and
jakobtfaber-ai (tailnet) refused connections — both apparently
decommissioned. Repo-side grep: no provisioning (no `.cursor/environment.json`,
no Dockerfile) installs the shim, so fresh/ephemeral cloud VMs are born
clean. Result: nothing to delete; the follow-up is closed by absence.

**Lane cleanup.** H17: review worktree force-removed (content proven to be
the `487e342` checkout — nothing unique), `agent/contimg-import-retirement`
branch deleted (`git branch -d` succeeded ⇒ merged), `/tmp/contimg-retirement.bundle`
removed. Mac: local branch deleted (`-d`, merged). Preserved separate lanes on
H17: `.emdash/` worktree and two `.windsurf` worktrees (not this task's).

## References

- Plan: [plan-contimg-import-retirement.md](plan-contimg-import-retirement.md)
- Implementation: [implement-contimg-import-retirement.md](implement-contimg-import-retirement.md)
- PR: https://github.com/dsa110/dsa110-continuum/pull/93
