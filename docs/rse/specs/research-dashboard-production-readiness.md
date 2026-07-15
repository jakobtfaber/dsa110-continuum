# Research: Production-ready pipeline dashboard (orchestrator choice + UI substrate)

**Date:** 2026-07-15
**Scope:** both (internal codebase + external prior art)
**Codebase state:** commit `2a62963`, 2026-07-15 (plus significant *uncommitted* working-tree state — see Findings §1)
**Related Documents:** `outputs/observability-dashboard-2026-07-14/{DECISION,ARCHITECTURE,STATUS,HUMAN_VALIDATION,THIRD_OPTION_CODEX,THIRD_OPTION_FABLE5}.md`; GitHub issues #48–#62

## Question / Scope

The target product is a dashboard UI that (a) monitors the fully-automated hourly imaging
pipeline end-to-end (diagnostics + science data products), (b) lets an operator manually run or
re-run the pipeline and individual stages when automation fails, and (c) is production-ready.
The open architectural question: is Dagster the right substrate, given "we currently use
Dagster"? In scope: every existing UI/observability surface, the pipeline's actual
execution/control model, the state of issues #48–#62, live H17 infrastructure, and external
evidence on Dagster-as-control-plane vs alternatives. Out of scope: implementing anything (this
doc feeds planning).

## Codebase Findings

### 1. "Currently using Dagster" is three different things — none of them is the target product

1. **Legacy `dsa110_contimg` Dagster** (running since 2026-03-01, *outside this repo*):
   `/data/dsa110-contimg/backend/workspace.yaml` loads
   `dsa110_contimg.workflow.dagster.definitions` with 14+ job modules (science_mosaic,
   on_demand_mosaic, calibrate_ms, bandpass_calibration, manual_conversion, imaging_advanced,
   file_management, database_maintenance, health_monitoring, assets, asset_checks). It is served
   by host processes (PIDs 2900–2902: legacy uvicorn API, `scripts.dagster_mux_app`,
   dagster-webserver on 127.0.0.1:3000) and a Docker stack (`dsa110-dagster-{adapter,webserver,
   code-server,daemon}` on :3211, `dsa110-graphql` :7001, `dsa110-carta` :9002,
   apollo-router), fronted by nginx on :80 (proxies `carta/`, `api/`, `dagster`) and :3210.
   These orchestrate the **old retired package**, not `dsa110_continuum`. Old systemd units
   (`dsa110-api`, `dsa110-dagster-webserver`, `dsa110-webui`, `dsa110-dagster-mux`,
   `dsa110-frontend`) all belong to the old package
   (`docs/archive/contimg-retirement/validation-contimg-import-retirement.md:150-190`).
2. **New read-only Dagster tracer-bullet** (2026-07-14, this repo, **uncommitted**):
   `dsa110_continuum/observability/dagster_defs.py` — six external-state assets in group
   `hour_11_observability` produced by one `@multi_asset` (`dagster_defs.py:80-172`), a
   1-minute always-running sensor (`dagster_defs.py:182-190`), webserver on 127.0.0.1:3212.
   Materializations are metadata-only; no science code runs (`dagster_defs.py:97`).
   `hour_state.py:151-233` is the package-local read-only collector (MS/cal/tile/mosaic/HDF5
   counts, disk, processes, run products, campaign state machine running>finished>absent at
   `hour_state.py:215-222`). Generic over date/hour via `HourStateConfig` (env
   `DSA110_OBSERVE_DATE`/`_HOUR`, defaults 2026-07-13/11 at `hour_state.py:18-19`); hour-11
   naming and a couple of alias paths are the only hardcoding
   (`mosaic_preview.py:61,193-202`).
3. **The retired Dagster science bridge** (this repo): `ScienceMosaicBridgeJob.execute()`
   raises `RuntimeError` (`dsa110_continuum/mosaic/science_jobs.py:133-138`). Production
   science mosaicking deliberately does **not** go through Dagster.

Yesterday's `DECISION.md` chose Dagster **only** for the read-only observability surface and
explicitly not for routing science mosaicking; `POST /exec` and all mutating surfaces were kept
out by design. The new requirement (run/re-run the pipeline from the UI) goes beyond that
decision's scope, so the orchestrator question must be re-opened for the *control* dimension —
that is the external research question below.

### 2. Live UI surfaces (and their status)

