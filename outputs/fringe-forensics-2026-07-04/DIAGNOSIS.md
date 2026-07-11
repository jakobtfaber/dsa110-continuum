# Crosshatch / herringbone fringe forensics — DSA-110 continuum tiles

Read-only investigation, 2026-07-04. Data on `h17`. No MS, FITS, cal table, or
pipeline state was modified. Figures + JSON: `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/`.

## TL;DR

The crosshatch is **not** a deconvolution artifact and **not** confined to a few
tiles — it is present in *every* tile of both epochs, at the *same* spatial-frequency
lattice, with only its amplitude varying. It has two coupled root causes, both
upstream of imaging:

1. **The imaging array is depleted to ~62 of ~96 usable antennas** because the
   bandpass/gain solve flags 30% of antennas (34, the *identical* list in both
   epochs). This gives a snapshot dirty beam with a strong regular sidelobe lattice
   at |uv| ≈ 1.0-1.35 kλ (215-290 m baselines). This lattice is the herringbone.
2. **RFI flagging never ran** (FLAG = 0.0000 in every imaged MS; no AOFlagger/MAD
   flag versions exist). Strong unflagged narrowband + satellite RFI, plus the
   12.5 Jy calibrator 3C454.3, inject large amounts of non-deconvolvable flux that
   convolves with the bad PSF and stamps the lattice across the whole field.

Cause (1) sets the pattern (frequency/orientation); cause (2) sets its amplitude
(how visible/bad it is). Deconvolution cannot remove it because it is a
visibility-domain corruption, not a real compact sky source (residual ≈ dirty).

## 1. Which tiles are bad (per-tile ranking)

Epoch `mosaic_2026-01-25` hour 22 (11 tiles, the flagged `2026-01-25T2200_mosaic.fits`).
Tiles ordered by RA (= drift time; low RA = west = mosaic left). Missing 22:31 tile
→ the RA gap 343.8°→346.7° is the mosaic vertical seam at x≈5600.

| tile (UTC) | RA° | central RMS (mJy) | fringe band-power | verdict |
|---|---|---|---|---|
| 22:00:18 | 337.3 | 4.75 | 1.6e2 | acceptable |
| 22:05:28 | 338.6 | 4.33 | 1.3e2 | acceptable (cleanest) |
| 22:10:37 | 339.9 | 4.93 | 1.5e2 | acceptable |
| 22:15:46 | 341.2 | 8.35 | 4.4e2 | fringed |
| 22:20:55 | 342.5 | 9.42 | 4.6e2 | fringed |
| 22:26:05 | 343.8 | 8.44 | 5.3e2 | fringed (3C454.3 transit tile) |
| 22:36:08 | 346.7 | 8.77 | 4.1e2 | fringed |
| 22:43:02 | 348.0 | 5.24 | 1.5e2 | acceptable |
| 22:48:11 | 349.3 | 5.25 | 2.3e2 | acceptable |
| **22:53:20** | 350.6 | **21.75** | **3.2e3** | BAD (RFI) |
| **22:58:30** | 351.9 | **54.17** | **2.0e4** | BAD (RFI) |

Epoch `mosaic_2026-02-26` hour 00 (10 tiles) is worse throughout — RMS 8-282 mJy.
`00:09:58` (RMS **282 mJy**, RA 40.2°) is catastrophic and its sharp left/right
amplitude discontinuity is the mosaic seam band at x≈7600.

Important: the amplitude ranking (RMS) and the visual "worst on the mosaic's
western third" disagree because MAD-RMS is nearly blind to a *coherent low-amplitude*
fringe. The westernmost 0125 tiles (22:00-22:10) have the best RMS yet still carry the
full lattice; the mosaic's west looks worst because 6 low-RA tiles overlap there and
their coherent (same-lattice) fringes reinforce under coaddition while pb-correction
amplifies tile-edge noise. The high-RMS east tiles (22:53/22:58) are down-weighted in
the inverse-variance coadd, so they do *not* dominate the mosaic despite being worst
per-tile.

Figures: `montage_0125_2200group.png`, `montage_0226_epoch.png`,
`qa_metric_bars.png`, `scan_mosaic_2026-01-25_T22.json`, `qa_metric_all.json`.

## 2. Fringe frequency, orientation, implied baseline

2-D FFT of flat-noise `-image` sub-regions (central 1400 px, Hanning-windowed,
8σ source-clipped). Worst tile 22:58:30, λ = 0.2134 m, cell = 6.0″. |uv|[λ] =
57.30 × f[cyc/deg]; baseline[m] = |uv| × λ. The power spectrum is a *discrete
lattice* (not a smooth source response), i.e. the corruption sits on a few specific
uv cells / baselines. Dominant families:

