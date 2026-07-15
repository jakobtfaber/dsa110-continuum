# Implementation Summary: Production-ready pipeline dashboard (monitoring + control)

---
**Date:** 2026-07-15
**Author:** AI Assistant (Claude Fable 5)
**Status:** Complete (code); manual verification + deploy steps pending user
**Plan Reference:** [plan-dashboard-production-readiness.md](plan-dashboard-production-readiness.md)

---

## Overview

Implemented the full plan on branch `dashboard-production` (7 commits, `7183fe7..136b99c`):
landed the previously-uncommitted observability baseline, built the token-gated pipeline
control plane (module + API + UI), added run-provenance and light-curve science views,
epoch auto-discovery, the scheduled automation entrypoint, systemd units, and operations docs.

**Implementation Duration:** 2026-07-15 (single session)

**Final Status:** ✅ Complete (code + automated verification); deploy/manual steps reserved for user

## Plan Adherence

**Deviations from Plan:**

- **Deviation 1:** Phase 0's `gh issue comment 51` and Phase 6's PR creation were NOT executed.
  - **Reason:** outward-facing actions reserved for user review per session constraints.
  - **Impact:** none on code; listed under Remaining Work.
- **Deviation 2:** Phase 2's "manual smoke on H17" was performed via the control module
  directly (`run_dry_run` against the real `scripts/batch_pipeline.py`, date 2026-01-25
  hours 22–23) rather than HTTP against the live :8767 server.
  - **Reason:** the running qa_server instance (PID 3226242, started 2026-07-14) still serves
    the pre-branch code; restarting a production service before user review was out of bounds.
  - **Impact:** end-to-end integration with the real pipeline is verified (179 MS discovered,
    Dec-strip check passed, dry-run plan rendered); the HTTP hop is exercised by 20+
    TestClient tests. Live-restart is a deploy step.
- **Deviation 3:** small test additions beyond plan (failed-run reaping, calendar-date
  validation, no-points light-curve page, invalid-date auto-launch outcome) and an extra
  registry-missing 404 guard on `GET /api/runs/{run_id}`.
  - **Reason:** edge cases surfaced while writing the planned tests.
  - **Impact:** stricter coverage, no interface change.

## Phases Completed

| Phase | Commit | Summary |
| --- | --- | --- |
| 0 — Baseline | `7183fe7` | Routed qa_server + observability package + 4 test suites committed (80 tests green first). |
| 1 — Control module | `5284d84` | `observability/control.py`: validated RunRequest→argv allowlist, SQLite registry, detached process-group launch, reaper thread, SIGTERM→SIGKILL group terminate, single-flight guard, dry-run. 12 tests. |
| 2 — Control API | `18d222b` | `/api/runs` list/detail/launch/terminate on qa_server; fail-closed bearer auth (constant-time), JSONL audit (token never written). 7 tests. |
| 3 — Control UI | `f6e1b9d` | Dashboard control panel (runs table, launch form, dry-run preview, terminate; token field suspends auto-reload), `/control/runs/{id}` detail pages with escaped log tails, `discover_epochs` replacing hardcoded EPOCHS (fallback retained). 6 tests. |
| 4 — Provenance | `b2f1540` | `/runs/{date}` manifest page: verdict badge, gate reasons, per-epoch QA table linked to mosaic artifacts, run report; partial manifests render placeholders. 3 tests. |
| 5 — Light curves | `344761a` | `/sources/lightcurve` + `.png`: cos(dec)-scaled nearest-match per epoch, per-point mosaic click-through, η/V from `photometry/metrics.py` canonical formulas; dashboard lookup form. 7 tests. |
| 6 — Automation/ops | `136b99c` | `scripts/auto_pipeline.py` (single-flight scheduled launcher, 3 tests), `ops/systemd/` units, `docs/operations/dashboard.md`, CLAUDE.md updates (control API note; corrected stale "monitor_server live" claim). |

## Files Modified

