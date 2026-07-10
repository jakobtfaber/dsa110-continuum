# Handoff: Sault-weighted coadd + crosshatch forensics

**Created:** 2026-07-04 08:19 (local)
**Branch:** `agent/sault-weighted-coadd` @ `72d963b` (LOCAL ONLY — never pushed; 1 commit ahead of `main` @ `57821b5`)
**Working tree:** clean
**Current phase:** Implement (mid-work-item) → next is a small follow-up commit, then PR, then Validate

## Context: how we got here

`main` now contains the fully merged dsa110_contimg import retirement (PR #93
`baef485`, validation addendum PR #94 `57821b5`; see
[validation-contimg-import-retirement.md](validation-contimg-import-retirement.md)).
After merge, the user pivoted to mosaic quality. Visual inspection of two
production hourly-epoch mosaics found: (a) edge dipole artifacts, (b) a
crosshatch/herringbone fringe pattern, (c) a missing-tile coverage pinch. Two
work items were authorized: **implement beam-validated per-pixel Sault
weighting in the production coadd** (this branch) and **root-cause the
crosshatch** (done, investigation only — fixes NOT yet implemented).

## Work item A — Sault coadd (this branch, one commit `72d963b`)

**Code:** `dsa110_continuum/mosaic/production.py` — `coadd_tiles` rewritten:
per-pixel weight `(1/sigma_flat^2) * PB^2` for PB-corrected inputs; PB
recovered as the `image/image-pb` ratio (fallback: band-average of
`-beam-N.fits`; last resort uniform + warning); `PB_CUTOFF=0.2` is now a
weight floor; numerator and weight planes reprojected identically. Helper
`_beam_path_for_tile` was deleted (no external references), `_pb_map_for_tile`
+ `_tile_base` added.

**Tests:** `tests/test_mosaic_sault_coadd.py` (7 tests: exact PB-ratio
recovery, constant-sky flux preservation = double-correction oracle,
overlap-pixel formula check that recomputes the coadd's own weight definition,
floor semantics ×2, band-average fallback, no-PB fallback). All 57 mosaic
tests + 122 mosaic/batch subset pass on Mac py312. Legacy blanking pin
(`tests/test_mosaic_production_coadd.py`) passes unchanged.

**H17 validation (all artifacts in
`/data/dsa110-continuum/outputs/mosaic-visual-qa-2026-07-03/` on h17):**
- Beam check: `image/image-pb` ratio is **pixel-identical** to `-beam-0.fits`
  on production tiles (`pb_ratio_vs_beam0.png`; widths equal at PB=0.9/0.7/0.5/0.3).
  The applied beam is strongly asymmetric (drift-averaged) — model-free ratio
  weighting is the right call. (An earlier "beam-0 is ~19% too wide" claim was
  measured false and has been corrected in docstring + commit message.)
- Epoch 2026-01-25T2200 rebuilt from same 11 tiles
  (`2026-01-25T2200_mosaic_sault.fits`, `_sault_full/zoom.png`): 3C454.3 peak
  16.28→16.24 Jy/beam (flux preserved), rms 26.6→24.9 mJy.
- Epoch 2026-02-26T0000 rebuilt (`ALL DONE` in `/tmp/sault-validate.log` on
  h17): rms 24.3→23.3 mJy, **but the 5.77 Jy edge dipole survived
  bit-identical** — it is a SINGLE-COVERAGE boundary pixel; Sault weighting
  only reallocates weight between overlapping tiles (num/den ≡ pixel value
  where one tile covers). Honest negative, already explained to user.
- NVSS cross-match (`scripts/verify_sources.py`): old vs new at ≥100 mJy →
  median_ratio 0.929 vs 0.880, BUT bright-end (0.5–20 Jy) 1.187→0.962 and all
  big outliers (2.2×, 2.6×, 8.2×) moved toward unity — the new estimator is
  more accurate; old median was noise-peak-inflated. SNR>5 detections 94/95.
  Deep run at ≥20 mJy (`verify_sault_deep.json`, CSV format despite name):
  311/608 detected; 100% at ≥100 mJy; dimmest detection MS_1555381 at 20.1 mJy
  SNR 5.3 (NVSS-proper: NVSS_1011389, 20.2 mJy). Detection-fraction-by-flux
  table + miss attribution: 63% of misses below 5× local rms, only 16%
  (49/297) pathology-attributable. Overlay renders:
  `sault_nvss_overlay.png`, `sault_dimmest_zoom.png`.

