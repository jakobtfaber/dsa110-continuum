# Completeness 44.9% FAIL — investigation and resolution (2026-07-10)

Diagnostic: `completeness_diag.py` (this directory), run against the RFI-cleaned
`/stage/dsa110-contimg/images/mosaic_2026-01-25/2026-01-25T2200_mosaic.fits`
using the same NVSS query and recovery test as
`dsa110_continuum/photometry/epoch_qa.py::measure_epoch_qa`.

## Breakdown of the 383 "expected" sources

| Category | N | Share |
| --- | --- | --- |
| Recovered (peak > 5σ local RMS) | 171 | 44.6% |
| On valid pixels, not recovered | 70 | 18.3% |
| On blank (NaN) pixels — never observed | 120 | 31.3% |
| Outside the pixel grid entirely | 22 | 5.7% |

The footprint query is a rectangular RA/Dec bounding box
(RA [334.4°, 354.8°], Dec [13.6°, 18.4°]) but the mosaic support is a rounded
strip covering only 66.9% of that box (positive_weight_fraction from the v1
gate). 142/383 = 37% of the catalog could never be recovered at any image
quality. This is a measurement bias, not an image deficiency.

- **Coverage-corrected completeness: 171/241 = 71.0% — above the 60% threshold.**
- Raw (biased) completeness: 171/383 = 44.6% (gate reported 44.9%; the ±1
  source difference comes from center-pixel finiteness vs 3×3 peak-box edge
  cases).

## The 70 genuine misses

- Concentrated at near-zero relative weight: w/wmax ≤ 0.04 for most of the
  top misses (tile edges, overlap-poor zones, pinch shoulders).
- Elevated local RMS at those positions (15–100 mJy vs 5.1 mJy global) —
  the calibration-lattice + edge-noise problem, consistent with
  edge/interior RMS ratio 2.35.
- Miss SNR distribution: median 3.3; 24/70 sit in 4–5σ (near-threshold).
- Flux distribution of misses: median 76 mJy (near the 50 mJy catalog cut).

## Verification of the other two hypotheses

- Local-noise calculation (`_local_rms` annular box): behaves correctly on
  valid pixels; falls back to global RMS only when <10 finite pixels.
- Source-presence test (`_peak_in_box`, 3×3): behaves correctly; returns 0.0
  on all-NaN boxes, which silently converted unobserved sources into misses.

## Resolution (code change, uncommitted, in the Mac worktree)

`dsa110_continuum/photometry/epoch_qa.py`: sources landing on non-finite
pixels are now excluded from the completeness denominator (`n_covered` added
to `EpochQAResult`; gate SKIPs if `n_covered < 5`). The 60% threshold is
UNCHANGED — this corrects the measurement, not the operating point.
`epoch_qa_plot.py` shows recovered/covered. Tests:
`tests/test_epoch_qa.py::TestCoverageAwareCompleteness` (3 new; 18 total pass).

Under the fixed gate this epoch scores: ratio 0.88 PASS, completeness 71%
PASS, RMS 5.1 PASS → epoch QA verdict would flip to PASS. The stricter
`dsa110-mosaic-quality-gate/v1` science gate still FAILS (central RMS 8.98 >
8.0, edge/interior 2.35 > 2.0) — overall status remains **not science-ready**.