**Created:** `dsa110_continuum/observability/control.py`, `tests/test_observability_control.py`,
`scripts/auto_pipeline.py`, `tests/test_auto_pipeline.py`,
`ops/systemd/dsa110-dashboard.service`, `ops/systemd/dsa110-autopipeline.service`,
`ops/systemd/dsa110-autopipeline.timer`, `docs/operations/dashboard.md`
(plus Phase 0 landing of the previously-untracked observability package and test suites).

**Modified:** `scripts/qa_server.py` (control router + pages + discovery + light curves),
`tests/test_qa_server.py` (23 new tests), `CLAUDE.md` (two sections).

**Deleted:** none.

## Verification Results

### Automated

- ✅ `make test-cloud PYTHON=/opt/miniforge/envs/casa6/bin/python` — 200 passed.
- ✅ Targeted suites (`test_qa_server` 63, `test_observability_control` 12,
  `test_auto_pipeline` 3, hour_state/mosaic_preview/no-dagster 40) — 118 passed.
- ✅ `ruff check` + `ruff format --check` clean on all touched files.
- ✅ Dagster-free import: `import dsa110_continuum.observability.control` loads no `dagster`.
- ✅ `tests/test_mosaic_import_no_dagster.py` still green.
- ✅ Real-pipeline smoke: `run_dry_run(RunRequest(date='2026-01-25', start_hour=22,
  end_hour=23))` executed the actual `batch_pipeline.py --dry-run` (179 valid MS, Dec-strip
  16.1° check passed, plan printed).
- ✅ `systemd-analyze verify` on shipped units — no unit-specific errors (system-wide legacy
  warnings only).
- ⏳ Full `pytest tests/ --ignore=tests/test_mosaic_ra_wrap.py` was launched; result recorded
  in the completion summary / final report.

### Manual (pending user)

- [ ] Restart qa_server from the branch; browser checks: control panel, discovered epochs,
  `/runs/2026-07-13` (real DEGRADED manifest), light curve at RA 47.499 Dec 17.0998.
- [ ] Full intervention loop on a scratch date (dry-run → launch → log tail → terminate;
  verify `pgrep -g` empty after terminate).
- [ ] Fail-closed check with token env unset.
- [ ] Install systemd units + token env file (`docs/operations/dashboard.md`), confirm timer.

## Issues Encountered

Minor only: ruff D102/D103/I001 on first pass (docstrings + import order, fixed); FastAPI
route needed an explicit registry-missing guard to avoid a 500 on `GET /api/runs/{id}` before
any run exists.

## Testing Summary

**Tests added:** 31 new (12 control module, 7 control auth, 3 discovery, 3 UI, 3 provenance,
7 light curve — counting per class; plus 3 auto-pipeline) on top of the 80 landed in Phase 0.
All criterion-first (injection safety, fail-closed auth, process-group kill incl. grandchild,
registry state machine, partial-manifest resilience, positional matching).

**All tests passing:** ✅ (cloud gate 200; targeted 118)

## Remaining Work

- [ ] User review of research + plan + this summary; then PR from `dashboard-production`.
- [ ] Issue-tracker updates (#48/#49 decisions, #51/#53/#59/#60 partial closes) — outward-facing.
- [ ] ADR in `docs/adr/` recording the #48/#49 decisions (short follow-up commit).
- [ ] Deploy: restart qa_server from branch, token env file, systemd install (sudo).
- [ ] Deferred slices per plan: #52, #54–#57, full #53 lifecycle badges, #58/#61 mosaic
  routes, #62 monitor_server retirement, legacy-stack teardown plan.

## Next Steps

1. User reviews `research-dashboard-production-readiness.md`, the plan, and this summary.
2. `ai-research-workflows:validating-implementations` after the manual checks.
3. PR + issue hygiene + deploy per Remaining Work.

## References

- [Plan](plan-dashboard-production-readiness.md) · [Research](research-dashboard-production-readiness.md)
- Commits: `7183fe7`, `5284d84`, `18d222b`, `f6e1b9d`, `b2f1540`, `344761a`, `136b99c`

---

**Implementation completed by AI Assistant on 2026-07-15**