**Verify-gate:** oracle + cross-check records exist for both files (sha
`049a8d56a8fe`, `1476145c498e`, `5f0918e1b71c`).

**Remaining before PR (decided with user):**
1. Write the accumulated weight plane out as a companion product
   (e.g. `*_mosaic_weight.fits`) so photometry/source-finding can threshold on
   effective local noise `1/sqrt(sum_weight)` — this is the correct handling
   of the surviving single-coverage edge artifacts. Wire through
   `build_epoch_coadd` → `scripts/batch_pipeline.py:1058,1887` (mosaic write
   site) without breaking the `(arr, wcs)` return contract used by
   `tests/test_mosaic_production_coadd.py:98`.
2. Note in the PR body: reprojection cost doubled (two planes; epoch rebuild
   ~1.7 h per epoch on h17 vs ~half before) — acceptable now, optimization
   follow-up (single coordinate-transform for both planes).
3. Push branch (`git push -u origin agent/sault-weighted-coadd` — push-gate
   sticky window may need a human Allow), PR to `dsa110/dsa110-continuum` with
   `--head jakobtfaber:agent/sault-weighted-coadd`, before/after renders +
   NVSS tables in body, then `ai-research-workflows:validating-implementations`.

## Work item B — crosshatch forensics (DONE, fixes NOT implemented)

Full memo: `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/DIAGNOSIS.md`
on h17 (+ figures, `qa_metric_all.json`). Headlines:
- Crosshatch is **systemic** (every tile, both epochs, same spatial-frequency
  lattice) and **visibility-domain** (fringe power identical in
  dirty/image/residual; PSF itself carries the lattice). Deeper CLEAN is
  explicitly useless (~4% effect on worst tile).
- **Root cause 1 (pattern):** bandpass/gain solve flags the identical
  34-antenna list in both epochs (30%); ~11 of those have normal raw
  amplitudes — solver-yield problem (refant/minsnr=5.0,
  `dsa110_continuum/calibration/runner.py:1206-1216`;
  `bandpass_diagnostics.py:656,784` names the "bad_antenna_or_refant" mode,
  suggests alt refant 104/105). Depleted array → PSF sidelobe lattice at
  |uv| ≈ 1.0–1.35 kλ (215–290 m, at the >1 kλ cut).
- **Root cause 2 (amplitude):** RFI flagging NEVER runs in the production
  batch path — base MS FLAG fraction 0.0000, no AOFlagger/MAD flag versions;
  `scripts/batch_pipeline.py` never calls `flag_rfi_aoflagger` /
  `flag_residual_rfi_clip` / `flag_extend`
  (`dsa110_continuum/calibration/flagging_rfi.py:214,434`,
  `flagging_amplitude.py:111`, orchestration `flagging_rfi.py:89-172`).
  Two-stage flagging contract (docs/reference/flagging.md) is un-invoked.
- **Prototyped QA metric** (not committed): fringe-annulus anisotropy
  (herringbone ≫1000 vs ~5 white noise) + central rms; suggested post-fix
  gate rms > 8 mJy OR anisotropy > 5000.
- Caveat: imaged `_meridian.ms` files were deleted; evidence is from base MS +
  cal tables (strong but inferential).

## Next steps in priority order

1. **Finish work item A** (weight-map product → push → PR → validate). Small.
2. **New work item (needs user go-ahead already implicitly given as "next"):
   wire two-stage RFI flagging into the production path** — biggest image
   quality win. Touches batch_pipeline/calibration orchestration; changes
   runtime and products materially.
3. **Cal-yield fix** — recover the ~11 wrongly-dropped antennas (alt refant
   104/105; minsnr sensitivity), re-solve on 2026-01-25, re-image one epoch to
   confirm lattice amplitude drops.
4. **Herringbone QA gate** — productionize the forensics metric into the
   per-tile QA hooks so bad tiles are quarantined pre-coadd.
