# RFI rebuild vs PR1 baseline — 2026-01-25T2200 mosaic quality comparison

**Written:** 2026-07-10 (session continuing handoff-2026-07-10-14-25)

Old = `pr1-validation/2026-01-25T2200_mosaic.fits` (sha256 91d2b03e…, gate
`pr1-quality-gate-2026-07-10/2026-01-25T2200_quality_gate.json`).
New = `/stage/dsa110-contimg/images/mosaic_2026-01-25/2026-01-25T2200_mosaic.fits`
(sha256 1ebd0713…, gate `rfi-rebuild-2026-07-10/2026-01-25T2200_quality_gate_rfi-rebuild.json`).
Both gates evaluated with the identical `dsa110-mosaic-quality-gate/v1` method
(`quality_gate_v1.py`, recovered verbatim from the 2026-07-10 morning session).

| Metric | Old (pre-RFI) | New (RFI-cleaned) | Threshold | New verdict |
| --- | --- | --- | --- | --- |
| Global robust RMS (mJy/beam) | 8.921 | 5.096 | — | −43% |
| Central component RMS (mJy/beam) | 11.492 | 8.980 | ≤ 8.0 | **FAIL** (was FAIL) |
| HERR anisotropy peak/median | 34.30 | 16.27 | ≤ 5000 | PASS (−53%) |
| Edge RMS (mJy/beam) | 19.820 | 10.806 | — | −45% |
| Interior RMS (mJy/beam) | 7.963 | 4.591 | — | −42% |
| Edge/interior RMS ratio | 2.489 | 2.354 | ≤ 2.0 | **FAIL** (was FAIL) |
| Positive weight fraction | 0.6687 | 0.6687 | ≥ 0.5 | PASS (identical footprint) |
| Effective noise median (mJy/beam) | 11.584 | 6.713 | — | −42% |
| Effective noise p95 (mJy/beam) | 347.5 | 324.2 | — | −7% |
| Peak (Jy/beam) | 16.240 | 16.189 | — | −0.3% (flux preserved) |
| Epoch QA completeness | — | 44.9% (172/383) | ≥ 60% | **FAIL** |

## Visual comparison

- New full render: `2026-01-25T2200_mosaic_rfi-rebuild_full.png` (+ provenance
  sidecar). Old render: `pr1-validation/2026-01-25T2200_mosaic_pr1-rebuild_full.png`.
- The crosshatch/PSF-lattice texture is still clearly visible across the whole
  strip in the new render, at visibly lower amplitude. Geometry unchanged —
  consistent with the calibration-yield diagnosis (~30% antenna loss), not RFI.
- The central coverage pinch (missing 22:31 tile) persists; weight map confirms
  near-zero weight in the pinch. Not repairable by coadd weighting.

## Verdict

**Not science-ready.** RFI flagging delivered a large, uniform amplitude
improvement (~40-45% RMS reduction everywhere; extreme-percentile amplitude
−80%; bright-source flux preserved) but did not change failure topology:
central RMS 8.98 > 8.0, edge/interior 2.35 > 2.0, completeness 44.9% < 60%.
Remaining work: calibration antenna yield (refants 104/105), completeness
investigation, missing-tile coverage.