| family | freq (cyc/deg) | period (arcmin) | orientation | \|uv\| (λ) | baseline (m) |
|---|---|---|---|---|---|
| E-W stripes (vertical) | u=±17.6, v=0 | 3.41 | 0° (E-W) | 1007 | **215** |
| N-S stripes (horizontal) | u=0, v=±23.6 | 2.55 | 90° (N-S) | 1351 | **288** |
| diagonal | (±11.6, ∓14.6) | 3.22 | ±52° | 1066 | **227** |

The v=0 (E-W) ridge extends to ±60 cyc/deg (→ ~730 m). **All families sit at
|uv| ≈ 1.0-1.35 kλ (215-290 m) — right at the `uvrange>1klambda` (213 m) short-baseline
cutoff.** The crosshatch is the snapshot dirty-beam sidelobe structure dominated by
the shortest *retained* baselines of the depleted array. The multiple baseline
orientations (E-W, N-S, diagonal) are what read visually as "herringbone/crosshatch."

Figures: `fft_0125_clean_vs_worst.png` (clean 22:00 vs 22:20 vs worst 22:58 — same
lattice, growing amplitude), `pdir_2026-01-25_225830.png`, `pdir_2026-01-25_220018.png`.

## 3. Dirty vs image vs residual — verdict: VISIBILITY-DOMAIN, not deconvolution

Robust RMS (central 600 px) and fringe/noise power ratio, worst tile 22:58:30:

| product | RMS (mJy) | fringe/noise |
|---|---|---|
| psf | 2.43 | 18.4 (same lattice as image!) |
| dirty | 46.54 | 9.7 |
| image | 44.73 | 9.5 |
| residual | 44.69 | 9.4 |

`dirty ≈ image ≈ residual` — deconvolution removed essentially nothing (46.5→44.7 mJy,
~4%), and the fringe power is identical in all three. **The fringe is in the
visibilities and is not deconvolvable.** By contrast the clean tile 22:00:18 shows real
cleaning (dirty 6.09 → residual 3.65 mJy, ~40%). The PSF itself carries the same
lattice (fringe/noise 18.4), confirming the *frequencies* are set by the snapshot uv
sampling; the *image amplitude* is set by how much uncleaned (RFI + bright-source) flux
is present. This rules out "shallow deconvolution of a real source" as the primary
cause — that would show dirty ≫ residual.

## 4. Flagging & calibration evidence

### Flagging: absent
- Base `.ms` FLAG fraction = **0.0000** (both epochs). Docs (`docs/reference/flagging.md`)
  say production should reach ~2.44% after the two-stage contract.
- `*_meridian.ms.flagversions/FLAG_VERSION_LIST` contains only `applycal_1..9`
  autosaves — **no AOFlagger / SumThreshold / 7σ-MAD / rflag / flagdata version exists.**
- `scripts/batch_pipeline.py` never calls `flag_rfi_aoflagger` / `flag_residual_rfi_clip`
  / `flag_extend` (the functions exist and work: `dsa110_continuum/calibration/flagging_rfi.py:214`,
  `flagging_amplitude.py:111`, `flagging_rfi.py:434`; two-stage orchestration at
  `flagging_rfi.py:89-172`). The only flags present are applycal's (which flag antennas
  that lack a gain solution).

### Unflagged RFI drives the extreme tiles
Raw per-SPW / per-channel / per-timestep amplitude (16 SPW × 48 ch, 24 integrations):
- `2026-01-25T22:58:30`: SPW 15 (1487-1499 MHz) at 5.7× median, SPW 14 (1475-1487 MHz)
  at 2.6×; channels 27-34 of SPW 15 (**~1493-1495 MHz**) at ~12.5× median. Per-timestep
  amplitude ramps 1.72→0.61 with a re-brightening at step 20 — a **satellite pass**
  signature (LEO/Starlink is the dominant OVRO RFI per the docs).
- `2026-02-26T00:09:58`: SPW 5 (1370-1381 MHz) at 4.1×, channels 43-47 (**~1379 MHz**)
  at 25-43× median; bright transient in timesteps 0-3 (2-2.5×) → the sharp tile-edge
  discontinuity that becomes the mosaic seam.
- Clean tile `22:00:18` is flat in both time and frequency (no SPW spike, no ramp).

### Calibration yield: 30% of antennas dropped, same list every epoch
Gain `.g` flag fraction **30.6%**, bandpass `.b` **27.7%**, both with the **identical
34-antenna** flagged list in 0125 and 0226:
`[9,18,20,21,22,47,48,51-66,70,72,81,88,90,92,93,98,100,108,116]`.
Cross-checking against raw data (cal MS 22:26:05):
- ~21 are genuinely absent from the correlator output (the whole 51-66 block, 9,20,21,22,116).
- 2 are dead (18, 108 at ~0.01-0.02× median).
- **~11 antennas have perfectly normal raw amplitude (0.9-1.2× median) yet were flagged
  in the solve**: 72, 81, 88, 90, 92, 93, 98 (full), and 47, 48, 70, 100 (partial).
  These are good antennas thrown away by the bandpass/gain solver (likely refant or
  `minsnr=5.0`; `runner.py:1216`, and `bandpass_diagnostics.py:656-658` flags exactly
  this "bad_antenna_or_refant" mode with fix "change_refant/flag_bad_antennas").

