# Implementation Summary: Phase A — per-artifact QA views (caltable #56, tile #55, MS #54)

---
**Date:** 2026-07-15
**Author:** AI Assistant
**Status:** Complete
**Plan Reference:** [plan-phase-a-artifact-qa-views-2026-07-15.md](plan-phase-a-artifact-qa-views-2026-07-15.md)

---

## Overview

All three per-artifact QA views shipped as three PRs to `dashboard-production` (#120, #121,
#122), each merged after local gates went green (the repo's CI only triggers on PRs to `main`).
The dashboard now runs under systemd (`dsa110-dashboard.service`) from a dedicated
`dashboard-production` worktree at `/data/dsa110-continuum-dashboard`, replacing the manually
launched stale process — the walkthrough's operational caveat. Campaign gate row A is done.

**Implementation Duration:** 2026-07-15 (single session, autonomous per Jakob's directive).

**Final Status:** ✅ Complete

## Plan Adherence

**Deviations from Plan:**

- **Baseline moved:** plan assumed `dashboard-production @ b6a303e`; a parallel lane had landed
  `57d4590` (control.py process-identity hardening). Verified no overlap with planned edits;
  proceeded on `57d4590`.
- **Stale process identity:** the plan named pid 1224700; that process had died and been
  replaced by another manual uvicorn (pid 1954561, ppid 1, live checkout). Verified same class
  (manual, non-systemd, pre-Phase-A code) before replacing it with the unit.
- **Render-failure catch broadened to `Exception`:** the planned tuple
  `(ImportError, RuntimeError, OSError, ValueError, KeyError)` missed `IndexError` from real
  adapter-layout bugs — any renderer exception now maps to a per-card 424 with the reason.
- **Closure-phase decimation changed:** planned `row_stride=8` left most antenna triangles
  without all three baselines (empty histogram). Now contiguous rows (`row_stride=1`,
  `max_rows=20000` ≈ 4 full integrations).
- **Four latent bugs fixed beyond plan scope** (all surfaced by live smoke, all in modules the
  issues wire in): see Issues Encountered.

## Phases Completed

### Phase 1: Shared artifact substrate — ✅ (commit dcee797, merged in PR #120)
`observability/artifacts.py` + 31 unit tests: strict name allowlists + containment, newest-first
discovery, cross-timestamp linking, mtime-keyed render cache.

### Phase 2: Per-caltable view (#56) — ✅ (PR #120, merge 722764a)
`observability/caltable_qa.py`, `scripts/artifact_pages.py` (caltable router), qa_server wiring,
17 tests incl. 2 live-integration; provenance sidecar card; 8 plot kinds all smoke-rendered on
real `.g`/`.b` tables; `CLOUD_SAFE_TESTS` += qa_server/substrate/caltable tests; systemd unit
repointed to the service worktree; both plan docs committed.

### Phase 3: Per-tile view (#55) — ✅ (PR #121, merge 185d321)
`observability/tile_qa.py` + tile router + 8 tests incl. live; image-gate card, bounded
scattering (424 degrade until `scattering` installed), cache temp-suffix fix.

### Phase 4: Per-MS view (#54) — ✅ (PR #122, merge 8734b3a)
`observability/ms_qa.py` + MS router + 9 tests incl. 2 live; lifecycle card; all 7 plot kinds
smoke-rendered on a real MS after fixing three latent bugs.

### Phase 5: Landing — ✅
Service worktree created; unit installed + `enable --now`; manual process replaced; live smoke
+ evidence in `outputs/dashboard-phase-a-2026-07-15/`; issues #54/#55/#56/#51/#59/#60 closed
with evidence; bug #123 filed; roadmap row A ticked.

## Files Modified

**Created:** `dsa110_continuum/observability/{artifacts,caltable_qa,tile_qa,ms_qa}.py`,
`scripts/artifact_pages.py`,
`tests/test_{artifact_substrate,caltable_pages,tile_pages,ms_pages}.py`,
`docs/rse/specs/plan-dashboard-feature-campaign-2026-07-15.md`,
`docs/rse/specs/plan-phase-a-artifact-qa-views-2026-07-15.md`, this record.

**Modified:** `scripts/qa_server.py` (router registration + nav row),
`scripts/run_cloud_safe_tests.py` (+5 test files),
`dsa110_continuum/visualization/calibration_plots.py` (plot_gains axis fix),
`dsa110_continuum/visualization/elevation_plots.py` (PHASE_DIR shape normalization),
`dsa110_continuum/qa/rfi_metrics.py` (pol/chan axis fixes),
`ops/systemd/dsa110-dashboard.service` (worktree + `DSA110_REPO_ROOT`),
`docs/operations/dashboard.md` (worktree topology + page URLs).

**Deleted:** none.

## Verification Results

### Automated Verification

- ✅ Full Phase A suite on H17: `pytest tests/test_artifact_substrate.py tests/test_caltable_pages.py tests/test_tile_pages.py tests/test_ms_pages.py tests/test_qa_server.py -q` → **131 passed** (live-integration tests included).
- ✅ `make test-cloud PYTHON=/opt/miniforge/envs/casa6/bin/python` → **331 passed** (now includes the five dashboard test files).
- ✅ `ruff check` clean on all new/modified files (pre-existing violations in touched viz modules left per CLAUDE.md).
- ✅ Live smoke (evidence: `outputs/dashboard-phase-a-2026-07-15/`): `/health` 200; three index pages 200; real caltable page + `gain_amp.png` + status JSON; real tile page + image PNG; real MS page + UV-coverage PNG; scattering card → 424 with reason; `POST /api/runs` without token → **403** (fail-closed preserved).
- ✅ `systemctl is-active dsa110-dashboard` → `active`; serving `dashboard-production @ 8734b3a` from `/data/dsa110-continuum-dashboard`.

### Manual Verification (pending Jakob)

- [ ] Walkthrough click-path: `/runs/2026-01-25` → epoch → LOW_SNR `.g` page → SNR plot + per-SPW table explain the gate reason.
- [ ] Review the three #56 deviations (recorded on the closed issue) and the `mocpy`/`scattering` optional-install question.
- [ ] Confirm `CLOUD_SAFE_TESTS` expansion (incl. `test_qa_server.py`) is wanted long-term.

## Issues Encountered

1. **`plot_gains` axis bug** — indexed CPARAM as `(nrow, nchan, npol)`; the casa_tables adapter
   returns `(nrow, npol, nchan)`. Crashed on `.g` (nchan=1), silently plotted pol-A/channel-slice
   on `.b`. Fixed (`calibration_plots.py:348,359,386`); caught by live-integration test.
2. **`extract_geometry_from_ms` PHASE_DIR shape** — hardcoded `(nfields, 1, 2)`; real MS via the
   adapter is `(nfields, 2, 1)` (CLAUDE.md invariant 4). Fixed with `np.squeeze`.
3. **`rfi_metrics` axis bugs** — waterfall collapsed the channel axis instead of pol
   (crash); `freq_occupancy` averaged `(0, 2)` leaving a 2-element "per-channel" array
   (silent). Fixed; `total_occupancy` (the only field pipeline QA consumes) was unaffected.
4. **Cache temp-name broke savefig** — builder received `*.png.tmp`; matplotlib rejects format
   `tmp`. Temp now keeps the real suffix (`*.tmp.png`).
5. **casatasks `plotbandpass` IndexError on `_0~23.b`** — falls back to the module's matplotlib
   renderer as designed; both bandpass kinds render.

## Testing Summary

**Tests Added:** 65 (31 substrate, 17 caltable, 8 tile, 9 MS) — route traversal suites for all
12 new path-taking routes, 404/424 mapping, XSS escaping, cache single-build/invalidation,
provenance sidecar handling, plot-kind gating, plus 5 H17-gated live-integration tests
satisfying each issue's "renders for at least one real artifact" criterion.

**All Tests Passing:** ✅ (131 in the dashboard suite; 331 in the CI gate)

## Performance Observations

First-hit renders on real artifacts: caltable plots ≤ ~5 s; tile image (4800²) ~10 s; MS
summary (~170 MB FLAG read) ~15 s; UV coverage (~40 MB UVW) ~5 s — all cached by artifact
mtime thereafter. Acceptable for lazy `<img>` cards; no page blocks on renders.

## Documentation Updated

- ✅ `docs/operations/dashboard.md` — worktree topology, upgrade procedure, page URLs.
- ✅ Campaign roadmap row A ticked (`plan-dashboard-feature-campaign-2026-07-15.md`).
- ✅ NumPy docstrings on all new public functions.

## Remaining Work

- [ ] Manual verification items above (Jakob).
- [ ] Decide bug #123 (fix vs delete dead ingest path).
- [ ] Optional: install `scattering` (+`mocpy`) into casa6 to light up the degraded cards.

## Next Steps

1. Phase B (#57 in-flight job state → unblocks #62 monitor_server retirement).
2. Phases C–F per the campaign roadmap; #114/#116 open questions still block E/F planning only.
3. When the gate table is fully checked: `dashboard-production → main` PR, move the H17
   checkout, delete `hardening-bugfixes-2026-07-15`.

## Lessons Learned

- The casa_tables adapter's `(nrow, npol, nchan)` layout differs from python-casacore's
  `(nrow, nchan, npol)` in the trailing axes — three independent modules assumed the latter.
  Any module reading multi-axis columns through the adapter should be smoke-tested against real
  data before being declared wired; the live-integration tests now institutionalize this for
  the dashboard.
- "Reachable from the page" acceptance criteria written from module lists can name modules with
  no valid inputs; adversarial verification before deviating kept the scope change defensible.

## References

- Plan: [plan-phase-a-artifact-qa-views-2026-07-15.md](plan-phase-a-artifact-qa-views-2026-07-15.md)
- Campaign: [plan-dashboard-feature-campaign-2026-07-15.md](plan-dashboard-feature-campaign-2026-07-15.md)
- PRs: #120 (66d7e03→722764a), #121 (545783d→185d321), #122 (→8734b3a)
- Issues: #54/#55/#56/#51/#59/#60 closed; #123 filed.
- Evidence: `outputs/dashboard-phase-a-2026-07-15/`

---

**Implementation completed by AI Assistant on 2026-07-15**