| Surface | Port | Status | Notes |
| --- | --- | --- | --- |
| `scripts/qa_server.py` (FastAPI) | 8767 | **live, public** via Cloudflare tunnel; **no auth** | 596 lines, uncommitted rewrite (+559/−173 vs HEAD). Routes: `/` HTML dashboard (30 s full-page reload, `qa_server.py:510`), `/artifacts/mosaic/{date}/{epoch}/status|thumb.png` router (`:526-549`), `/api/status` ops snapshot (`:558-566`), `/health`. Read-only; path-traversal- and XSS-hardened; 562-line test file. Hardcoded 6-epoch `EPOCHS` list (`:52-59`). |
| Dagster tracer-bullet | 3212 | live, localhost-only | See §1.2. Human-validated 2026-07-14 (`HUMAN_VALIDATION.md`: all checks PASS). |
| `scripts/monitor_server.py` (FastAPI) | — | not running (verified via `pgrep`, 2026-07-15; CLAUDE.md's "live, host-ops" note is stale), no launcher in repo, not tunneled | 78 lines. Read-only probes plus **`POST /exec` running `subprocess.run(shell=True)`** gated only by shared-secret env (`monitor_server.py:63-73`). Untested. Issue #62 plans its retirement. |
| `dsa110_continuum/mosaic/api.py` | — | **dormant** | 535-line `/mosaics` router incl. mutating `POST /mosaics/create` (spawns `execute_mosaic_pipeline_task`, `api.py:224-227`) and `DELETE /mosaics/{name}`. Never mounted anywhere; `configure_mosaic_api` never called (repo-wide grep). Issues #58/#61 plan mounting read-only/mutating subsets. |
| `python -m http.server 8765` | 8765 | live | Bare directory listing of `/data/dsa110-proc/products/lightcurves`. |

Frontend stack facts: FastAPI ≥0.115 / uvicorn / dagster ≥1.12.10 in `pyproject.toml:46-77`;
**no** Panel/Plotly/Streamlit anywhere; bokeh pinned only for a CVE and never imported; a
`django>=4.2` dependency exists ("Phase 1: Add VAST-style monitoring UI") with zero Django code —
aspirational leftover.

### 3. Pipeline execution/control model (what a control dashboard must drive)

`scripts/batch_pipeline.py` (2343 lines) is **CLI-only** — `main()` at `:1217`, no importable
entrypoint. Stages: per-epoch gaincal → RFI flagging → per-tile calibrate+image (subprocess pool,
SIGKILL timeout `:81,:1071-1142`) → hourly-epoch mosaics (`:846-897`) → forced photometry →
manifest/summary/report emission (`:2226-2319`).

Manual-intervention primitives **already exist as CLI knobs**:

- per-tile re-run: `--force-recal` (`:1320`), checkpoint resume via `.tile_checkpoint.json`
  (atomic writer `:424,479-482`), quarantine with `--quarantine-after-failures` /
  `--clear-quarantine` (`:549-574`), `--retry-failed` one-retry-with-cooldown (`:1137-1142`);
- per-hour re-run: `--start-hour`/`--end-hour` (`:1291-1298`) +
  `_epoch_should_rebuild` (`:519`) keyed on prior-manifest verdict;
- photometry-only: `--skip-photometry`, `scripts/regenerate_photometry.py`,
  `scripts/forced_photometry.py`;
- read-only planning: `--dry-run` → `_dry_run_main` (`:709`) prints the full rebuild/skip/
  quarantine plan and exits without writing.

Run state is **file-based JSON, not a service**: `.tile_checkpoint.json` (stage dir),
`{date}_manifest.json` (`dsa110_continuum/qa/provenance.py:42,248` — tiles, epochs, gates,
verdict CLEAN/DEGRADED/FAILED), `{date}_run_summary.json` (`batch_pipeline.py:2245-2269`, also
symlinked to `/tmp/pipeline_last_run.json` and optionally POSTed to `DSA_NOTIFY_URL`
`:2294-2310`), `run_report.md` (`qa/run_report.py:34-68`), `qa_summary.csv` (`:85-89,976`),
promotion sidecar (`:2319`). Fifteen-plus SQLite stores exist (unified
`/data/dsa110-contimg/state/db/pipeline.sqlite3` with `ms_index`, `images`, `photometry`,
`ese_candidates`, calibration tables, etc. — `dsa110_continuum/database/schema.sql:3-159`), but
the run/checkpoint state a dashboard needs is in the JSON artifacts.

**There is no automation today.** No cron/systemd runs `dsa110_continuum`'s pipeline; campaigns
are hand-launched per hour (e.g. `outputs/slowvis-mosaic-campaign-2026-07-14/batch_run_h11.log`).
"Fully automated with manual fallback" is therefore an unbuilt requirement, not a current
property — the dashboard plan must include the automation trigger itself.

The in-repo `workflow/` Job/Pipeline/Executor abstraction is largely vestigial for production:
`batch_pipeline.py` never touches it; only the dormant on-demand-mosaic path and
`calibration/pipeline.py` use `PipelineExecutor` (read-only legacy DB,
`workflow/executor.py:298-327`).

### 4. Issues #48–#62 already spec the product

All fifteen are open, `needs-triage`, zero comments, no linked PRs. Shape: three ADRs (#48
architecture: cadence/render-time/consolidation/navigation; #49 auth posture — today the public
tunnel has **no auth**; #50 interactive FITS vs pre-rendered), one tracer-bullet (#51: routed
FastAPI scaffold at :8767 — **effectively already implemented** by the uncommitted qa_server
rewrite, which has exactly the routed-artifact architecture #51 asks for, but the issue is open
and the code uncommitted), then vertical slices: stage-event→diagnostic contract (#52),
auto-discovered artifact browser with 7 lifecycle states (#53), per-MS/per-tile/per-caltable QA
views (#54–#56) wiring the existing 19 `qa/` + 40+ `visualization/` modules, per-artifact
in-flight job state (#57, prereq for retiring monitor_server #62), mounting `mosaic/api.py`
read-only (#58) then mutating routes auth-gated (#61), the forced-photometry/light-curve
science surface (#59 — "the primary science surface"), and QA-gate/provenance run view (#60).
Adjacent: #82 campaign tracker; #95/#96 green-workspace umbrellas that explicitly defer to
#48–#62 as source of truth.

Sequencing encoded in the issues: #51 blocks nearly everything; #61 blocked by #49+#58;
#62 blocked by #49+#51+#57.

### 5. Gaps between current state and the user's target

1. **Nothing is committed**: qa_server rewrite, `dsa110_continuum/observability/` (3 modules),
   4 test files — all working-tree only. First production-readiness step is landing this.
2. **No control surface at all**: nothing in the new stack can launch `batch_pipeline.py`; the
   only mutating endpoints are the unmounted `mosaic/api.py` routes and the deliberately
   unmounted `POST /exec`.
3. **No auth** on a publicly-tunneled origin (#49 undecided) — blocking for any mutating route.
4. **No automation trigger** (cron/systemd/sensor) for the new pipeline.
5. **Science-product depth missing**: no light-curve/variability view (#59), no per-artifact QA
   views (#54–#56), no lifecycle browser (#53); the historical `EPOCHS` list is hardcoded.
6. **Three parallel legacy surfaces still run** (old Dagster host+Docker stacks, nginx fronts,
   lightcurve http.server) — operator-confusing and unowned; retirement is unplanned except
   #62 (monitor_server).
7. **Dashboard state fragmentation**: run truth lives in per-date JSON artifacts + 15 SQLite
   stores + filesystem globs; every UI so far re-derives it by scanning.

## Prior Art

### Dagster OSS as a control plane — feature fit is real

Everything the manual-intervention role needs is in OSS, none of it Dagster+-gated: Launchpad
runs with editable config ([webserver docs](https://docs.dagster.io/guides/operate/webserver),
[run configuration](https://docs.dagster.io/guides/operate/configuration/run-configuration)),
daily/hourly partitioned assets ([partitioning](https://docs.dagster.io/guides/build/partitions-and-backfills/partitioning-assets)),
UI backfills over partition ranges ([backfilling](https://docs.dagster.io/guides/build/partitions-and-backfills/backfilling-data)),
re-execution from failure / arbitrary step subsets ([run retries](https://docs.dagster.io/deployment/execution/run-retries)),
and blocking asset checks as QA gates ([asset checks](https://docs.dagster.io/guides/test/asset-checks)).
Dagster+-only features (alerting, RBAC/audit, Insights) are not needed for this role
([Dagster+ scope](https://docs.dagster.io/deployment/dagster-plus)).

### Disconfirming findings on self-hosted Dagster (mandatory adversarial pass)

- **Event-log growth is unbounded by default** — no built-in retention; users report the
  `event_logs` table at ~99% of DB size, one at 250 GB
  ([discuss thread](https://discuss.dagster.io/t/5115138/hi-team-our-dagster-event-logs-table-is-like-250g-now-see-no));
  cleanup is DIY ([discussion #12985](https://github.com/dagster-io/dagster/discussions/12985),
  [issue #15526](https://github.com/dagster-io/dagster/issues/15526));
  DB tuning is a documented operator chore
  ([database tuning](https://docs.dagster.io/deployment/troubleshooting/database-tuning)).
- **Daemon/webserver memory leaks and crashes recur**:
  [#32070](https://github.com/dagster-io/dagster/issues/32070),
  [#21307](https://github.com/dagster-io/dagster/issues/21307),
  [#26287](https://github.com/dagster-io/dagster/issues/26287) (daemon to 20 GB),
  [#18997](https://github.com/dagster-io/dagster/issues/18997),
  [#19893](https://github.com/dagster-io/dagster/issues/19893) (crashes, no stack trace).
- **UI Terminate does not reliably kill grandchild subprocesses** — exactly the WSClean/CASA
  shape: steps surviving run termination is a filed bug
  ([issue #21476](https://github.com/dagster-io/dagster/issues/21476)); in-op code must catch
  `DagsterExecutionInterruptedError` and kill its own process group
  ([discussion #13986](https://github.com/dagster-io/dagster/discussions/13986)). No mid-step
  checkpointing — a killed step restarts at the step boundary (acceptable at per-tile
  granularity). Run monitoring / `dagster/max_runtime` enforcement exists but needs the daemon
  ([run monitoring](https://docs.dagster.io/deployment/execution/run-monitoring)).
- Net operational footprint on one host: webserver + daemon + code-server (3 services), Postgres
  strongly advised over SQLite
  ([OSS deployment architecture](https://docs.dagster.io/deployment/oss/oss-deployment-architecture)),
  plus a retention cron and planned restarts. Comparisons rate this moderate — lighter than
  Airflow, heavier than Prefect or a bare FastAPI service
  ([DZone](https://dzone.com/articles/airflow-vs-dagster-vs-prefect-which-scheduler-fits),
  [Bruin 2026](https://getbruin.com/blog/best-data-pipeline-tools-2026/)).

### Alternatives

- **Prefect 3 self-hosted** — deployments give UI-triggered runs with a parameter form,
  cancel/pause, retries ([deployments](https://docs.prefect.io/v3/concepts/deployments),
  [self-host](https://docs-3.prefect.io/3.0/manage/self-host)); lightest self-host of the three
  ([ZenML comparison](https://www.zenml.io/blog/orchestration-showdown-dagster-vs-prefect-vs-airflow)).
  Weaker partition story: date/hour become flow parameters; per-partition status grids are
  hand-built. Not installed in casa6 today.
- **Airflow** — mature trigger-with-conf and clear-to-rerun
  ([DAG runs](https://airflow.apache.org/docs/apache-airflow/stable/dag-run.html)), but the
  heaviest self-host ("0.5–1 FTE of platform engineering at scale",
  [Bruin 2026](https://getbruin.com/blog/best-data-pipeline-tools-2026/)). Overkill for one host,
  one operator.
- **Custom FastAPI driving the CLI** — zero new infrastructure (it is what `qa_server.py`
  already is) and total control of domain UI; the cost is re-implementing run-state, queueing,
  retry, and termination machinery. The build-vs-buy consensus is that orchestrators earn their
  keep when *re-run semantics over partitions* matter
  ([ZenML](https://www.zenml.io/blog/orchestration-showdown-dagster-vs-prefect-vs-airflow),
  [CodeX](https://medium.com/codex/airflow-vs-prefect-vs-dagster-which-workflow-tool-actually-fits-your-stack-d581e622cd27)) —
  but see Synthesis: in this repo the CLI already owns most of those semantics.

### Observatory prior art

- **Simons Observatory** (production, on-site): Prefect orchestrates daily packaging + reduction
  on a site compute node; chosen for Python-native flows, the monitoring UI, and
  concurrency limits ([arXiv:2406.10905](https://arxiv.org/abs/2406.10905)).
- **Keck LRIS-2** (Caltech/WMKO, in development): "workflow orchestration using Prefect"
  ([SPIE AS26 program](https://spie.org/AS26/conferencedetails/astronomy-control-software)).
- **No production observatory using Dagster surfaced** across four query variants (absence of
  evidence from these backends, not proof — but the asymmetry vs Prefect is a data point).
  Locally, this codebase already retired one Dagster science bridge.
- **Steward risk/wildcard:** Prefect acquired Dagster Labs, announced 2026-07-13, with stated
  commitments to keep both OSS lines maintained
  ([prefect.io](https://www.prefect.io/prefect-acquires-dagster),
  [dagster.io](https://dagster.io/blog/prefect-is-acquiring-dagster)). Long-horizon investment
  in two overlapping OSS orchestrators under one company is an open risk.

## Synthesis

1. **Two halves, one decision.** The *monitoring/science-visualization* half (per-artifact QA
   views, lifecycle browser, light curves, provenance) cannot be rendered by any orchestrator UI
   — it is domain UI, already spec'd as the unified FastAPI server in #48–#62 and partially
   built (uncommitted routed `qa_server.py`). That half is needed under every orchestrator
   choice. The genuinely open question is only the *control* half: what launches, re-runs, and
   terminates `batch_pipeline.py`.
2. **The control half is thinner than it looks.** `batch_pipeline.py` already implements the
   hard re-run semantics internally — idempotent per-tile skip/rebuild from checkpoint +
   manifest verdict, quarantine, retry, per-hour windows, photometry-only, dry-run
   (Findings §3). An external orchestrator wrapping the CLI would *duplicate* partition/state
   bookkeeping the CLI already owns (Dagster partition status vs manifest verdicts = two
   sources of truth), while the standard build-vs-buy argument for orchestrators assumes that
   machinery is absent.
3. **Dagster evidence cuts both ways.** Feature fit for control is 1:1 and already installed;
   but the operational burden is real (event-log retention, daemon leaks), the
   grandchild-kill caveat lands exactly on WSClean/CASA-shaped work, this repo already walked
   back one Dagster science path, the legacy Dagster stack on H17 is a live example of the
   unmaintained end-state, and observatory precedent (Simons, Keck) went Prefect.
4. **Light recommendation (design deferred to planning):** keep Dagster where yesterday's
   decision put it — read-only observability, optionally with an eventual sunset — and build
   the production dashboard as the #48–#62 unified FastAPI server, adding the control plane as
   a *small, auth-gated run-launcher* in that same service: spawn `batch_pipeline.py` in its
   own process group with chosen flags (date/hours/`--force-recal`/`--retry-failed`/…), persist
   a run registry, stream/serve logs, and expose terminate as process-group kill. Automation
   then becomes a scheduler (systemd timer, or the existing Dagster sensor if retained) calling
   the same launcher — one code path for automated and manual runs. Dagster-as-control
   (partitioned assets wrapping the CLI) remains the documented fallback if a partition-grid UI
   proves indispensable; Prefect only becomes attractive if the ops burden of Dagster is being
   paid anyway and a migration window opens.
5. **Gaps that block "production-ready" regardless of the orchestrator decision** (Findings §5):
   uncommitted work must land; auth (#49) gates every mutating route; no automation trigger
   exists; the science surfaces (#53–#60, esp. #59) are unbuilt; legacy stacks need a
   retirement plan; run-state fragmentation needs a single read model.

Open questions for planning: auth mechanism choice (#49 options: VPN-only / basic auth /
pre-shared token / Caltech OAuth vs the current Cloudflare tunnel posture); whether the
run-launcher lives in `qa_server.py` or a separate auth-boundary service; lifecycle-state read
model (derive on scan vs persist); fate of the Dagster tracer-bullet (keep as-is vs fold its
collector into the FastAPI status API — the collector `hour_state.py` is already
Dagster-free); legacy-stack retirement sequencing.

## References / Sources

- Code: anchors inline above (`scripts/qa_server.py`, `scripts/batch_pipeline.py`,
  `dsa110_continuum/observability/*`, `dsa110_continuum/mosaic/{api,science_jobs,pipeline}.py`,
  `dsa110_continuum/qa/{provenance,run_report}.py`, `dsa110_continuum/workflow/executor.py`,
  `docs/archive/contimg-retirement/validation-contimg-import-retirement.md`).
- Prior session: `outputs/observability-dashboard-2026-07-14/` (DECISION, ARCHITECTURE, STATUS,
  HUMAN_VALIDATION, THIRD_OPTION_CODEX, THIRD_OPTION_FABLE5, REPRODUCE).
- Issues: dsa110/dsa110-continuum #48–#62, #82, #95, #96.
- External: Dagster docs (webserver, run-configuration, partitioning-assets, backfilling-data,
  run-retries, asset-checks, run-monitoring, oss-deployment-architecture, database-tuning,
  dagster-plus) at docs.dagster.io; Dagster GitHub issues/discussions #32070, #21307, #26287,
  #18997, #19893, #21476, #23125, #15526, discussions #12985, #13986, #24232, #28182;
  discuss.dagster.io event-log-250GB thread; Prefect docs (deployments, self-host) at
  docs.prefect.io; Airflow dag-run docs; comparisons: DZone "Airflow vs Dagster vs Prefect",
  ZenML orchestration showdown, Bruin best-data-pipeline-tools-2026, FreeAgent 2025,
  Medium/CodeX 2025; observatory prior art: arXiv:2406.10905 (Simons Observatory), SPIE AS26
  astronomy-control-software program (Keck LRIS-2); acquisition: prefect.io/prefect-acquires-dagster,
  dagster.io/blog/prefect-is-acquiring-dagster (2026-07-13).