5. Earlier semi-automation gap list (still open): new-namespace
   conversion/indexing entry point (`dsa110 convert`/`index add` live only in
   the old install), cal ladder proof on a non-golden date, catalog
   completeness QA fix. See conversation-era analysis; no spec doc exists yet.

## Known-broken / unverified

- Branch `agent/sault-weighted-coadd` is **unpushed**; no CI run yet. Mac full
  suite NOT re-run on this branch (only the 122-test mosaic/batch subset +
  57 mosaic tests). The 8 known pre-existing Mac failures baseline is
  documented in validation-contimg-import-retirement.md.
- Epoch-2 Sault renders (`2026-02-26T0000_sault_full/zoom.png` on h17) were
  generated but never vision-reviewed.
- Edge dipole artifact persists by design until the weight-map product lands.
- Crosshatch persists in all products; both epochs remain unusable for
  compact-source variability per the forensics QA metric until flagging + cal
  fixes land.
- The `verify_*.json` outputs are CSV content despite the extension.
- `/tmp/production_sault.py`, `/tmp/sault-validate.log` on h17 are scratch;
  the outputs dirs are the durable copies.

## Critical files to read first

1. `dsa110_continuum/mosaic/production.py` (the rewritten coadd)
2. `tests/test_mosaic_sault_coadd.py`
3. h17: `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/DIAGNOSIS.md`

## Environment / gotchas (hard-won this session)

- H17: ssh alias `h17`, user `ubuntu` (do NOT override HOME); python
  `/opt/miniforge/envs/casa6/bin/python`; repo `/data/dsa110-continuum` on
  `main` @ `57821b5`; reproject 0.19 available. Filter ssh noise with
  `grep -av "WARNING\|post-quantum\|openssh"` (note `-a`: logs contain
  binary-ish bytes; plain grep goes silent).
- Python-over-ssh: stdout is block-buffered under nohup redirect — prints
  appear only at exit; use `strings` on the log. Single quotes INSIDE
  ssh-single-quoted heredocs get shell-mangled — use double-quoted dict keys
  (py3.12 allows same-quote nesting in f-strings).
- `ls -t /data/incoming` hangs (stat storm; ~40k files) — use name-sorted.
- push-gate: `gate:main` pushes (any remote's main) block until a human
  clicks Allow (sticky window ~8h); feature-branch pushes and `gh pr merge`
  passed this session. Repo convention: merge commits via cross-repo PRs
  (`--head jakobtfaber:<branch>` to `dsa110/dsa110-continuum`).
- Commit trailers required: `Co-Authored-By: Claude Fable 5
  <noreply@anthropic.com>` + `Claude-Session:
  https://claude.ai/code/session_018Tsfo7PUmu4LFRv6stySfV` (new session ⇒ new
  session URL).
- Show images to the user via `open <png>` (macOS Preview), after scp to the
  session scratchpad.
- Domain numbers used repeatedly: Dec strip 16.13°, drift 1.20°/5-min tile,
  tile footprint ~4.0° (4800 px @ 3″ tiles; mosaic grid 6″/px), PB FWHM ≈
  3.2°, ~3-tile sensitivity overlap (N±1 ≈ 46% weight, N±2 ≈ 4%), tiles 1&4
  footprints still touch (3Δ=3.61° < 4.0°), 5th does not.

## Recommended next skill

Finish item A inline (small), then `ai-research-workflows:validating-implementations`
for the branch. For items 2–3 (flagging + cal yield), start with
`ai-research-workflows:planning-implementations` — they change production
behavior and deserve a phased plan grounded in
`docs/reference/flagging.md` + the DIAGNOSIS.md evidence.

## References

- [validation-contimg-import-retirement.md](validation-contimg-import-retirement.md)
- [implement-contimg-import-retirement.md](implement-contimg-import-retirement.md)
- [handoff-2026-07-03-03-16-caskade-simulation-control.md](handoff-2026-07-03-03-16-caskade-simulation-control.md) (prior session)
- h17: `/data/dsa110-continuum/outputs/mosaic-visual-qa-2026-07-03/`,
  `/data/dsa110-continuum/outputs/fringe-forensics-2026-07-04/`
