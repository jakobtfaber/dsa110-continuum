**Findings**

Medium: `weight_map_is_valid()` can accept a mismatched companion weight map with positive weights outside the mosaic science footprint. It only enforces `weight > 0` where the mosaic is finite, but does not enforce zero/non-positive weight where the mosaic is `NaN`. Since `_archive_epoch_products()` relies on this validator before archiving, a same-shape/same-WCS but wrong-footprint weight map could pass and be archived as a valid pair. Evidence: [production.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/dsa110_continuum/mosaic/production.py:410), [batch_pipeline.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/scripts/batch_pipeline.py:140). Add the reverse-footprint check and a regression test.

Low: [tests/test_package_health.py](/data/dsa110-continuum-worktrees/agent-sault-weighted-coadd/tests/test_package_health.py:1) is untracked in `git status`, so the new dependency-contract test can be accidentally omitted from the final commit/PR even though it exists in the working tree.

I did not modify files or run tests, per request.