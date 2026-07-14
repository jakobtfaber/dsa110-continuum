**Findings**

1. [scripts/batch_pipeline.py:139-142](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/scripts/batch_pipeline.py:139): `_archive_epoch_products()` validates the stage pair, then copies mosaic first and weight second. If the weight copy fails after the mosaic copy succeeds, the products directory can be left with a new mosaic paired with an old/missing weight map. That reintroduces the archive-pair mismatch under failure conditions. Use temp paths plus atomic rename, or otherwise make the destination pair update all-or-nothing; add a test that forces the second `copy2` to fail.

2. [dsa110_continuum/mosaic/production.py:420](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/dsa110_continuum/mosaic/production.py:420): `weight_map_is_valid()` only compares `crpix`, `crval`, and `cdelt`, so a weight map with the same shape/scale but different projection, axis type, rotation/PC/CD matrix, or units can pass as “grid-aligned.” I verified a `RA---TAN/DEC--TAN` weight map validates against a `RA---SIN/DEC--SIN` mosaic. Compare the full celestial WCS contract needed for pixel alignment, and add a regression test for mismatched `CTYPE`.

Prior findings: the success-path archive-pair mismatch is addressed by validating/copying both products together, but the partial-copy failure case remains. The unusable-weight validation finding is resolved for missing/corrupt/misaligned-by-CRVAL companions and zero/negative/NaN weights on science pixels.

Focused tests passed: `66 passed, 3 warnings` for the touched mosaic/provenance/report/package-health tests.