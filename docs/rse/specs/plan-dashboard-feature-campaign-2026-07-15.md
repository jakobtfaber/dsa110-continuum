# Plan: dashboard feature campaign — ship gate for dashboard-production → main

---
**Date:** 2026-07-15
**Status:** Roadmap approved-in-scope by user (all items declared ship-blocking); per-phase
implementation plans to follow per issue.
**Baseline:** `dashboard-production @ b6a303e` (monitoring+control core + observability fixes).
Walkthrough + efficacy evidence: `outputs/dashboard-walkthrough-2026-07-15/`.

---

## Ship gate (user, 2026-07-15)

`dashboard-production → main` does NOT ship until all of:

| Phase | Feature | Issues | Notes |
| --- | --- | --- | --- |
| ✅ A | Per-artifact QA views: MS / tile / caltable | #54 #55 #56 (closed 2026-07-15, PRs #120–#122; record: `implement-phase-a-artifact-qa-views.md`) | Closes the "QA gate says why → show me the artifact" loop (e.g. gaincal LOW_SNR → inspect the .g). Read-only pages on the existing routed substrate. |
| ✅ B | Per-artifact in-flight job state | #57 | Shipped PR #125; `monitor_server.py` deleted + auth ADR 0002 (#62, #49) in PR #126 (2026-07-15). |
| C | Artifact browser + stage events | #53 #52 | #52's stage-event → diagnostic-card contract is the foundation; #53 layers lifecycle badges on discovery. |
| D | File-exploration UI (diagnostic + science products) | #115 | Generalizes #53; hard root allowlist, traversal-guard reuse. |
| E | Survey-vs-sky comparison view | #114 | Reference-catalog overlay vs forced-phot detections; surfaces the completeness gate visually. Needs catalog + flux-floor decisions (in issue). |
| F | Telescope status page | #116 | v1 from H17-visible data (incoming freshness, pointing Dec vs strip, refant health, disks, H23 probe). Needs scope confirmation (in issue). |

Sequencing rationale: A first (highest operator value, pure read-only, no new state); B second
(unblocks the standing security retirement #62); C before D (D generalizes C's discovery layer);
E and F are independent of A–D and can run parallel to any phase once their open questions are
answered.

## Standing constraints

- Substrate stays: routed FastAPI, server-rendered HTML, polling (no WebSockets/SSE), token-gated
  mutations, fail-closed. Per `plan-dashboard-production-readiness.md` decisions 1–5.
- Every phase: tests in `tests/test_qa_server.py` style (path-traversal suite for any new
  path-taking route), `make test-cloud` green, live smoke on H17.
- `scripts/qa_server.py` is LIVE production — phases land as PR-sized increments on
  `dashboard-production`, dashboard restarted under systemd per increment (see walkthrough caveats:
  adopt `ops/systemd/dsa110-dashboard.service` at Phase A landing, fixing the stale-process gap).
- Open ADRs #48/#49/#50 constrain C/D/E: #50 (interactive FITS vs pre-rendered) should be decided
  before D commits to a FITS renderer beyond thumbnails.

## Out of scope (unchanged from production-readiness plan)

Dagster/Prefect as control plane; frontend framework rewrite; legacy stack teardown;
push transport.

## Next actions

1. Jakob: answer the open questions in #114 (master catalog, flux floor) and #116 (status page
   contents beyond data-derivable v1).
2. Phase A implementation plan (`ai-research-workflows:planning-implementations`) → implement →
   PR to dashboard-production → restart under systemd.
3. Iterate B→F; re-run the walkthrough/efficacy capture per phase; ship main when the gate table
   is fully checked.
