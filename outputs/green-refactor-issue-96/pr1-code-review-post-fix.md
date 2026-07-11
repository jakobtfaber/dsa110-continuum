No actionable correctness findings.

Verified the requested fix: [production.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/dsa110_continuum/mosaic/production.py:411) now derives the science footprint from finite mosaic pixels and rejects any nonzero weight outside it at line 415. It also rejects nonpositive weights inside the footprint at line 419.

The regression test is correct: [test_mosaic_sault_coadd.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/tests/test_mosaic_sault_coadd.py:283) creates a mosaic with `mosaic[0, 0] = np.nan`, writes an all-ones weight map, and asserts `weight_map_is_valid(...)` is false. That directly covers positive weight outside the science footprint.

I also reviewed the changed batch/archive path. Existing mosaics with missing or invalid weight companions are forced to rebuild at [batch_pipeline.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/scripts/batch_pipeline.py:1944), fresh weight maps are written with the mosaic at line 1990, and archive now copies the validated mosaic/weight pair together at [batch_pipeline.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/scripts/batch_pipeline.py:2117).

Per instruction, I did not modify files or run tests.