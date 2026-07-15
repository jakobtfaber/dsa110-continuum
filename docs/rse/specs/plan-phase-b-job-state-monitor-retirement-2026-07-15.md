# Implementation Plan: Phase B — per-artifact in-flight job state (#57) + monitor_server retirement (#62, closes #49)

---
**Date:** 2026-07-15
**Author:** AI Assistant
**Status:** Draft (authored autonomously; deviations flagged on issues at close-out)
**Related Documents:**
- [Campaign roadmap](plan-dashboard-feature-campaign-2026-07-15.md) — gate row B
- [Phase A record](implement-phase-a-artifact-qa-views.md) — substrate this builds on

---

## Overview

Row B of the ship gate. Two PRs:

- **PR 4 (#57):** an "In-flight job" card on every artifact detail page — which pipeline
  process (wsclean / batch_pipeline / convert) is touching *this* artifact right now, its PID,
  and the newest log lines filtered to the artifact's timestamp. Researcher question answered:
  "why is it slow / is it stuck?" without leaving the page.
- **PR 5 (#62 + #49):** write the auth-posture ADR (the decision already shipped: pre-shared
  bearer token, fail-closed, audit log — readiness plan decision 2) → close #49; then delete
  `scripts/monitor_server.py` (option 2 of #62): every read-only endpoint is superseded
  (`/disk`,`/processes`,`/logs` → `/api/status` + #57 cards; `/ms`,`/images` →
  `/artifacts/ms|tile|mosaic`), it has no code callers, is not running (pgrep-verified
  2026-07-15), and `POST /exec` must not exist anywhere per #62.

**Goal:** #57, #62, #49 closed; no shell-execution endpoint in the repo; job state visible on
live pages.

## Current State Analysis

- `scripts/qa_server.py:337-363` — `process_status()`: ps scan for
  `("batch_pipeline.py", "wsclean", "aoflagger")`, returns pid/elapsed/state/command. Generic
  (dashboard heartbeat), not artifact-scoped.
- `dsa110_continuum/observability/control.py:229` — `list_runs()`: launcher-owned runs with
  `status`, `pid`, `log_path`, `request_json`; `running` reconciled against `/proc`.
- `scripts/qa_server.py:310-317` — `_tail()` helper (unfiltered).
- `scripts/monitor_server.py` (78 lines) — `/processes` ps-aux keyword grep (`:35-42`),
  `/logs` newest file from `/tmp` glob patterns (`:52-62`), `POST /exec`
  `subprocess.run(shell=True)` behind `TIDY_EXEC_SECRET` (`:63-73`). Unmounted, not running,
  callers = docs only (verified).
- Artifact pages (Phase A): `scripts/artifact_pages.py` — caltable/tile/ms detail pages with
  card sections; `observability/artifacts.py::related_artifacts` already derives the
  timestamp/date for any artifact.
- `docs/adr/0001-canonical-hourly-epoch-coadd.md` — ADR format (Status + Considered Options).
- #49 OPEN: the auth decision shipped in the readiness arc but was never recorded as the ADR
  it promised (`plan-dashboard-production-readiness.md:1324`).

**Process↔artifact ground truth:** wsclean argv embeds the tile imagename
(`.../{ts}` prefix); conversion argv embeds the MS/HDF5 timestamp; `batch_pipeline.py` argv
carries `--date {date}` while its per-run log (control registry `log_path`) names specific
timestamps per tile/MS line. So: full-timestamp substring match on argv → direct process hit;
date match on a batch argv → date-level hit whose log is then line-filtered by full timestamp.

## Desired End State

- Caltable/tile/MS pages + status JSONs carry a `job` section: matched processes (pid,
  elapsed, command), date-level batch runs, and ≤40 log lines filtered to the artifact
  timestamp; "No active job touching this artifact" otherwise.
- `docs/adr/0002-observability-auth-posture.md` exists; #49 closed.
- `scripts/monitor_server.py` deleted; CLAUDE.md + `docs/operations/dashboard.md` updated;
  `check_contimg_mentions`/test-cloud still green; #62 closed.

## What We're NOT Doing

- No mosaic-epoch job card (no mosaic HTML page exists yet — that's Phase C's stage-event
  work; noted on #57 at close).
- No new process-spawning, no push updates (polling substrate stands), no `/tmp` log-glob
  resurrection (control-registry logs are the authority; the ad-hoc `/tmp/convert_*.log`
  pattern predates the registry).
- Not moving monitor_server to `tools/` (option 1): nothing left worth moving once `/exec` is
  out — recorded on #62 for async review.

## Implementation Approach

`observability/job_state.py` (pure stdlib + control registry; no CASA):

- `active_processes()` — one `ps -eo pid=,etimes=,args=` pass filtered by
  `("wsclean", "aoflagger", "batch_pipeline.py", "dsa110", "casa")`, excluding the scanner.
- `jobs_for_timestamp(ts, control_config=None, max_lines=40)` →
  `{"processes": [argv contains ts], "batch": [argv contains ts[:10] date],
    "runs": running registry runs whose log tail mentions ts (line-filtered),
    "active": bool}`.
- Routers call it inside the existing summary/status handlers; pages render a "In-flight job"
  card between the related-links row and the metrics tables.

Tests monkeypatch `active_processes` and `pipeline_control.list_runs` (fake log file on
tmp_path) — one in-flight scenario per #57's acceptance criterion, plus the no-job state and
the ts-filtering unit. Live H17 test asserts the card renders (either state).

PR 5 is mechanical: ADR text (decision already made), `git rm scripts/monitor_server.py`,
CLAUDE.md service-table + `<important>` block edits, dashboard.md network-posture line,
close-out comments.

## Implementation Phases

### Phase B1 (PR 4, closes #57)

1. Failing tests `tests/test_job_state.py`: `jobs_for_timestamp` matches ts in fake process
   argv; date-level batch match; running-run log filtered to ts lines; inactive → empty;
   route test: MS page shows "In-flight job" card with PID when monkeypatched active, and
   "No active job" otherwise; status JSON carries `job`.
2. Implement `dsa110_continuum/observability/job_state.py`; wire into
   `scripts/artifact_pages.py` (all three status handlers + pages).
3. `CLOUD_SAFE_TESTS` += `tests/test_job_state.py`; ruff; full suite; live smoke on a page.

### Phase B2 (PR 5, closes #62 and #49)

1. `docs/adr/0002-observability-auth-posture.md` (token/fail-closed/audit/Cloudflare-Access
   optional hardening; considered: per-user auth, mTLS, Dagster-side auth).
2. `git rm scripts/monitor_server.py`; update `CLAUDE.md` (services block + command table),
   `docs/operations/dashboard.md` (posture paragraph); rg confirms no live references remain.
3. Gates green; PR; merge; close #49 (ADR link), #62 (supersession map per endpoint).

## Success Criteria

**Automated:** `pytest tests/test_job_state.py tests/test_qa_server.py tests/test_caltable_pages.py tests/test_tile_pages.py tests/test_ms_pages.py -q` green on H17;
`make test-cloud` green; `rg -l "monitor_server" scripts/ dsa110_continuum/ tests/ Makefile ops/` → empty after PR 5;
live: `curl /artifacts/ms/<name>/status | jq .job` present; no `/exec` route anywhere
(`rg -n "shell=True" scripts/ dsa110_continuum/` clean).

**Manual (Jakob):** during the next real pipeline run, open an in-progress artifact page and
confirm the card names the right process and log lines; ratify the delete-vs-move choice on
#62 and the ADR wording on #49.

## Open Questions

*(none — delete-vs-move recorded as a reviewable deviation on #62)*

---

**References:** issues #57/#62/#49; `plan-dashboard-production-readiness.md` decision 2;
Phase A record. PRs: #124? (numbers assigned at creation).
