# Dashboard design review + Pipeline Console tracer bullet — 2026-07-15

Scope: acted as operator and developer against the frontend monitoring/control surfaces. Brought each surface up against a synthetic H17-like data tree (4 dates spanning CLEAN / DEGRADED / in-flight states), exercised every endpoint, vetted the UI in a real browser, and closed the coverage gaps with a new unified console. All screenshots in this directory are real renders of the running code, not mockups.

## What was tested

The synthetic tree mirrored production layouts exactly: `{date}T{ts}_sb{NN}.hdf5` incoming groups, `.ms` dirs, `{date}T22:26:05_0~23.{b,g}` cal tables, tile FITS + `.tile_checkpoint.json` (with quarantine-threshold failures), `{date}T{HH}00_mosaic.fits` + `.weights.fits` companions, forced-phot CSVs, `{date}_manifest.json`, `{date}_run_summary.json`, `run_report.md`, and run logs.

## Findings on the existing surfaces

`scripts/qa_server.py` (live, port 8767) works as a wall display but not as an operations console. The epoch list is hardcoded (`EPOCHS`, 6 entries) so new dates never appear — the in-flight 2026-07-14 date was invisible. `STAGE` is a hardcoded path (had to monkeypatch to test off-H17; `PRODUCTS` already honors `DSA110_PRODUCTS_BASE`). Every page load re-opens every mosaic FITS synchronously to compute peak/RMS — expensive against real 4800×4800 mosaics, and uncached. It exposes no calibration QA, no checkpoint/quarantine state, no manifests/gates, no logs, and no control. The thumbnail route is traversal-safe (verified) and thumbnail caching is correct.

