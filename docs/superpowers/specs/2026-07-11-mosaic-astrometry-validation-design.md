# Mosaic astrometry validation — design

Date: 2026-07-11  
Target product: `/stage/dsa110-contimg/images/mosaic_2026-01-25/2026-01-25T2200_mosaic.fits`  
Status: approved for implementation planning

## Goal

Validate that known radio sources are correctly positioned on the
2026-01-25T2200 hourly-epoch mosaic: measure ΔRA / ΔDec against external
catalogs, gate on RMS relative to the synthesized beam, and produce plots
under `outputs/`. Phase 1 is a diagnostic script; Phase 2 hardens the method
into the package and addresses the broken master catalog.

## Decisions (locked)

| Topic | Choice |
| --- | --- |
| Deliverable | Diagnose this mosaic first, then harden |
| Reference truth | NVSS + FIRST + RACS (RAX), queried separately |
| Master catalog | **Not used** in Phase 1 — see Finding below |
| Position measurement | Catalog-seeded peak (primary) + blind cross-match (secondary) |
| PASS gate | Seeded RMS ≤ √(BMAJ × BMIN) / 5 |
| Implementation shape | Analysis script under `scripts/`; promote to `qa/` later |

### Finding: master catalog is VLASS-only

`/data/dsa110-contimg/state/catalogs/master_sources.sqlite3` records NVSS,
FIRST, RAX, and VLASS as build inputs, but `sources` and `catalog_matches`
are 100% VLASS (`has_nvss` / `has_first` / `has_rax` all zero). Forced
photometry “master” positions are therefore VLASS-only today. Rebuild is
explicitly a Phase 2 / follow-up item, not a Phase 1 blocker.

### Beam context

This mosaic (and its input tiles) have restoring beam
**BMAJ ≈ 58.8″**, **BMIN ≈ 33.1″**, pixel scale **6″**. The glossary “3″”
figure is tile *cell* size in some configs, not the synthesized beam.
Threshold for this mosaic: √(58.8 × 33.1) / 5 ≈ **8.8″**.

## Phase 1 — diagnose

### Entry point

```bash
/opt/miniforge/envs/casa6/bin/python scripts/validate_mosaic_astrometry.py \
  --mosaic /stage/dsa110-contimg/images/mosaic_2026-01-25/2026-01-25T2200_mosaic.fits \
  --out-dir /data/dsa110-continuum/outputs/astrometry-2026-01-25 \
  [--min-flux-mjy 50] [--surveys nvss,first,rax]
```

Use `PYTHONPATH=/data/dsa110-continuum` (or `/workspace` in that context).

### Catalog query

- Footprint from mosaic valid-pixel bounding box, padded by **one BMAJ**
  on each side (degrees).
- Min flux: **50 mJy** (matches epoch QA / forced-phot default).
- Surveys via existing `dsa110_continuum.catalog.query.cone_search`:
  `nvss`, `first`, `rax` (alias `racs`).
- Keep per-survey results separate (no forced merge).

### Primary: catalog-seeded peak

For each catalog source in the footprint:

1. Extract a cutout with half-width ≈ **1 × BMAJ** (~10 pixels at 6″/px).
2. Estimate local RMS from cutout edge / annulus.
3. Require peak **SNR ≥ 5**; reject peaks on the cutout edge.
4. Position = peak pixel, with cheap sub-pixel parabolic refine when practical.
5. Convert via mosaic WCS; offsets are **DSA − catalog** in arcsec:
   ΔRA·cos(Dec), ΔDec.
6. Total separation = √(ΔRA² + ΔDec²).

### Secondary: blind cross-match

- Run existing source-finding (Aegean/BANE) if available; otherwise skip with
  a clear log line and omit blind CSVs.
- Match detections to each catalog within **beam/2**
  (√(BMAJ×BMIN)/2 ≈ 22″).
- Blind tables are diagnostic only; **PASS/FAIL uses seeded RMS**.

### Gate

- Need ≥ **5** seeded matches for a survey to be scored.
- Per survey: **PASS** if seeded RMS ≤ √(BMAJ×BMIN)/5;
  **WARN** if |mean offset| > 2″ (systematic shift) even when RMS passes.
- Overall: **PASS** only if every *scored* survey PASSes.
  Unscored surveys (too few matches) are reported as **SKIP**, not FAIL.

### Artifacts

Under `/data/dsa110-continuum/outputs/astrometry-2026-01-25/`:

| File | Content |
| --- | --- |
| `seeded_offsets_{nvss,first,rax}.csv` | Per-source offsets, SNR, fluxes |
| `blind_matches_{survey}.csv` | Secondary matches (optional) |
| `summary.json` | n, mean/median/RMS, threshold, verdict per survey |
| `offset_scatter.png` | ΔRA vs ΔDec panels |
| `quiver_sky.png` | Offset vectors on sky (bright subset) |
| `hist_separation.png` | \|offset\| histograms with threshold line |

### Code layout (Phase 1)

- Logic lives in `scripts/validate_mosaic_astrometry.py` (chosen approach A).
- Reuse `catalog.query.cone_search` and the existing source-finding entry
  point when available.
- No new package API or unit tests required in Phase 1.

### Explicitly out of scope (Phase 1)

- Rebuilding `master_sources.sqlite3`
- Wiring into `batch_pipeline` / epoch QA
- Applying a WCS correction or rewriting the mosaic FITS

## Phase 2 — harden (follow-up)

1. Promote measurement helpers into `dsa110_continuum/qa/astrometry.py`.
2. Replace stub `mosaic.qa.check_astrometry` (currently estimates RMS from
   catalog type without measuring offsets).
3. Optionally add an astrometry panel to epoch QA plots.
4. Rebuild master catalog so NVSS/FIRST/RAX flags and fluxes are populated;
   re-validate that forced photometry “master” matches multi-survey truth.
5. Add unit tests with synthetic cutouts / WCS (no real FITS required).

## Error handling

- Missing mosaic / unreadable WCS → exit non-zero with clear message.
- Empty catalog query for a survey → SKIP that survey in summary.
- Aegean unavailable → skip blind path; seeded path still runs.
- All surveys SKIP → overall FAIL (cannot validate).

## Success criteria

Phase 1 succeeds when:

1. The script runs on `2026-01-25T2200_mosaic.fits` end-to-end.
2. `summary.json` reports per-survey seeded stats and a clear overall verdict.
3. Offset plots are written under `outputs/astrometry-2026-01-25/`.
4. Master-catalog gap is documented in the summary or run log (not silently
   ignored as if master were usable).