The `.g`/`.b` are single-time (static) solutions applied across the whole ~1 h block,
anchored on 3C454.3 (12.5 Jy) transit at 22:26 — no time dependence, so the mid-block
tiles near the calibrator passage (22:15-22:36) show elevated residual sidelobes.

## Root-cause ranking (with evidence)

1. **Calibration flags 30% of antennas → sparse array → strong-sidelobe snapshot PSF
   (SETS the crosshatch).** Evidence: PSF carries the exact lattice (fringe/noise 18.4);
   fringe families all at 1.0-1.35 kλ = shortest retained baselines; the *same* 34-antenna
   flag list both epochs; ~11 of them have good raw data (solver yield problem, not dead
   array). This is why *every* tile is fringed and why deconvolution can't help.
2. **RFI flagging absent → unflagged narrowband/satellite RFI injects non-deconvolvable
   flux (SETS the amplitude / worst tiles).** Evidence: FLAG=0, no RFI flag versions,
   pipeline never calls the flag functions, SPW-localized 12-43× amplitude spikes and
   satellite time-ramps exactly on the highest-RMS tiles.
3. **3C454.3 (12.5 Jy) residual + static single-time cal** — secondary; elevates tiles
   near the calibrator's field passage (22:15-22:36).
4. **Not** a deconvolution-depth problem (residual ≈ dirty) and **not** a few isolated
   bad tiles.

## Concrete pipeline stages to fix

1. **Insert the two-stage RFI flagging into the production imaging/calibration path**
   (currently never invoked). Call `flag_rfi_aoflagger` → `flag_residual_rfi_clip(sigma=7)`
   → `flag_extend` on each MS *before* applycal + imaging (functions ready in
   `dsa110_continuum/calibration/flagging_rfi.py` / `flagging_amplitude.py`; canonical
   order in `docs/reference/flagging.md`). Biggest, cheapest amplitude win — removes the
   22:53/22:58 and 00:09:58 class of blow-ups.
2. **Fix calibration yield in the bandpass/gain solve** (`dsa110_continuum/calibration/runner.py:1206-1216`,
   `minsnr=5.0`). Recover the ~11 good antennas being dropped: try an alternate refant
   (`bandpass_diagnostics.py:784` suggests 104/105), inspect why 72/81/88/90/92/93/98 fail
   the solve. More antennas → denser uv → lower sidelobes → weaker intrinsic crosshatch.
3. Do **not** simply deepen CLEAN — the corruption is in the visibilities and is not
   deconvolvable; deeper cleaning won't touch it until (1) and (2) are fixed.

## QA metric proposal

Two complementary throwaway metrics (prototyped in `/tmp/final_qa.py`, `/tmp/psf_test.py`
on h17; not committed), computed on the `-image` central 700 px, Hanning-windowed,
8σ source-clipped 2-D power spectrum:

- **HERR anisotropy** = 99.5th-percentile / median power in the off-axis annulus
  12-45 cyc/deg. Scale-free; white noise ≈ 5, a discrete fringe lattice ≫ 1000. In
  this dataset *every* tile scores 700-370000 → correctly flags the whole run as
  fringe-affected (the artifact is systemic).
- **central robust RMS** (mJy, MAD-based, `-image-pb` central 600 px) — carries amplitude.

Per-tile values are in `qa_metric_all.json`. Suggested operational gate for use *after*
the flagging+cal fixes (to catch residual bad tiles):

- REJECT tile if `central RMS > 8 mJy` **or** `HERR anisotropy > 5000`.
  On 0125 this cleanly separates the acceptable tiles (22:00/22:05/22:10/22:43/22:48,
  RMS 4.3-5.3, band-power 1.3-2.3e2) from the fringed majority (RMS 8-54, band-power
  4-200e2). On 0226 it flags the whole epoch, correctly.
- As a coherent-fringe *presence* alarm on the current (unfixed) data, HERR anisotropy
  > 2000 fires on all tiles — the honest reading is that both epochs are unusable for
  compact-source variability until the upstream flagging+cal are corrected.

## Saved artifacts (all on h17)

- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/montage_0125_2200group.png`
- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/montage_0226_epoch.png`
- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/fft_0125_clean_vs_worst.png`
- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/pdir_2026-01-25_225830.png`
- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/pdir_2026-01-25_220018.png`
- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/qa_metric_bars.png`
- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/scan_mosaic_2026-01-25_T22.json`
- `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/qa_metric_all.json`