`scripts/monitor_server.py` (live, port 8765) is JSON-only with no UI. Paths are hardcoded; `/logs` globs a fixed list; `/exec` correctly fails closed when `TIDY_EXEC_SECRET` is unset (verified 403), but it remains a raw shell hook — the security-relevant surface flagged in the campaign plan (#62).

`dsa110_continuum/templates/pipeline_dashboard.html` is orphaned and broken: nothing references it, it targets `/api/v1/...` endpoints that do not exist in this repo, and it has unescaped single braces (`}` at the `refreshTasks`/`refreshFeatures` catch blocks) that make `render_template()` raise `ValueError` if anyone ever renders it. Recommend deleting it together with `dev_portal.html` (also orphaned) as part of #62, rather than patching.

`dsa110_continuum/mosaic/api.py` is dormant (never mounted) and its `DELETE /{name}` (with optional file deletion) has no auth — do not mount it as-is.

One latent non-dashboard finding: `batch_pipeline.py --dry-run` is documented as read-only, but `_dry_run_main` imports `mosaic_day` → `imaging/cli_imaging.py`, which runs `casa_runtime()` at import time and `mkdir`s the CASA log dir — an import-time side effect that breaks dry-run on hosts without `/data` (workaround: `CASA_LOG_DIR=...`). Same class of issue as the import-time invariants in CLAUDE.md; worth a small follow-up issue.

## Coverage gaps (before) and how they were closed

| Stage | Before | Now (Pipeline Console) |
| --- | --- | --- |
| Ingest (HDF5) | static Pages site only | per-date subband-group completeness (n/16) |
| Conversion (MS) | count by date | per-date MS list, latest timestamp |
| Calibration | "b-table exists" badge | BP + G tables per date, missing/partial flags |
| Imaging (tiles) | dir sizes only | tile counts, checkpoint state, failure table with consecutive-failure counts and quarantine risk |
| Mosaicking | 6 hardcoded epochs | auto-discovered epochs, thumbnails, peak/RMS/DR (mtime-cached), weight-map presence |
| QA | none | manifest verdict per date, per-epoch `qa_result`, triggered gates with reasons |
| Photometry/science | median ratio of one CSV | per-CSV source counts, bright counts, median ratio with pass-band coloring |
| Diagnostics | latest log only | artifact viewer: logs, manifests, run reports, FITS headers, CSV heads — path-traversal guarded |
| Control | raw `/exec` shell | structured, token-gated: `batch_pipeline.py` launch (dry-run default, confirm on real runs), quarantine clear, job registry with live log tail and kill |

## The new console

`scripts/dashboard_server.py` (port 8766) + `tests/test_dashboard_server.py` (11 tests, all passing; ruff-clean). Single file, no build step, dark operator theme. It is a tracer bullet for issues #51–#61: routed server (#51), stage state to browser card (#52), artifact browser and QA views (#53–#60), mutating pipeline-on-demand routes (#61). It does not touch the two live services, per the Phase-3 security rule.

Auth posture (relevant to ADR #49): read-only routes open; mutating routes require `X-DSA110-Token` == `DSA110_DASH_TOKEN`; token unset ⇒ control disabled server-side and greyed out in the UI (fail closed, verified by test). Launches are argv-list subprocess calls — no shell, date/hour inputs validated (`2026-01-25; rm -rf /` is rejected, verified by test). Artifact reads are confined to the configured pipeline roots (verified by test).

Verified end-to-end in this session: coverage matrix aggregation across all four dates; degraded-date view surfacing the WSClean-timeout failure, `catalog_completeness` FAIL gate, and 0.555 ratio in red; clear-quarantine zeroing the checkpoint failure count (visible on next matrix refresh); a real `batch_pipeline.py --dry-run 2026-01-25 22–23` launched through the control API, completing rc=0 with the full dry-run plan captured in the job log; token-missing and wrong-token 403s; UI interaction (date drill-down, artifact viewer, error toast) in headless Chromium with zero console errors.

## Deploy on H17

```bash
PYTHONPATH=/data/dsa110-continuum DSA110_DASH_TOKEN=<secret> \
  /opt/miniforge/envs/casa6/bin/python \
  /data/dsa110-continuum/scripts/dashboard_server.py   # port 8766
```

Defaults target H17 paths; no env needed besides the token. `CASA_LOG_DIR` is inherited by launched jobs if set.

## Recommended follow-ups

Retire `pipeline_dashboard.html` + `dev_portal.html` (orphaned/broken) and fold `monitor_server.py`'s remaining value (`/disk`, `/processes`, `/logs` are all superseded) into #62. Write ADR #48/#49 using the console's auth posture as the strawman. Once trusted, deprecate the hardcoded `EPOCHS` list in `qa_server.py` or point it at `/api/dates`. Consider a small follow-up issue for the `--dry-run` import-time side effect noted above.

## Design pass (2026-07-15, second iteration)

The UI was reworked for a minimal, information-dense presentation: borderless typographic layout (micro-label section headers, hairline dividers instead of panels), tabular-num monospace numerics with zeros dimmed so anomalies pop, a 7-segment per-date stage glyph (ingest → conversion → calibration → imaging → mosaic → QA → photometry; blue = done, green = QA clean, amber = partial/failures, red = quarantine/fail, dim = pending) for one-glance triage, thin disk meters in a single header strip, dot-based status language throughout, mosaic cards keyed by epoch hour with peak/RMS/DR/weights inline, and artifact chips. Verified interactively in headless Chromium after the redesign: date drill-down, artifact viewer, error toast, zero page errors; all 11 API tests unchanged and passing.

## Three-page structure (2026-07-15, third iteration)

The console is now three pages behind one server with shared nav: `/` telescope status, `/pipeline` (the coverage + control console, unchanged), `/science` (product gallery).

**Home (`/`)** centres on telescope state for a drift-scan array: a full-sky RA×Dec map with a client-side live meridian (pure LST arithmetic, updates every second with no polling), the Dec strip band, and the highlighted strip×meridian intersection — the tile being observed right now. Overlaid sources come from `/api/sky`: a built-in bright-sky list (A-team: Cas A, Cyg A, Tau A, Vir A, Her A, Hyd A, Pic A, For A; flux standards 3C48/138/147/286/295 and 0834+555; other prominent 3C sources), the Sun via astropy ephemeris, and flux-limited (≥2 Jy) rows from the master-catalog / VLA-calibrator SQLite DBs when present (`DSA110_MASTER_CAT_DB`, `DSA110_VLA_CAL_DB`; schema sniffed, best-effort). A "transiting now / next two hours" chip row lists what's in the strip. Antenna liveness is a 110-chip grid (green/amber/red/grey) fed by `/api/antennas`: an ops-writable JSON file (`DSA110_ANT_STATUS_JSON`) takes precedence; otherwise per-antenna solution flags from the newest bandpass table (via the CASA table adapter, H17 only); otherwise all-grey. Every live element carries its data age, and the freshness tile turns amber past 3 h, red past 24 h — the page never pretends stale state is live.

**Science (`/science`)** is a newest-first product feed: per-date verdict, epoch-mosaic cards with QA badge and click-to-zoom lightbox, and per-epoch forced-photometry tables with pass-band-colored median ratios.

Screenshots `ui3_home.png`, `ui3_pipeline.png`, `ui3_science.png` are live renders of the running server (not static snapshots). 16 tests pass (5 new: sky state, antenna JSON precedence + padding, no-source fallback, science feed, three-page serving); ruff clean; zero browser page errors on all three pages.

## Fourth iteration (2026-07-15): antenna layout, variability, launch tooling

Antenna geographic layout: `/api/antennas` now merges positions from an antpos CSV (`DSA110_ANTPOS_CSV`, default glob `/data/dsa110-antpos/ant_ids*.csv`) — tolerant of x/y-metres, east/north, or lat/lon columns (lat/lon projected to local east-north about the centroid), and of name forms like `DSA-001`/`1.0`. When ≥3 antennas have positions, Home renders a geographic scatter (status-colored, hover shows name/status/flag-fraction/offsets); otherwise it falls back to the chip grid.

Variability: `/api/variability` stacks every `*_forced_phot.csv` across product dates into per-source lightcurves and ranks by η using the **canonical** formulas imported from `dsa110_continuum.photometry.metrics` (`canonical_metrics: true` confirmed at runtime; local equivalents only if the package is unimportable). `/api/lightcurve/{source}` returns one source's series. The Science page opens with the η-ranked table (sparklines inline) next to a click-to-focus lightcurve panel with error bars. Demo validation: a planted ESE-like dip (NVSS_0007, ×0.4 on one epoch) ranks first at η=172 vs 77 for the next source, and its dip is visible in the lightcurve panel.

Launch tooling: `scripts/run_dashboard.sh` — resolves the casa6 python, generates/persists a 0600 control token, sets `PYTHONPATH`/`CASA_LOG_DIR`, runs foreground (`--fg`) or nohup-background with timestamped logs. H17 bring-up is now:

```bash
cd /data/dsa110-continuum && ./scripts/run_dashboard.sh
# → http://lxd110h17:8766/  (telescope · /pipeline · /science)
```

Optional env on H17: `DSA110_ANT_STATUS_JSON` (ops antenna feed), `DSA110_ANTPOS_CSV`, `DSA110_MASTER_CAT_DB`, `DSA110_VLA_CAL_DB`. 20 tests pass (4 new: antpos x/y + lat/lon parsing, variability stacking/ranking, lightcurve endpoint); ruff clean; zero page errors. Screenshots `ui4_*.png`.

## Files

- `console_snapshot.html` — self-contained interactive snapshot (captured API data baked in; ~3 MB, session artifact, kept out of git)
- `ui2_full.png` / `ui2_degraded.png` / `ui2_inflight.png` — redesigned UI: full console, DEGRADED date, in-flight date
- `ui_full.png`, `ui_degraded.png`, `ui_inflight.png`, `ui_viewer.png` — first-iteration design (superseded)
